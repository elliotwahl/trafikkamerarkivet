"""Hämtar en kamerabild. Delas av svepet och allt annat som behöver bilder."""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import trv


def parsa_tid(s):
    """Trafikverkets PhotoTime, t.ex. 2026-08-15T21:08:02.000+02:00 -> UTC."""
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def en(kam):
    """Laddar ner en kameras bild. Returnerar (kam, bytes, fel).

    Kastar aldrig — ett nätfel på en kamera av 786 ska kosta den kameran,
    inte hela svepet.
    """
    for forsok in range(config.FORSOK):
        try:
            data, _ = trv.hamta_bild(kam["PhotoUrl"])
            if len(data) < 1024:
                return kam, None, f"misstänkt liten bild ({len(data)} B)"
            return kam, data, None
        except Exception as e:  # noqa: BLE001
            if forsok == config.FORSOK - 1:
                return kam, None, f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (forsok + 1))
    return kam, None, "okänt fel"
