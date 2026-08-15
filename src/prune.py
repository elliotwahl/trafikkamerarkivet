#!/usr/bin/env python3
"""Rullande arkiv: rensar dygn äldre än RETENTION_DAGAR.

Med rensning slutar arkivet växa efter första året — det lägger sig på en
platå i stället för att kräva mer disk varje månad.

    RETENTION_DAGAR=365 python3 src/prune.py          # visar vad som skulle tas bort
    RETENTION_DAGAR=365 python3 src/prune.py --kor    # tar bort på riktigt
"""

import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config


def dygn_i_arkivet():
    """Ger (katalog, datum) för varje kamera-dygn."""
    for katalog in config.ARKIV.glob("*/[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"):
        if not katalog.is_dir():
            continue
        try:
            d = datetime(
                int(katalog.parents[1].name), int(katalog.parent.name), int(katalog.name),
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
        yield katalog, d


def storlek(katalog):
    return sum(f.stat().st_size for f in katalog.rglob("*") if f.is_file())


def main(argv):
    kor = "--kor" in argv
    if config.RETENTION_DAGAR <= 0:
        print("RETENTION_DAGAR är 0 — arkivet sparar allt. Inget att rensa.")
        return 0

    grans = datetime.now(timezone.utc) - timedelta(days=config.RETENTION_DAGAR)
    gamla = sorted(((k, d) for k, d in dygn_i_arkivet() if d < grans), key=lambda x: x[1])
    if not gamla:
        print(f"inget äldre än {grans:%Y-%m-%d} ({config.RETENTION_DAGAR} dagar)")
        return 0

    bytes_ = sum(storlek(k) for k, _ in gamla)
    print(f"{'tar bort' if kor else 'skulle ta bort'} {len(gamla)} kamera-dygn "
          f"äldre än {grans:%Y-%m-%d}: {bytes_/1e9:.2f} GB "
          f"({gamla[0][1]:%Y-%m-%d} till {gamla[-1][1]:%Y-%m-%d})")

    if not kor:
        print("kör med --kor för att radera")
        return 0

    for katalog, _ in gamla:
        shutil.rmtree(katalog, ignore_errors=True)
    # Tomma år/månad-kataloger städas bort så att arkivet inte fylls av skal.
    for tom in sorted(config.ARKIV.glob("*/*/*"), reverse=True):
        if tom.is_dir() and not any(tom.iterdir()):
            tom.rmdir()
    for tom in sorted(config.ARKIV.glob("*/*"), reverse=True):
        if tom.is_dir() and not any(tom.iterdir()):
            tom.rmdir()
    print(f"raderade {bytes_/1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
