#!/bin/zsh
# Stoppar allt. Två oberoende bromsar, för att den ena kan vara otillgänglig
# när man behöver den.
#
#   ./scripts/nodbroms.sh            stoppa
#   ./scripts/nodbroms.sh --slapp    släpp bromsen igen
#   ./scripts/nodbroms.sh --status   vad är läget
#
# Broms 1: en STOPP-fil i bufferten. Både svepet och packningen kollar den
#          först av allt och gör ingenting om den finns. Den går att skapa
#          och radera från Cloudflares webbgränssnitt på tio sekunder —
#          utan git, utan GitHub, utan att något deployas.
#
# Broms 2: GitHubs scheman stängs av, så jobben startar inte alls.
#
# Broms 1 räcker för att inget ska skrivas. Broms 2 sparar dessutom
# Actions-minuter och slipper misslyckade körningar i loggen.

set -e
ROT="${0:a:h:h}"
cd "$ROT"
FLOWS=(svep.yml packa.yml status.yml)

status() {
  python3 - <<'PY'
import sys
sys.path.insert(0, "src")
import config, r2
try:
    på = r2.finns(r2.STOPP)
    antal, byte = r2.anvandning()
    print(f"  nödbroms i bufferten : {'I ‼️' if på else 'av'}")
    print(f"  buffert              : {byte/1e9:.2f} GB i {antal} objekt "
          f"(tak {config.TAK_GB} GB, gratisnivå 10 GB)")
except Exception as e:
    print(f"  bufferten svarar inte: {e}")
PY
  echo "  scheman              :"
  gh workflow list 2>/dev/null | sed 's/^/    /'
}

case "${1:-stoppa}" in
  --status)
    status
    ;;
  --slapp)
    python3 -c "import sys; sys.path.insert(0,'src'); import config, r2; r2.radera(r2.STOPP); print('  STOPP borttagen ur bufferten')" || true
    for f in $FLOWS; do gh workflow enable "$f" 2>/dev/null && echo "  slog på $f"; done
    echo
    status
    ;;
  *)
    python3 -c "import sys; sys.path.insert(0,'src'); import config, r2; r2.skriv(r2.STOPP, 'stoppad manuellt', 'text/plain'); print('  STOPP skriven till bufferten')"
    for f in $FLOWS; do gh workflow disable "$f" 2>/dev/null && echo "  stängde av $f"; done
    python3 -c "import sys; sys.path.insert(0,'src'); import config, larm; larm.skicka('🛑 <b>Nödbromsen är i.</b> Svep, packning och status är avstängda. Inget skrivs till bufferten.')" >/dev/null || true
    echo
    status
    ;;
esac
