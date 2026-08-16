#!/usr/bin/env python3
"""Skriver STATUS.md ur bufferten och archive.org.

Två syften. Det uppenbara: en läsbar sida över vad arkivet innehåller och när
det senast hämtade något. Det mindre uppenbara: GitHub stänger av schemalagda
arbetsflöden i repon som varit inaktiva i 60 dagar, och ett arkiv som bara
rullar på är per definition inaktivt. Den här filen committas en gång om
dygnet och håller schemat vid liv.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ia
import r2

UT = Path(__file__).resolve().parent.parent / "STATUS.md"


def las_json(nyckel):
    rå = r2.las(nyckel)
    if not rå:
        return None
    try:
        return json.loads(rå)
    except json.JSONDecodeError:
        return None


def alder(iso):
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def main():
    nu = datetime.now(timezone.utc)
    svep = las_json("status/senaste-svep.json") or {}
    packning = las_json("status/senaste-packning.json") or {}
    antal, byte = r2.anvandning("ra/")

    rader = ["# Status", "",
             f"Uppdaterad {nu:%Y-%m-%d %H:%M} UTC.", ""]

    d = alder(svep.get("ts"))
    if d is None:
        rader.append("**Senaste svep:** okänt — bufferten svarar inte.")
    else:
        minuter = int(d.total_seconds() // 60)
        skick = "✅" if minuter < 45 else "⚠️"
        rader += [
            f"**Senaste svep:** {skick} för {minuter} minuter sedan "
            f"({svep.get('ts', '')[:16]} UTC)", "",
            f"| | |", "|---|---|",
            f"| Kameror | {svep.get('kameror', '?')} |",
            f"| Nya bilder i svepet | {svep.get('sparade', '?')} |",
            f"| Oförändrade | {svep.get('dubbletter', '?')} |",
            f"| Fel | {svep.get('fel', '?')} |",
            f"| Buffert | {byte/1e9:.2f} GB i {antal} objekt |",
            "",
        ]

    if packning:
        dp = alder(packning.get("ts"))
        timmar = int(dp.total_seconds() // 3600) if dp else "?"
        rader += [f"**Senaste packning:** för {timmar} timmar sedan — "
                  f"{packning.get('klara', 0)} perioder klara, "
                  f"{packning.get('misslyckade', 0)} misslyckade.", ""]

    rader += ["## Arkivet", "",
              "Ett item per dygn på archive.org. Varje kamera har en video per "
              "sextimmarsperiod plus ett register med tidsstämplar.", "",
              "| dygn | filer | |", "|---|---|---|"]

    for i in range(1, 8):
        dygn = f"{nu - timedelta(days=i):%Y-%m-%d}"
        filer = ia.filer_i_item(dygn, timeout=30)
        if filer:
            rader.append(f"| {dygn} | {len(filer)} | "
                         f"[archive.org](https://archive.org/details/{ia.item_namn(dygn)}) |")

    rader += ["", "---", "",
              "Genereras av `src/status.py`. Siffrorna kommer ur bufferten och "
              "archive.org, inte ur någon separat bokföring."]

    UT.write_text("\n".join(rader) + "\n", encoding="utf-8")
    print(f"skrev {UT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
