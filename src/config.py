"""Inställningar. Allt går att styra med miljövariabler så att samma kod
fungerar likadant lokalt som på en server."""

import os
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent


def _las_env():
    """Läser .env om den finns. Riktiga miljövariabler vinner alltid, så att
    GitHub Secrets kan ta över utan att koden ändras. Rader som inte ser ut
    som nyckel=värde hoppas över i stället för att fälla hela körningen."""
    fil = ROT / ".env"
    if not fil.exists():
        return
    for rad in fil.read_text(encoding="utf-8").splitlines():
        rad = rad.strip()
        if not rad or rad.startswith("#") or "=" not in rad:
            continue
        nyckel, varde = rad.split("=", 1)
        nyckel = nyckel.strip()
        if nyckel and nyckel not in os.environ:
            os.environ[nyckel] = varde.strip()


_las_env()

# Var arkivet hamnar. Peka om till en extern disk eller en monterad
# storage box genom att sätta ARKIV_DIR.
ARKIV = Path(os.environ.get("ARKIV_DIR", ROT / "data" / "arkiv"))
STATE_DB = Path(os.environ.get("STATE_DB", ROT / "data" / "state.db"))
LOGG_DIR = Path(os.environ.get("LOGG_DIR", ROT / "data" / "logg"))
KAMERA_JSON = Path(os.environ.get("KAMERA_JSON", ROT / "data" / "kameror.json"))

# Trafikverkets öppna API. demokey står i deras egen testbänk och räcker
# för att läsa kameralistan; en egen nyckel är gratis men kräver konto.
TRV_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"
# En tom miljövariabel är inte samma sak som en osatt. GitHub Actions skickar
# tom sträng för hemligheter som inte finns, och utan `or` hade nyckeln blivit
# tom i stället för demokey — vilket ger 401 från Trafikverket.
TRV_NYCKEL = os.environ.get("TRV_NYCKEL") or "demokey"

# Vilka kameror. Trafikverket har två sorter: "Trafikflödeskamera" (1280x720,
# ~110 KB) och "Väglagskamera" (~2 MP, ~340 KB, uppdateras var 5:e minut).
TYPER = [t.strip() for t in os.environ.get("TYPER", "Trafikflödeskamera").split(",") if t.strip()]

# Vilka län. Tomt = hela landet. "1" = Stockholms län, "14" = Västra Götaland.
LAN = [n.strip() for n in os.environ.get("LAN", "").split(",") if n.strip()]

# Bildvariant: fullsize (1280x720), medium (385x217) eller thumbnail (180x101).
VARIANT = os.environ.get("VARIANT") or "fullsize"

# Rullande arkiv: rensa dygn äldre än så här många dagar. 0 = spara allt.
RETENTION_DAGAR = int(os.environ.get("RETENTION_DAGAR", "0"))

# Bara för test: ta bara N kameror i svepet.
BEGRANSA = int(os.environ.get("BEGRANSA", "0"))

PARALLELLA = int(os.environ.get("PARALLELLA", "8"))
TIMEOUT = int(os.environ.get("TIMEOUT", "30"))
FORSOK = int(os.environ.get("FORSOK", "3"))

# Stoppa svepet om disken börjar ta slut, hellre än att fylla den.
MIN_LEDIGT_GB = float(os.environ.get("MIN_LEDIGT_GB", "5"))

# Hårt tak för bufferten, i GB. Passeras det slutar svepet skriva — hellre
# ett stillastående arkiv än en oväntad räkning. R2:s gratisnivå är 10 GB,
# och taket ligger under den med marginal för att hinna larma i tid.
TAK_GB = float(os.environ.get("TAK_GB", "8"))
VARNA_GB = float(os.environ.get("VARNA_GB", "5"))

# Komprimering. H.264 är inte minst — AV1 är 40 % mindre vid samma kvalitet —
# men det är det enda formatet som spelas upp av varje webbläsare och varje
# enhet. Apple har ingen mjukvaruavkodare för AV1, så allt äldre än iPhone
# 15 Pro hade stått utan arkiv. Lagringen är gratis; publiken är det inte.
KODEK = os.environ.get("KODEK") or "h264"            # av1 | h265 | h264
CRF = os.environ.get("CRF", "")                   # tom = kodekens standard

# Trafikverket ber om att få veta vem som hämtar. Sätt KONTAKT till en
# mejladress så syns den i User-Agent.
KONTAKT = os.environ.get("KONTAKT", "")
UA = "trafikkamerarkivet/1.0" + (f" ({KONTAKT})" if KONTAKT else "")
