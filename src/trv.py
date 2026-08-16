"""Klient mot Trafikverkets öppna API och mot bild-endpointen.

Två anrop räcker för hela arkivet:
  kameror(lätt=True)  -> Id + PhotoTime + PhotoUrl för alla kameror, ett anrop.
                          Det är dedup-nyckeln: vi vet vilka bilder som är nya
                          innan vi laddat ner en enda byte.
  kameror(lätt=False) -> hela metadatan (namn, beskrivning, koordinater, riktning).
"""

import gzip
import json
import time
import urllib.error
import urllib.request

import config

FALT_LATT = ["Id", "PhotoTime", "PhotoUrl", "Type"]
FALT_FULL = [
    "Id", "Name", "Description", "Type", "Active", "Status", "HasFullSizePhoto",
    "PhotoUrl", "PhotoTime", "CountyNo", "Direction", "Geometry.WGS84", "ModifiedTime",
]


def _query(falt, typer):
    typfilter = "".join(f'<EQ name="Type" value="{t}"/>' for t in typer)
    if len(typer) > 1:
        typfilter = f"<OR>{typfilter}</OR>"
    lansfilter = "".join(f'<EQ name="CountyNo" value="{n}"/>' for n in config.LAN)
    if len(config.LAN) > 1:
        lansfilter = f"<OR>{lansfilter}</OR>"
    inkludera = "".join(f"<INCLUDE>{f}</INCLUDE>" for f in falt)
    return (
        "<REQUEST>"
        f'<LOGIN authenticationkey="{config.TRV_NYCKEL}"/>'
        '<QUERY objecttype="Camera" schemaversion="1">'
        "<FILTER>"
        '<EQ name="Deleted" value="false"/>'
        '<EQ name="Active" value="true"/>'
        f"{typfilter}{lansfilter}"
        "</FILTER>"
        f"{inkludera}"
        "</QUERY></REQUEST>"
    )


def kameror(latt=True, typer=None, forsok=3):
    """Hämtar kameralistan. Kastar om API:t svarar med fel eller trunkerar.

    Trafikverket returnerar då och då ett tomt svar utan att flagga fel. Ett
    tomt svar får aldrig tolkas som "det finns inga kameror" — då hade svepet
    tyst arkiverat ingenting — men det är inte heller värt att offra ett svep
    för, så vi försöker om ett par gånger först.
    """
    typer = typer or config.TYPER
    body = _query(FALT_LATT if latt else FALT_FULL, typer).encode("utf-8")
    sista_fel = None

    for n in range(forsok):
        if n:
            time.sleep(3 * n)
        try:
            req = urllib.request.Request(
                config.TRV_URL,
                data=body,
                headers={"Content-Type": "text/xml", "User-Agent": config.UA,
                         "Accept-Encoding": "gzip"},
            )
            with urllib.request.urlopen(req, timeout=config.TIMEOUT) as r:
                rå = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    rå = gzip.decompress(rå)
            svar = json.loads(rå)["RESPONSE"]["RESULT"][0]
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, KeyError, IndexError) as e:
            sista_fel = f"{type(e).__name__}: {e}"
            continue

        fel = svar.get("ERROR")
        if fel:
            # "Maximum response size is reached" betyder att listan är avhuggen —
            # då saknas kameror tyst, och det vill vi inte upptäcka om ett halvår.
            raise RuntimeError(f"Trafikverket svarade med fel: {fel}")

        lista = svar.get("Camera") or []
        if lista:
            return lista
        sista_fel = "tomt svar"

    raise RuntimeError(f"Trafikverket gav ingen kameralista på {forsok} försök ({sista_fel})")


def bild_url(photo_url, variant=None):
    variant = variant or config.VARIANT
    return photo_url if variant == "medium" else f"{photo_url}?type={variant}"


def hamta_bild(photo_url, variant=None):
    """Laddar ner en bild. Returnerar (bytes, phototime-header)."""
    req = urllib.request.Request(
        bild_url(photo_url, variant), headers={"User-Agent": config.UA}
    )
    with urllib.request.urlopen(req, timeout=config.TIMEOUT) as r:
        return r.read(), r.headers.get("phototime")
