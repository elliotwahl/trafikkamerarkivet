#!/usr/bin/env python3
"""Ett svep mot bufferten. Ingenting rör lokal disk mer än flyktigt.

Körs var 15:e minut på en maskin som inte finns kvar efteråt, så allt tillstånd
lever i R2: vad vi redan hämtat, när senaste svepet gick, hur mycket som ligger
och väntar på komprimering.

Rutorna buntas till en tar per svep i stället för att laddas upp en och en.
Det är skillnaden mellan 96 skrivningar om dygnet och 75 000 — det senare hade
ätit R2:s gratisnivå på en vecka.
"""

import hashlib
import io
import json
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import collect
import larm
import r2
import trv

STATE = "status/state.json"
HJARTSLAG = "status/senaste-svep.json"
KAMEROR = "status/kameror.json"
RA = "ra"

# Larma om bufferten börjar närma sig R2:s gratisnivå på 10 GB.
BUFFERT_VARNING_GB = 6.0


def las_state():
    rå = r2.las(STATE)
    if not rå:
        return {}
    try:
        return json.loads(rå)
    except json.JSONDecodeError:
        print("  state.json gick inte att läsa, börjar om från tom")
        return {}


def uppdatera_kameralista(nu):
    """Full metadata en gång per dygn. Det är den som gör arkivet läsbart
    om tio år — utan den är en kamera bara ett id."""
    rå = r2.las(KAMEROR)
    if rå:
        try:
            gammal = json.loads(rå)
            hämtad = datetime.fromisoformat(gammal["hämtad"])
            if (nu - hämtad).total_seconds() < 20 * 3600:
                return gammal["kameror"]
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    kameror = trv.kameror(latt=False)
    r2.skriv(KAMEROR, json.dumps(
        {"hämtad": nu.isoformat(), "kameror": kameror}, ensure_ascii=False
    ), "application/json")
    print(f"  kameralistan uppdaterad: {len(kameror)} kameror")
    return kameror


def svep():
    t0 = time.time()
    nu = datetime.now(timezone.utc)

    try:
        uppdatera_kameralista(nu)
    except Exception as e:  # noqa: BLE001 — metadata är trevligt, bilder är viktigt
        print(f"  kameralistan kunde inte uppdateras: {e}")

    kameror = trv.kameror(latt=True)
    tidigare = las_state()
    nya = [k for k in kameror
           if k.get("PhotoTime") and k.get("PhotoUrl")
           and tidigare.get(k["Id"], {}).get("t") != k["PhotoTime"]]
    if config.BEGRANSA:
        nya = nya[: config.BEGRANSA]

    bunt = io.BytesIO()
    sparade = dubbletter = fel = 0
    rader = []

    with tarfile.open(fileobj=bunt, mode="w") as tar:
        with ThreadPoolExecutor(config.PARALLELLA) as pool:
            for kam, data, felmed in pool.map(collect.hamta_en, nya):
                if felmed:
                    fel += 1
                    continue
                sha = hashlib.sha256(data).hexdigest()
                # Kameror som räknar upp tiden men skickar identisk bild
                # (frusen bild, "no signal") ska inte sparas två gånger.
                if tidigare.get(kam["Id"], {}).get("sha") == sha:
                    dubbletter += 1
                    tidigare[kam["Id"]] = {"t": kam["PhotoTime"], "sha": sha}
                    continue

                ts = collect.parsa_tid(kam["PhotoTime"])
                namn = f"{kam['Id']}/{ts:%Y%m%dT%H%M%S}Z.jpg"
                info = tarfile.TarInfo(namn)
                info.size = len(data)
                info.mtime = int(ts.timestamp())
                tar.addfile(info, io.BytesIO(data))
                rader.append({"kamera": kam["Id"], "t": f"{ts:%Y-%m-%dT%H:%M:%S}Z",
                              "fil": namn, "b": len(data), "sha256": sha})
                tidigare[kam["Id"]] = {"t": kam["PhotoTime"], "sha": sha}
                sparade += 1

    nyckel = f"{RA}/{nu:%Y-%m-%d}/{nu:%H%M%S}.tar"
    storlek = r2.skriv(nyckel, bunt.getvalue(), "application/x-tar")
    r2.skriv(f"{RA}/{nu:%Y-%m-%d}/{nu:%H%M%S}.json",
             json.dumps(rader, ensure_ascii=False), "application/json")
    r2.skriv(STATE, json.dumps(tidigare, ensure_ascii=False), "application/json")

    antal, byte = r2.anvandning(f"{RA}/")
    sekunder = time.time() - t0
    hjartslag = {
        "ts": nu.isoformat(), "kameror": len(kameror), "nya": len(nya),
        "sparade": sparade, "dubbletter": dubbletter, "fel": fel,
        "mb": round(storlek / 1e6, 1), "sekunder": round(sekunder, 1),
        "buffert_objekt": antal, "buffert_gb": round(byte / 1e9, 2),
    }
    r2.skriv(HJARTSLAG, json.dumps(hjartslag, ensure_ascii=False), "application/json")

    print(f"{nu:%H:%M:%S}  {len(kameror)} kameror, {sparade} sparade, "
          f"{dubbletter} dubbletter, {fel} fel, {storlek/1e6:.0f} MB på "
          f"{sekunder:.0f} s (buffert {byte/1e9:.2f} GB i {antal} objekt)")

    # Bufferten ska tömmas var sjätte timme. Växer den ändå har komprimeringen
    # slutat fungera, och då är det bråttom innan gratisnivån tar slut.
    if byte / 1e9 > BUFFERT_VARNING_GB:
        larm.skicka(
            f"⚠️ Bufferten är uppe i {byte/1e9:.1f} GB i {antal} objekt. "
            f"R2:s gratisnivå är 10 GB — komprimeringen har troligen slutat köra."
        )
    if fel > len(nya) * 0.2 and len(nya) > 20:
        larm.skicka(f"⚠️ {fel} av {len(nya)} kameror gick inte att hämta i svepet.")

    # Hjärtslaget går ut sist, när svepet faktiskt lyckats. Uteblir det larmar
    # dödmansknappen — det är det enda som kan upptäcka att insamlingen slutat
    # köra helt, eftersom ett jobb som inte startar inte kan larma om sig självt.
    larm.hjartslag(data=f"{sparade} sparade, {fel} fel, buffert {byte/1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(svep())
    except Exception as e:  # noqa: BLE001 — larma innan jobbet dör
        larm.skicka(f"🔴 Svepet kraschade: {type(e).__name__}: {e}")
        larm.hjartslag("/fail", f"{type(e).__name__}: {e}")
        raise
