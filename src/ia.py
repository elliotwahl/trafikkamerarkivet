"""Uppladdning till Internet Archive via deras S3-liknande API.

**Ett item per dygn**, med alla kameror i: trafikkamerarkivet-YYYY-MM-DD.

Strukturen är vald av en enda anledning: archive.org begränsar hur snabbt ett
konto får *skapa* items, inte hur många filer man lägger i dem. Ett item per
kamera hade betytt 785 nya items på en eftermiddag, vilket deras spamskydd
stoppar direkt. Ett item per dygn betyder ett nytt item om dygnet, och filerna
inuti kan vara hur många som helst (deras rekommendation är under 10 000 —
vi landar på ~3 100).

Filerna i ett dygnsitem:

    SE_STA_CAMERA_0_1075001058-00.mp4   sex timmars rutor, en video
    SE_STA_CAMERA_0_1075001058-06.mp4
    ...
    index-00.json                        alla kameror, alla tidsstämplar

En viewer kan räkna ut URL:en till vilket dygn och vilken kamera som helst
utan att slå i något register:

    https://archive.org/download/trafikkamerarkivet-2026-08-15/SE_STA_CAMERA_0_1075001058-06.mp4

Nycklarna hämtas på archive.org/account/s3.php och läses ur miljön.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config

S3 = "https://s3.us.archive.org"
METADATA = "https://archive.org/metadata"
NEDLADDNING = "https://archive.org/download"


def item_namn(dygn):
    """dygn som 'YYYY-MM-DD' -> itemnamn."""
    return f"trafikkamerarkivet-{dygn}"


def fil_url(dygn, filnamn):
    return f"{NEDLADDNING}/{item_namn(dygn)}/{filnamn}"


def _auth():
    access = os.environ.get("IA_ACCESS", "")
    secret = os.environ.get("IA_SECRET", "")
    if not (access and secret):
        raise RuntimeError(
            "IA_ACCESS/IA_SECRET saknas. Hämta nyckelparet på "
            "archive.org/account/s3.php och lägg det i .env"
        )
    return f"LOW {access}:{secret}"


def _meta(varde):
    """archive.org svarar 500 på headers med icke-ASCII. Deras lösning är att
    skicka värdet URL-kodat inuti uri(), så gör vi det när det behövs."""
    varde = (varde or "").strip()
    return varde if varde.isascii() else "uri(" + urllib.parse.quote(varde, safe="") + ")"


def _begar(metod, url, data=None, headers=None, forsok=2, timeout=300):
    """IA svarar 503 SlowDown när kön är lång eller när kontot går för fort.

    Ett försök till efter en minut, sedan ger vi upp. Att banka vidare är
    precis det som utlöser deras spamskydd, och materialet ligger kvar i
    bufferten — nästa körning om sex timmar tar det. Ett arkiv har inte
    bråttom, men det ska heller inte göra sig ovälkommet.
    """
    for n in range(forsok):
        req = urllib.request.Request(url, data=data, method=metod)
        req.add_header("Authorization", _auth())
        req.add_header("User-Agent", config.UA)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status
        except urllib.error.HTTPError as e:
            kropp = e.read().decode("utf-8", "replace").replace("\n", " ")
            if e.code == 503 and n < forsok - 1:
                paus = 60 * (n + 1)
                print(f"  IA säger vänta, pausar {paus} s")
                time.sleep(paus)
                continue
            raise RuntimeError(f"IA {metod}: {e.code} {kropp[:240]}") from e
        except (urllib.error.URLError, TimeoutError, OSError):
            if n < forsok - 1:
                time.sleep(15 * (n + 1))
                continue
            raise
    raise RuntimeError(f"IA {metod} {url}: gav upp efter {forsok} försök")


def _meta_headers(dygn, antal_kameror):
    """Sätts när dygnets item skapas. Beskrivningen är det som gör arkivet
    begripligt för någon som hittar hit om tio år."""
    return {
        "x-archive-auto-make-bucket": "1",
        "x-archive-queue-derive": "0",
        "x-archive-meta-mediatype": "movies",
        "x-archive-meta-title": _meta(f"Trafikverkets vägkameror {dygn}"),
        "x-archive-meta-description": _meta(
            f"Ett dygns bilder från {antal_kameror} av Trafikverkets "
            f"trafikflödeskameror, {dygn}. Varje kamera har en video per "
            f"sextimmarsperiod (-00, -06, -12, -18) med en bildruta per "
            f"hämtning. Tidsstämplar, namn, vägnummer och koordinater finns i "
            f"index-filerna. Bilderna hämtades var femtonde minut från "
            f"Trafikverkets öppna API och är oförändrade utöver "
            f"videokomprimering. Licens: Creative Commons Zero."
        ),
        "x-archive-meta-licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/",
        "x-archive-meta-subject": _meta(
            "trafikkamera;trafikverket;sverige;vägtrafik;öppna data;trafikflöde"
        ),
        "x-archive-meta-language": "swe",
        "x-archive-meta-date": dygn,
        "x-archive-meta-source": "https://api.trafikinfo.trafikverket.se/",
    }


def ladda_upp(dygn, filnamn, data, skapa_item=False, antal_kameror=0):
    """PUT en fil till dygnets item. `data` är bytes eller en sökväg."""
    if isinstance(data, (str, Path)):
        data = Path(data).read_bytes()
    headers = {"Content-Length": str(len(data)), "x-archive-queue-derive": "0"}
    if skapa_item:
        headers.update(_meta_headers(dygn, antal_kameror))
    _begar("PUT", f"{S3}/{item_namn(dygn)}/{filnamn}", data=data, headers=headers)
    return fil_url(dygn, filnamn), len(data)


def filer_i_item(dygn, timeout=90):
    """Vilka filer ligger redan i dygnets item? Ett anrop ger hela listan.

    Metadata-API:t svarar på några sekunder; download-URL:en gör det inte —
    den hänger sig på filer som inte finns, vilket är precis fallet vi frågar
    om. Returnerar None när svaret inte gick att hämta, så att anropande kod
    kan skilja "tomt item" från "vet inte" — och aldrig raderar på ett
    missförstånd.
    """
    req = urllib.request.Request(f"{METADATA}/{item_namn(dygn)}")
    req.add_header("User-Agent", config.UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            svar = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return {f.get("name") for f in svar.get("files", []) if f.get("name")}


def radera(dygn, filnamn):
    _begar("DELETE", f"{S3}/{item_namn(dygn)}/{filnamn}",
           headers={"x-archive-cascade-delete": "1"}, forsok=2)
