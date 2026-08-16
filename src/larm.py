"""Larm till Telegram, och hjärtslag till en dödmansknapp.

Larmen går ut *från* jobbet och fungerar bara om jobbet kör. Det värsta
felet är därför det de inte kan fånga: att insamlingen slutar köra helt.
Mot det finns `hjartslag()` — en ping till en extern tjänst som larmar när
pingen uteblir. Den behöver inte veta något om oss, bara sakna oss.

Båda är tysta om de inte är konfigurerade. Ett larm som inte går fram ska
aldrig få fälla insamlingen."""

import json
import os
import urllib.error
import urllib.request


def hjartslag(suffix="", data=""):
    """Pingar HJARTSLAG_URL (healthchecks.io eller liknande).

    Uteblir pingen larmar tjänsten — det är det enda sättet att upptäcka att
    hela insamlingen tystnat, eftersom ett jobb som inte kör inte kan larma
    om sig självt. `suffix` kan vara "/fail" eller "/start" hos de flesta
    tjänster.
    """
    url = os.environ.get("HJARTSLAG_URL", "")
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url.rstrip("/") + suffix,
            data=data.encode("utf-8")[:10000] if data else None,
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001 — samma sak här
        print(f"[hjärtslag gick inte fram: {e}]")
        return False


def skicka(text, tyst=False):
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        print(f"[larm, ingen Telegram konfigurerad] {text}")
        return False
    data = json.dumps({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": tyst,
        "link_preview_options": {"is_disabled": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001 — ett larm som kraschar får aldrig
        print(f"[larm gick inte fram: {e}] {text}")  # fälla jobbet det larmar om
        return False
