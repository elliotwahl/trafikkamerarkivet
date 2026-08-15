#!/usr/bin/env python3
"""Ett svep: fråga Trafikverket vilka kameror som har en ny bild, hämta dem.

Körs var 5:e minut. Kamerorna själva uppdateras ungefär en gång i minuten,
så ett svep hämtar alltid färskaste bilden — aldrig samma två gånger, för
PhotoTime i API-svaret jämförs mot vad vi redan har.
"""

import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import state
import trv

OGILTIGT = str.maketrans({c: "_" for c in '/\\:*?"<>| '})


def kamera_katalog(kamera_id, ts):
    """arkiv/<kamera>/<år>/<månad>/<dag>/ — dagsindelat i UTC så att en
    dygnsvideo alltid betyder samma sak oavsett sommartid."""
    trygg = kamera_id.translate(OGILTIGT)
    return config.ARKIV / trygg / f"{ts:%Y}" / f"{ts:%m}" / f"{ts:%d}"


def parsa_tid(s):
    """Trafikverkets PhotoTime, t.ex. 2026-08-15T21:08:02.000+02:00 -> UTC."""
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def ledigt_gb(sokvag):
    sokvag.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(sokvag).free / 1024**3


def hamta_en(kam):
    """Laddar ner en kameras bild. Returnerar (kam, bytes, fel)."""
    for forsok in range(config.FORSOK):
        try:
            data, _ = trv.hamta_bild(kam["PhotoUrl"])
            if len(data) < 1024:
                return kam, None, f"misstänkt liten bild ({len(data)} B)"
            return kam, data, None
        except Exception as e:  # noqa: BLE001 — nätfel ska aldrig fälla svepet
            if forsok == config.FORSOK - 1:
                return kam, None, f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (forsok + 1))
    return kam, None, "okänt fel"


def uppdatera_metadata():
    """Full kameralista en gång per dygn: namn, beskrivning, koordinater.
    Det är den som gör arkivet läsbart om fem år."""
    try:
        färsk = config.KAMERA_JSON.exists() and (
            time.time() - config.KAMERA_JSON.stat().st_mtime < 20 * 3600
        )
        if färsk:
            return
        kameror = trv.kameror(latt=False)
        config.KAMERA_JSON.parent.mkdir(parents=True, exist_ok=True)
        config.KAMERA_JSON.write_text(
            json.dumps(
                {"hämtad": datetime.now(timezone.utc).isoformat(), "kameror": kameror},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"  metadata uppdaterad: {len(kameror)} kameror")
    except Exception as e:  # noqa: BLE001 — metadata är trevligt, bilder är viktigt
        print(f"  metadata kunde inte uppdateras: {e}")


def svep():
    t0 = time.time()
    nu = datetime.now(timezone.utc)
    config.LOGG_DIR.mkdir(parents=True, exist_ok=True)

    ledigt = ledigt_gb(config.ARKIV)
    if ledigt < config.MIN_LEDIGT_GB:
        print(f"AVBRYTER: bara {ledigt:.1f} GB ledigt (gränsen är {config.MIN_LEDIGT_GB} GB). "
              f"Kör compact.py eller peka ARKIV_DIR någon annanstans.")
        return 1

    db = state.anslut()
    uppdatera_metadata()

    try:
        kameror = trv.kameror(latt=True)
    except Exception as e:  # noqa: BLE001 — nästa svep om fem minuter får försöka igen
        print(f"AVBRYTER: kameralistan gick inte att hämta: {e}")
        db.close()
        return 1
    tidigare = state.kant(db)
    nya = [k for k in kameror if tidigare.get(k["Id"], ("",))[0] != k.get("PhotoTime")]
    nya = [k for k in nya if k.get("PhotoTime") and k.get("PhotoUrl")]
    if config.BEGRANSA:
        nya = nya[: config.BEGRANSA]

    sparade = dubbletter = fel = 0
    bytes_ner = 0
    rader = {}

    with ThreadPoolExecutor(config.PARALLELLA) as pool:
        for kam, data, felmed in pool.map(hamta_en, nya):
            if felmed:
                fel += 1
                print(f"  ! {kam['Id']}: {felmed}")
                continue
            bytes_ner += len(data)
            sha = hashlib.sha256(data).hexdigest()

            # Vissa kameror räknar upp PhotoTime men skickar identisk bild
            # (frusen bild, "no signal"). Spara inte samma byte två gånger.
            if tidigare.get(kam["Id"], (None, None))[1] == sha:
                dubbletter += 1
                state.notera(db, kam["Id"], kam["PhotoTime"], sha, None, nu.isoformat())
                continue

            ts = parsa_tid(kam["PhotoTime"])
            katalog = kamera_katalog(kam["Id"], ts)
            katalog.mkdir(parents=True, exist_ok=True)
            namn = f"{ts:%Y%m%dT%H%M%S}Z.jpg"
            (katalog / namn).write_bytes(data)
            rader.setdefault(katalog, []).append(
                {"t": f"{ts:%Y-%m-%dT%H:%M:%S}Z", "f": namn, "b": len(data), "sha256": sha}
            )
            state.notera(db, kam["Id"], kam["PhotoTime"], sha, namn, nu.isoformat())
            sparade += 1

    # index.jsonl per kamera och dygn — viewern behöver aldrig lista katalogen
    for katalog, rs in rader.items():
        with (katalog / "index.jsonl").open("a", encoding="utf-8") as f:
            for r in sorted(rs, key=lambda x: x["t"]):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    db.commit()
    sekunder = time.time() - t0
    db.execute(
        "INSERT OR REPLACE INTO svep VALUES (?,?,?,?,?,?,?,?)",
        (nu.isoformat(), len(kameror), len(nya), sparade, dubbletter, fel,
         bytes_ner, round(sekunder, 1)),
    )
    db.commit()
    db.close()

    rad = {
        "ts": nu.isoformat(), "kameror": len(kameror), "nya": len(nya),
        "sparade": sparade, "dubbletter": dubbletter, "fel": fel,
        "mb": round(bytes_ner / 1e6, 1), "sekunder": round(sekunder, 1),
        "ledigt_gb": round(ledigt_gb(config.ARKIV), 1),
    }
    with (config.LOGG_DIR / f"{nu:%Y-%m}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rad, ensure_ascii=False) + "\n")

    print(f"{nu:%H:%M:%S}  {len(kameror)} kameror, {len(nya)} nya, {sparade} sparade, "
          f"{dubbletter} dubbletter, {fel} fel, {rad['mb']} MB på {sekunder:.0f} s "
          f"({rad['ledigt_gb']} GB ledigt)")
    return 0


if __name__ == "__main__":
    sys.exit(svep())
