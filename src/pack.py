#!/usr/bin/env python3
"""Packar bufferten till dygnsitems på archive.org.

Kör var sjätte timme. Tar varje *avslutad* sextimmarsperiod som fortfarande
ligger kvar i bufferten, komprimerar den per kamera till AV1 och laddar upp.

Att den letar efter allt som ligger kvar, i stället för att räkna ut vilken
period som är "nästa", gör den självläkande: en missad körning tas igen av sig
själv, och en halvfärdig körning gör bara om det som inte hann bli klart.

Bufferten töms först när archive.org självt räknar upp filerna i sitt
metadata-API. Går verifieringen inte att genomföra ligger råmaterialet kvar.
"""

import collections
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compact
import config
import ia
import larm
import r2

RA = "ra"
STATUS = "status/senaste-packning.json"
PERIODER = (0, 6, 12, 18)


def perioder_i_bufferten():
    """{(dygn, period): [nycklar]} för allt råmaterial som ligger kvar."""
    grupper = collections.defaultdict(list)
    for nyckel in r2.lista(f"{RA}/"):
        delar = nyckel.split("/")
        if len(delar) != 3 or not delar[2].endswith(".tar"):
            continue
        dygn, fil = delar[1], delar[2]
        try:
            timme = int(fil[:2])
        except ValueError:
            continue
        period = max(p for p in PERIODER if p <= timme)
        grupper[(dygn, period)].append(nyckel)
    return grupper


def avslutad(dygn, period, nu):
    """En period är klar när dess sista kvart har passerat."""
    start = datetime.strptime(dygn, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=period)
    return nu >= start + timedelta(hours=6)


def packa_upp(nycklar, katalog):
    """Hämtar tar-filerna ur bufferten och packar upp dem. {kamera: [rutor]}."""
    rutor = collections.defaultdict(list)
    for nyckel in sorted(nycklar):
        rå = r2.las(nyckel)
        if not rå:
            print(f"  ! {nyckel} gick inte att läsa, hoppar över")
            continue
        with tarfile.open(fileobj=io.BytesIO(rå), mode="r") as tar:
            tar.extractall(katalog, filter="data")
        # Registret bredvid taren har tidsstämplar och kontrollsummor
        reg = r2.las(nyckel.replace(".tar", ".json"))
        if reg:
            for r in json.loads(reg):
                rutor[r["kamera"]].append(r)
    for kamera in rutor:
        rutor[kamera].sort(key=lambda r: r["t"])
    return rutor


def koda(katalog, kamera, rader, ut):
    """Rutorna för en kamera till en AV1-video. Returnerar antal rutor."""
    lista = katalog / f".{kamera}.txt"
    filer = [katalog / r["fil"] for r in rader]
    filer = [f for f in filer if f.exists()]
    if len(filer) < 2:
        return 0
    lista.write_text("".join(f"file '{f}'\nduration 1\n" for f in filer), encoding="utf-8")
    encoder, crf_standard, extra = compact.KODEKAR[config.KODEK]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lista),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:v", encoder, "-crf", config.CRF or crf_standard,
        "-pix_fmt", "yuv420p", "-r", "1", "-movflags", "+faststart",
        *extra, str(ut),
    ]
    kord = subprocess.run(cmd, capture_output=True, text=True)
    lista.unlink(missing_ok=True)
    if kord.returncode != 0:
        print(f"  ! {kamera}: ffmpeg: {kord.stderr.strip()[:200]}")
        return 0
    # Lita aldrig på att videon blev rätt — räkna rutorna i den.
    if compact.ffprobe_antal(ut) != len(filer):
        print(f"  ! {kamera}: fel antal rutor i videon, hoppar över")
        ut.unlink(missing_ok=True)
        return 0
    return len(filer)


def packa_period(dygn, period, nycklar, kameraregister, nu):
    print(f"\n{dygn} kl {period:02d}–{period+6:02d}: {len(nycklar)} svep i bufferten")
    arbete = Path(tempfile.mkdtemp(prefix="tkark-"))
    try:
        rutor = packa_upp(nycklar, arbete)
        if not rutor:
            print("  inget råmaterial, hoppar över")
            return False

        redan = ia.filer_i_item(dygn) or set()
        skapa_item = not redan
        antal_kameror = 0
        antal_rutor = 0
        bytes_upp = 0
        index = {}

        for kamera, rader in sorted(rutor.items()):
            filnamn = f"{kamera}-{period:02d}.mp4"
            if filnamn in redan:
                continue
            ut = arbete / filnamn
            n = koda(arbete, kamera, rader, ut)
            if not n:
                continue
            try:
                _, storlek = ia.ladda_upp(dygn, filnamn, ut, skapa_item=skapa_item,
                                          antal_kameror=len(rutor))
                skapa_item = False
            except Exception as e:  # noqa: BLE001
                print(f"  ! {filnamn}: {e}")
                continue
            meta = kameraregister.get(kamera, {})
            index[kamera] = {
                "namn": meta.get("Name"),
                "beskrivning": meta.get("Description"),
                "lan": meta.get("CountyNo"),
                "riktning": meta.get("Direction"),
                "wgs84": (meta.get("Geometry") or {}).get("WGS84"),
                "video": filnamn,
                "rutor": [{"i": i, "t": r["t"], "b": r["b"], "sha256": r["sha256"]}
                          for i, r in enumerate(rader) if (arbete / r["fil"]).exists()],
            }
            antal_kameror += 1
            antal_rutor += n
            bytes_upp += storlek
            ut.unlink(missing_ok=True)

        if index:
            ia.ladda_upp(dygn, f"index-{period:02d}.json",
                         json.dumps({"dygn": dygn, "period": period,
                                     "källa": "Trafikverkets öppna API (CC0)",
                                     "kameror": index}, ensure_ascii=False).encode("utf-8"))

        # Verifiera mot archive.org innan bufferten töms.
        uppe = ia.filer_i_item(dygn)
        forvantat = {f"{k}-{period:02d}.mp4" for k in index} | {f"index-{period:02d}.json"}
        if uppe is None or not forvantat <= uppe:
            saknas = len(forvantat - (uppe or set()))
            print(f"  ! {saknas} filer kunde inte verifieras hos archive.org — "
                  f"behåller bufferten, nästa körning gör om")
            return False

        for nyckel in nycklar:
            r2.radera(nyckel)
            r2.radera(nyckel.replace(".tar", ".json"))

        print(f"  {antal_kameror} kameror, {antal_rutor} rutor, "
              f"{bytes_upp/1e6:.0f} MB till archive.org, buffert tömd")
        return True
    finally:
        shutil.rmtree(arbete, ignore_errors=True)


def main(argv):
    nu = datetime.now(timezone.utc)
    tvinga = "--tvinga" in argv  # packa även perioder som inte hunnit ta slut

    rå = r2.las("status/kameror.json")
    kameraregister = {}
    if rå:
        kameraregister = {k["Id"]: k for k in json.loads(rå)["kameror"]}

    grupper = perioder_i_bufferten()
    att_gora = sorted(g for g in grupper if tvinga or avslutad(*g, nu))
    if not att_gora:
        print("inget avslutat att packa")
        return 0

    print(f"{len(att_gora)} period(er) att packa")
    klara = misslyckade = 0
    for dygn, period in att_gora:
        try:
            if packa_period(dygn, period, grupper[(dygn, period)], kameraregister, nu):
                klara += 1
            else:
                misslyckade += 1
        except Exception as e:  # noqa: BLE001 — en period ska inte fälla resten
            print(f"  ! {dygn} kl {period:02d}: {type(e).__name__}: {e}")
            misslyckade += 1
        time.sleep(1)

    r2.skriv(STATUS, json.dumps({
        "ts": nu.isoformat(), "klara": klara, "misslyckade": misslyckade,
    }, ensure_ascii=False), "application/json")

    if misslyckade:
        larm.skicka(f"⚠️ {misslyckade} period(er) kunde inte packas. "
                    f"Råmaterialet ligger kvar i bufferten.")
    return 1 if misslyckade and not klara else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001
        larm.skicka(f"🔴 Packningen kraschade: {type(e).__name__}: {e}")
        raise
