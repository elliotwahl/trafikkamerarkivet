#!/bin/zsh
# Startar arkivet som två launchd-jobb: svep var 5:e minut, komprimering 03:17.
#
#   ./scripts/installera.sh                    # arkivet under repots data/
#   ./scripts/installera.sh /Volumes/Disk/kam  # arkivet på en extern disk
#
# Avinstallera:  ./scripts/installera.sh --stopp

set -e
ROT="${0:a:h:h}"
AGENTS="$HOME/Library/LaunchAgents"
JOBB=(se.liot.trafikkamerarkivet.svep se.liot.trafikkamerarkivet.komprimering)

if [[ "$1" == "--stopp" ]]; then
  for j in $JOBB; do
    launchctl bootout "gui/$UID/$j" 2>/dev/null && echo "stoppade $j" || echo "$j kördes inte"
    rm -f "$AGENTS/$j.plist"
  done
  exit 0
fi

ARKIV="${1:-$ROT/data/arkiv}"
mkdir -p "$ARKIV" "$ROT/data/logg"

for j in $JOBB; do
  sed -e "s|@ROT@|$ROT|g" -e "s|@ARKIV@|$ARKIV|g" "$ROT/scripts/$j.plist" > "$AGENTS/$j.plist"
  launchctl bootout "gui/$UID/$j" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$AGENTS/$j.plist"
  echo "startade $j"
done

echo
echo "arkiv:  $ARKIV"
echo "logg:   $ROT/data/logg/svep.log"
echo "status: launchctl list | grep trafikkamerarkivet"
