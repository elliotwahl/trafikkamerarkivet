#!/bin/zsh
# Kopierar värdena i .env till GitHub Secrets, så att arbetsflödena kommer åt
# dem. Skriver aldrig ut värdena — bara vilka nycklar som satts.
#
#   ./scripts/lagg-in-secrets.sh
#
# Kräver att `gh` är inloggat mot rätt konto.

set -e
ROT="${0:a:h:h}"
cd "$ROT"

if [[ ! -f .env ]]; then
  echo "ingen .env i $ROT" >&2
  exit 1
fi

# Bara de nycklar arbetsflödena faktiskt läser. Allt annat i .env ignoreras.
NYCKLAR=(
  R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET
  IA_ACCESS IA_SECRET
  TELEGRAM_TOKEN TELEGRAM_CHAT_ID
  HJARTSLAG_URL KONTAKT TRV_NYCKEL
)

satta=0
hoppade=()
for nyckel in $NYCKLAR; do
  # Trimma blanksteg: ett inledande mellanslag i en token blir en
  # kontrolltecken-krasch långt senare, i ett helt annat sammanhang.
  varde=$(grep -E "^${nyckel}=" .env | head -1 | cut -d= -f2- | sed -E "s/^[[:space:]]+//; s/[[:space:]]+$//")
  if [[ -z "$varde" ]]; then
    hoppade+=("$nyckel")
    continue
  fi
  # gh läser värdet från stdin när --body utelämnas, vilket håller
  # hemligheterna borta från processlistan.
  printf '%s' "$varde" | gh secret set "$nyckel" >/dev/null
  echo "  satt $nyckel"
  ((satta++)) || true
done

echo
echo "$satta hemligheter satta i $(gh repo view --json nameWithOwner -q .nameWithOwner)"
if (( ${#hoppade} )); then
  echo "tomma i .env, hoppades över: ${hoppade[*]}"
fi
