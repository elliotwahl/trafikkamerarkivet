#!/usr/bin/env python3
"""Laddar upp komprimerade dygn till archive.org och rensar dem lokalt.

Regeln som aldrig får kompromissas med: **radera aldrig före verifiering.**
Filen tas bort lokalt först när en HEAD mot dess publika URL på archive.org
svarar 200. Misslyckas något ligger materialet kvar och nästa körning gör om
försöket.

    python3 src/upload.py                 # laddar upp allt komprimerat
    python3 src/upload.py --rensa         # ...och raderar lokalt efteråt
    python3 src/upload.py --en            # bara ett dygn, för att prova
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import ia

OGILTIGT = str.maketrans({c: "_" for c in '/\\:*?"<>| '})


def kameraregister():
    """Kamera-id -> metadata, från den lista svepet sparar."""
    if not config.KAMERA_JSON.exists():
        raise RuntimeError(
            f"{config.KAMERA_JSON} saknas — kör src/collect.py en gång först"
        )
    kameror = json.loads(config.KAMERA_JSON.read_text(encoding="utf-8"))["kameror"]
    return {k["Id"].translate(OGILTIGT): k for k in kameror}


def fardiga_dygn():
    """Varje katalog som har en komprimerad video och ett rutregister."""
    for rutor in sorted(config.ARKIV.glob("*/[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/rutor.json")):
        katalog = rutor.parent
        video = katalog / "dygn.mp4"
        if video.exists():
            yield katalog, video, rutor


def main(argv):
    rensa = "--rensa" in argv
    bara_en = "--en" in argv
    register = kameraregister()

    upp = hoppade = fel = 0
    bytes_upp = 0
    # Ett metadata-anrop per item räcker för att veta vad som redan finns.
    kant = {}
    for katalog, video, rutor in fardiga_dygn():
        kameramapp = katalog.parents[2].name
        ar = katalog.parents[1].name
        dygn = f"{ar}-{katalog.parent.name}-{katalog.name}"
        kamera = register.get(kameramapp)
        if not kamera:
            print(f"  ! {kameramapp}: finns inte i kameralistan, hoppar över")
            fel += 1
            continue

        nyckel = (kamera["Id"], ar)
        if nyckel not in kant:
            kant[nyckel] = ia.filer_i_item(*nyckel)
        redan = kant[nyckel] or set()
        nytt_item = not redan

        klart = True
        for lokal, filnamn in ((video, f"{dygn}.mp4"), (rutor, f"{dygn}.json")):
            if filnamn in redan:
                hoppade += 1
                continue
            try:
                url, n = ia.ladda_upp(kamera, ar, filnamn, lokal, forsta_gangen=nytt_item)
                nytt_item = False
                bytes_upp += n
                upp += 1
                print(f"  {filnamn:<20} {n/1024:>7.0f} KB  {url}")
            except Exception as e:  # noqa: BLE001 — ett fel ska inte stoppa resten
                print(f"  ! {filnamn}: {e}")
                fel += 1
                klart = False

        # Radera aldrig utifrån antagandet att uppladdningen gick bra. Först
        # när archive.org självt räknar upp filerna får de tas bort lokalt.
        if klart and rensa:
            uppe = ia.filer_i_item(*nyckel)
            if uppe and {f"{dygn}.mp4", f"{dygn}.json"} <= uppe:
                video.unlink(missing_ok=True)
                rutor.unlink(missing_ok=True)
            else:
                print(f"  ! {dygn} för {kameramapp}: inte verifierat hos IA, behåller lokalt")
                fel += 1

        if bara_en:
            break
        time.sleep(0.2)  # var snäll mot IA:s kö

    print(f"\n{upp} filer uppladdade ({bytes_upp/1e6:.1f} MB), "
          f"{hoppade} fanns redan, {fel} fel")
    return 1 if fel else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
