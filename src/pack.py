#!/usr/bin/env python3
"""Komprimerar bufferten och laddar upp till archive.org — i den ordningen,
men oberoende av varandra.

Två faser som inte får blockera varandra:

    1. komprimera   ra/ (råa rutor)  ->  klart/ (färdiga videor)
    2. ladda upp    klart/           ->  archive.org

Poängen med att skilja dem åt är marginalen. Råa rutor är 9,4 GB per dygn,
komprimerade är samma dygn 1,2 GB. Om fas 2 ligger nere — archive.org strypte
oss, deras kö är lång, nätverket är trasigt — fortsätter fas 1 ändå, och
bufferten räcker då i åtta dygn i stället för ett.

Fas 1 raderar råmaterialet först när videorna ligger i bufferten.
Fas 2 raderar videorna först när archive.org självt räknar upp dem.
Ingen av dem raderar något på ett antagande.
"""

import collections
import io
import json
import shutil
import sys
import tarfile
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import ia
import larm
import r2
import video

RA = "ra"
KLART = "klart"
STATUS = "status/senaste-packning.json"
PERIODER = (0, 6, 12, 18)


# ---------------------------------------------------------------- fas 1

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
        grupper[(dygn, max(p for p in PERIODER if p <= timme))].append(nyckel)
    return grupper


def avslutad(dygn, period, nu):
    """En period är klar när dess sista kvart har passerat."""
    start = (datetime.strptime(dygn, "%Y-%m-%d").replace(tzinfo=timezone.utc)
             + timedelta(hours=period))
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
        reg = r2.las(nyckel.replace(".tar", ".json"))
        if reg:
            for r in json.loads(reg):
                rutor[r["kamera"]].append(r)
    for kamera in rutor:
        rutor[kamera].sort(key=lambda r: r["t"])
    return rutor


def koda(katalog, kamera, rader, ut):
    """Rutorna för en kamera till en video. Returnerar antal kodade rutor."""
    filer = [katalog / r["fil"] for r in rader]
    filer = [f for f in filer if f.exists()]
    ok, fel = video.koda(filer, ut, config.KODEK, config.CRF)
    if not ok:
        if fel != "färre än två rutor":
            print(f"  ! {kamera}: {fel}")
        return 0
    return len(filer)


def komprimera_period(dygn, period, nycklar, kameraregister):
    print(f"\n{dygn} kl {period:02d}–{period+6:02d}: {len(nycklar)} svep")
    arbete = Path(tempfile.mkdtemp(prefix="tkark-"))
    try:
        rutor = packa_upp(nycklar, arbete)
        if not rutor:
            print("  inget råmaterial, hoppar över")
            return False

        index = {}
        kameror = rutor_totalt = bytes_ut = 0
        for kamera, rader in sorted(rutor.items()):
            filnamn = f"{kamera}-{period:02d}.mp4"
            ut = arbete / filnamn
            n = koda(arbete, kamera, rader, ut)
            if not n:
                continue
            data = ut.read_bytes()
            r2.skriv(f"{KLART}/{dygn}/{filnamn}", data, "video/mp4")

            # Rutlistan ligger per kamera, inte samlad. En viewer som vill visa
            # en kamera ska hämta en liten fil — inte 7 MB rutor för 786 andra.
            rutor_ut = [{"i": i, "t": r["t"], "b": r["b"], "sha256": r["sha256"]}
                        for i, r in enumerate(rader) if (arbete / r["fil"]).exists()]
            r2.skriv(f"{KLART}/{dygn}/{kamera}-{period:02d}.json",
                     json.dumps({"kamera": kamera, "dygn": dygn, "period": period,
                                 "video": filnamn, "fps": 1, "rutor": rutor_ut},
                                ensure_ascii=False),
                     "application/json")

            meta = kameraregister.get(kamera, {})
            # Periodens index är bara vilka kameror som finns och var de står.
            index[kamera] = {
                "namn": meta.get("Name"),
                "beskrivning": meta.get("Description"),
                "lan": meta.get("CountyNo"),
                "riktning": meta.get("Direction"),
                "wgs84": (meta.get("Geometry") or {}).get("WGS84"),
                "video": filnamn,
                "antal_rutor": len(rutor_ut),
                "forsta": rutor_ut[0]["t"] if rutor_ut else None,
                "sista": rutor_ut[-1]["t"] if rutor_ut else None,
            }
            kameror += 1
            rutor_totalt += n
            bytes_ut += len(data)
            ut.unlink(missing_ok=True)

        if not index:
            print("  inga kameror gick att koda, behåller råmaterialet")
            return False

        r2.skriv(f"{KLART}/{dygn}/index-{period:02d}.json",
                 json.dumps({"dygn": dygn, "period": period,
                             "källa": "Trafikverkets öppna API (CC0)",
                             "kameror": index}, ensure_ascii=False),
                 "application/json")

        # Råmaterialet raderas först när videorna faktiskt ligger i bufferten.
        saknas = [k for k in index if not r2.finns(f"{KLART}/{dygn}/{k}-{period:02d}.mp4")]
        if saknas:
            print(f"  ! {len(saknas)} videor kom inte fram till bufferten, "
                  f"behåller råmaterialet")
            return False
        for nyckel in nycklar:
            r2.radera(nyckel)
            r2.radera(nyckel.replace(".tar", ".json"))

        print(f"  {kameror} kameror, {rutor_totalt} rutor -> {bytes_ut/1e6:.0f} MB "
              f"({bytes_ut/max(rutor_totalt,1)/1024:.1f} KB/ruta), råmaterialet rensat")
        return True
    finally:
        shutil.rmtree(arbete, ignore_errors=True)


# ---------------------------------------------------------------- fas 2

def ladda_upp_klart():
    """Allt som ligger färdigkomprimerat skickas till archive.org.

    Går det inte ligger det kvar och nästa körning tar det. Ett dygn
    komprimerat är 1,2 GB, så bufferten tål ungefär åtta dygns avbrott.
    """
    per_dygn = collections.defaultdict(list)
    for nyckel in r2.lista(f"{KLART}/"):
        delar = nyckel.split("/")
        if len(delar) == 3:
            per_dygn[delar[1]].append(nyckel)
    if not per_dygn:
        return 0, 0

    uppladdade = kvar = 0
    for dygn in sorted(per_dygn):
        nycklar = sorted(per_dygn[dygn])
        redan = ia.filer_i_item(dygn)
        if redan is None:
            print(f"  {dygn}: archive.org svarar inte, låter det ligga")
            kvar += len(nycklar)
            continue
        skapa_item = not redan
        print(f"\n{dygn}: {len(nycklar)} filer att ladda upp")

        for nyckel in nycklar:
            filnamn = nyckel.split("/")[-1]
            if filnamn in redan:
                r2.radera(nyckel)
                continue
            data = r2.las(nyckel)
            if data is None:
                continue
            try:
                ia.ladda_upp(dygn, filnamn, data, skapa_item=skapa_item,
                             antal_kameror=len(nycklar) // 3)
                skapa_item = False
                uppladdade += 1
            except Exception as e:  # noqa: BLE001 — nästa körning tar resten
                print(f"  ! {filnamn}: {str(e)[:140]}")
                kvar += 1
                break  # strypt eller nere — sluta banka på

        # Radera bara det archive.org självt räknar upp. Deras metadata-API
        # släpar ofta en minut efter en uppladdning, så det som just skickats
        # syns sällan direkt — då ligger det kvar och städas nästa körning.
        # Att vänta in dem här hade bara gjort jobbet långsammare.
        time.sleep(5)
        uppe = ia.filer_i_item(dygn)
        if uppe:
            for nyckel in nycklar:
                if nyckel.split("/")[-1] in uppe:
                    r2.radera(nyckel)
    return uppladdade, kvar


# ---------------------------------------------------------------- körning

def main(argv):
    nu = datetime.now(timezone.utc)

    if r2.stoppad():
        print("nödbromsen är i (status/STOPP finns i bufferten) — gör ingenting")
        return 0
    tvinga = "--tvinga" in argv       # packa även perioder som inte tagit slut
    bara_upp = "--bara-upp" in argv   # hoppa över komprimeringen

    rå = r2.las("status/kameror.json")
    kameraregister = {k["Id"]: k for k in json.loads(rå)["kameror"]} if rå else {}

    klara = misslyckade = 0
    if not bara_upp:
        grupper = perioder_i_bufferten()
        att_gora = sorted(g for g in grupper if tvinga or avslutad(*g, nu))
        print(f"fas 1: {len(att_gora)} period(er) att komprimera")
        for dygn, period in att_gora:
            try:
                if komprimera_period(dygn, period, grupper[(dygn, period)], kameraregister):
                    klara += 1
                else:
                    misslyckade += 1
            except Exception as e:  # noqa: BLE001 — en period ska inte fälla resten
                print(f"  ! {dygn} kl {period:02d}: {type(e).__name__}: {e}")
                misslyckade += 1

    print("\nfas 2: laddar upp till archive.org")
    uppladdade, kvar = ladda_upp_klart()
    ovantar = len(r2.lista(f"{KLART}/"))
    print(f"  {uppladdade} filer uppladdade, {kvar} misslyckade, "
          f"{ovantar} ligger kvar i bufferten i väntan på verifiering")

    _, byte = r2.anvandning()
    r2.skriv(STATUS, json.dumps({
        "ts": nu.isoformat(), "klara": klara, "misslyckade": misslyckade,
        "uppladdade": uppladdade, "kvar": kvar,
        "buffert_gb": round(byte / 1e9, 2),
    }, ensure_ascii=False), "application/json")

    if misslyckade:
        larm.skicka(f"⚠️ {misslyckade} period(er) kunde inte komprimeras. "
                    f"Råmaterialet ligger kvar.")
    if kvar and byte / 1e9 > config.VARNA_GB:
        larm.skicka(f"⚠️ {kvar} filer väntar på archive.org och bufferten är "
                    f"uppe i {byte/1e9:.1f} GB av gratisnivåns 10.")
    return 1 if misslyckade and not klara else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001
        larm.skicka(f"🔴 Packningen kraschade: {type(e).__name__}: {e}")
        raise
