#!/usr/bin/env python3
"""Packar ihop ett dygns bildrutor per kamera till en video + ett register.

Poängen: en trafikkamera står still. Bakgrunden är identisk från ruta till
ruta och bara bilarna rör sig, vilket videokodekar är byggda för att utnyttja.
Samma dygn som JPEG-rutor tar mångdubbelt större plats än som dygnsvideo.

Körs på natten, efter att dygnet är färdigt:
    python3 src/compact.py              # gårdagen (UTC)
    python3 src/compact.py 2026-08-15   # ett visst dygn
    python3 src/compact.py --behall     # verifiera utan att radera rutorna
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config

KODEKAR = {
    # kodek: (ffmpeg-encoder, standard-CRF, extra flaggor)
    # Uppmätt på 16 rutor från Essingeleden (1280x720, 110 KB/ruta som JPEG):
    #   av1 crf 32 -> 17 KB/ruta   av1 crf 40 -> 10 KB/ruta
    #   h265 crf 38 -> 13 KB/ruta (samma PSNR som av1 crf 40, 25 % större)
    "av1": ("libsvtav1", "32", ["-preset", "6"]),
    "h265": ("libx265", "30", ["-tag:v", "hvc1", "-x265-params", "log-level=error"]),
    "h264": ("libx264", "24", ["-preset", "slow"]),
}


def ffprobe_antal(fil):
    ut = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(fil)],
        capture_output=True, text=True,
    )
    try:
        return int(ut.stdout.strip().split(",")[0])
    except (ValueError, IndexError):
        return -1


def koda(katalog, rutor, kodek):
    encoder, crf_standard, extra = KODEKAR[kodek]
    crf = config.CRF or crf_standard
    lista = katalog / ".rutor.txt"
    # concat-demuxern tar rutorna i exakt den ordning vi bestämt, oavsett
    # om någon fil råkat få ett namn som sorterar konstigt.
    lista.write_text(
        "".join(f"file '{r['f']}'\nduration 1\n" for r in rutor), encoding="utf-8"
    )
    ut = katalog / f"dygn.{'mp4' if kodek != 'av1' else 'mp4'}"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lista),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:v", encoder, "-crf", crf, "-pix_fmt", "yuv420p",
        "-r", "1", "-movflags", "+faststart", *extra, str(ut),
    ]
    kord = subprocess.run(cmd, capture_output=True, text=True)
    lista.unlink(missing_ok=True)
    if kord.returncode != 0:
        return None, kord.stderr.strip()[:300]
    return ut, None


def las_rutor(katalog):
    """Rutorna för dygnet, i tidsordning. index.jsonl är sanningen; saknas
    den faller vi tillbaka på filnamnen (som är tidsstämplar)."""
    index = katalog / "index.jsonl"
    rutor = []
    if index.exists():
        sedda = set()
        for rad in index.read_text(encoding="utf-8").splitlines():
            if not rad.strip():
                continue
            r = json.loads(rad)
            if r["f"] in sedda or not (katalog / r["f"]).exists():
                continue
            sedda.add(r["f"])
            rutor.append(r)
    else:
        for f in sorted(katalog.glob("*.jpg")):
            n = f.name  # 20260815T192002Z.jpg
            t = f"{n[0:4]}-{n[4:6]}-{n[6:8]}T{n[9:11]}:{n[11:13]}:{n[13:15]}Z"
            rutor.append({"f": n, "t": t, "b": f.stat().st_size})
    return sorted(rutor, key=lambda r: r["t"])


def komprimera_dygn(katalog, kodek, behall):
    rutor = las_rutor(katalog)
    if len(rutor) < 2:
        return None
    fore = sum(r.get("b") or (katalog / r["f"]).stat().st_size for r in rutor)

    video, fel = koda(katalog, rutor, kodek)
    if fel:
        print(f"  ! {katalog}: ffmpeg: {fel}")
        return None

    antal = ffprobe_antal(video)
    if antal != len(rutor):
        # Hellre behålla rutorna och reda ut det än att radera i blindo.
        print(f"  ! {katalog}: {antal} rutor i videon men {len(rutor)} förväntade — behåller JPEG")
        return None

    (katalog / "rutor.json").write_text(
        json.dumps(
            {
                "kamera": katalog.parents[2].name,
                "dygn": f"{katalog.parents[1].name}-{katalog.parent.name}-{katalog.name}",
                "kodek": kodek, "fps": 1, "video": video.name,
                "rutor": [
                    {"i": i, "t": r["t"], "b": r.get("b"), "sha256": r.get("sha256")}
                    for i, r in enumerate(rutor)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    efter = video.stat().st_size
    if not behall:
        for r in rutor:
            (katalog / r["f"]).unlink(missing_ok=True)
        (katalog / "index.jsonl").unlink(missing_ok=True)
    return len(rutor), fore, efter


def main(argv):
    behall = "--behall" in argv
    datum = [a for a in argv if not a.startswith("--")]
    if datum:
        d = datetime.strptime(datum[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        d = datetime.now(timezone.utc) - timedelta(days=1)

    kodek = config.KODEK
    if kodek not in KODEKAR:
        print(f"okänd kodek {kodek}, välj bland {', '.join(KODEKAR)}")
        return 2

    monster = f"*/{d:%Y}/{d:%m}/{d:%d}"
    kataloger = sorted(p for p in config.ARKIV.glob(monster) if p.is_dir())
    print(f"komprimerar {d:%Y-%m-%d} ({kodek}): {len(kataloger)} kameror")

    kameror = rutor = fore = efter = 0
    for k in kataloger:
        res = komprimera_dygn(k, kodek, behall)
        if not res:
            continue
        n, f0, f1 = res
        kameror += 1
        rutor += n
        fore += f0
        efter += f1

    if kameror:
        print(f"  {kameror} kameror, {rutor} rutor: "
              f"{fore/1e9:.2f} GB -> {efter/1e9:.2f} GB ({fore/max(efter,1):.1f}x mindre, "
              f"{efter/max(rutor,1)/1024:.1f} KB/ruta)")
    else:
        print("  inget att komprimera")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
