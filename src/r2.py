"""Cloudflare R2 — bufferten mellan svep och komprimering.

R2 talar S3, men vi implementerar signeringen själva i stället för att dra in
boto3. Ett insamlingsjobb som ska rulla i åratal mår bra av att inte ha några
beroenden alls: inget att uppgradera, inget som slutar fungera när en
transitiv dependency ändrar sig.

Bufferten innehåller bara råmaterial på väg till archive.org. Ingenting här är
tänkt att överleva mer än ett dygn.
"""

import datetime
import hashlib
import hmac
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import config

REGION = "auto"
TJANST = "s3"
TOM_HASH = hashlib.sha256(b"").hexdigest()


def _konf():
    konto = os.environ.get("R2_ACCOUNT_ID", "")
    nyckel = os.environ.get("R2_ACCESS_KEY_ID", "")
    hemlig = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    hink = os.environ.get("R2_BUCKET", "")
    if not all((konto, nyckel, hemlig, hink)):
        saknas = [n for n, v in (("R2_ACCOUNT_ID", konto), ("R2_ACCESS_KEY_ID", nyckel),
                                 ("R2_SECRET_ACCESS_KEY", hemlig), ("R2_BUCKET", hink)) if not v]
        raise RuntimeError(f"R2 saknar {', '.join(saknas)} — se .env")
    return konto, nyckel, hemlig, hink


def _hmac(nyckel, meddelande):
    return hmac.new(nyckel, meddelande.encode("utf-8"), hashlib.sha256).digest()


def _signera(metod, vag, query, kropp, extra_headers=None):
    """AWS Signature Version 4. Returnerar (url, headers)."""
    konto, access, hemlig, _ = _konf()
    host = f"{konto}.r2.cloudflarestorage.com"
    nu = datetime.datetime.now(datetime.timezone.utc)
    stampel = nu.strftime("%Y%m%dT%H%M%SZ")
    dag = nu.strftime("%Y%m%d")

    kroppshash = hashlib.sha256(kropp).hexdigest() if kropp else TOM_HASH
    headers = {
        "host": host,
        "x-amz-content-sha256": kroppshash,
        "x-amz-date": stampel,
    }
    headers.update({k.lower(): v for k, v in (extra_headers or {}).items()})

    sorterade = sorted(headers.items())
    kanoniska = "".join(f"{k}:{v.strip()}\n" for k, v in sorterade)
    signerade = ";".join(k for k, _ in sorterade)

    kanonisk_query = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted((query or {}).items())
    )
    kanonisk_vag = urllib.parse.quote(vag, safe="/~")
    kanonisk = (f"{metod}\n{kanonisk_vag}\n{kanonisk_query}\n"
                f"{kanoniska}\n{signerade}\n{kroppshash}")

    scope = f"{dag}/{REGION}/{TJANST}/aws4_request"
    att_signera = (f"AWS4-HMAC-SHA256\n{stampel}\n{scope}\n"
                   f"{hashlib.sha256(kanonisk.encode('utf-8')).hexdigest()}")

    k = _hmac(f"AWS4{hemlig}".encode("utf-8"), dag)
    k = _hmac(k, REGION)
    k = _hmac(k, TJANST)
    k = _hmac(k, "aws4_request")
    signatur = hmac.new(k, att_signera.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
        f"SignedHeaders={signerade}, Signature={signatur}"
    )
    url = f"https://{host}{kanonisk_vag}"
    if kanonisk_query:
        url += f"?{kanonisk_query}"
    return url, headers


def _begar(metod, vag, query=None, kropp=None, extra_headers=None, timeout=120):
    url, headers = _signera(metod, vag, query, kropp, extra_headers)
    req = urllib.request.Request(url, data=kropp, method=metod)
    for k, v in headers.items():
        if k != "host":  # urllib sätter Host själv
            req.add_header(k, v)
    req.add_header("User-Agent", config.UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _vag(nyckel=""):
    _, _, _, hink = _konf()
    return f"/{hink}/{nyckel}" if nyckel else f"/{hink}"


def skriv(nyckel, data, content_type="application/octet-stream"):
    if isinstance(data, str):
        data = data.encode("utf-8")
    _begar("PUT", _vag(nyckel), kropp=data,
           extra_headers={"content-type": content_type})
    return len(data)


def las(nyckel):
    """Returnerar bytes, eller None om nyckeln inte finns."""
    try:
        _, data = _begar("GET", _vag(nyckel))
        return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def finns(nyckel):
    try:
        _begar("HEAD", _vag(nyckel))
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def radera(nyckel):
    _begar("DELETE", _vag(nyckel))


def lista(prefix="", max_nycklar=1000):
    """Alla nycklar under ett prefix. Sidindelar själv."""
    nycklar = []
    fortsatt = None
    while True:
        query = {"list-type": "2", "prefix": prefix, "max-keys": str(max_nycklar)}
        if fortsatt:
            query["continuation-token"] = fortsatt
        _, kropp = _begar("GET", _vag(), query=query)
        xml = kropp.decode("utf-8", "replace")
        nycklar += re.findall(r"<Key>([^<]+)</Key>", xml)
        if "<IsTruncated>true</IsTruncated>" not in xml:
            break
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            break
        fortsatt = m.group(1)
    return nycklar


STOPP = "status/STOPP"


def stoppad():
    """Nödbroms. Finns objektet status/STOPP i bucketen slutar allt skriva.

    Den ligger i bufferten och inte i koden av ett skäl: den går att slå på
    från Cloudflares webbgränssnitt på tio sekunder, utan git, utan GitHub,
    utan att något behöver deployas. Det är den broms som fungerar även när
    allt annat krånglar.
    """
    try:
        return finns(STOPP)
    except Exception:  # noqa: BLE001 — kan vi inte fråga, kör vidare
        return False


def anvandning(prefix=""):
    """(antal objekt, totala bytes) — för att hålla koll på 10 GB-gränsen."""
    antal = byte = 0
    fortsatt = None
    while True:
        query = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if fortsatt:
            query["continuation-token"] = fortsatt
        _, kropp = _begar("GET", _vag(), query=query)
        xml = kropp.decode("utf-8", "replace")
        storlekar = [int(s) for s in re.findall(r"<Size>(\d+)</Size>", xml)]
        antal += len(storlekar)
        byte += sum(storlekar)
        if "<IsTruncated>true</IsTruncated>" not in xml:
            break
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            break
        fortsatt = m.group(1)
    return antal, byte
