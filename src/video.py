"""Videokodning av bildrutor, och kontrollen av att den blev rätt.

Uppmätt på 16 rutor från Essingeleden, 1280x720, 110 KB/ruta som JPEG:

    av1  crf 32 -> 15,6 KB/ruta   (7,0x mindre än JPEG)
    av1  crf 40 -> 10,4 KB/ruta   (PSNR 38,0 / SSIM 0,952)
    h265 crf 38 -> 13,2 KB/ruta   (PSNR 38,5 / SSIM 0,954)

Standardvalet är medvetet försiktigt. Vid 200 % förstoring är enda synliga
skillnaden mot originalet att sensorbruset är borta.

Antalet rutor per video spelar större roll än avståndet mellan dem: fyra
rutor kostar 32 KB styck, sexton kostar 15,6, eftersom nyckelbilden delas av
fler. Därför kodas sextimmarsperioder, inte enskilda hämtningar.
"""

import subprocess

# kodek: (ffmpeg-encoder, standard-CRF, extra flaggor)
KODEKAR = {
    "av1": ("libsvtav1", "32", ["-preset", "6"]),
    "h265": ("libx265", "30", ["-tag:v", "hvc1", "-x265-params", "log-level=error"]),
    "h264": ("libx264", "24", ["-preset", "slow"]),
}


def antal_rutor(fil):
    """Räknar rutorna i en färdig video. -1 om den inte gick att läsa."""
    ut = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(fil)],
        capture_output=True, text=True,
    )
    try:
        return int(ut.stdout.strip().split(",")[0])
    except (ValueError, IndexError):
        return -1


def koda(filer, ut, kodek, crf=""):
    """Bildrutor -> video, en ruta per sekund. Returnerar (ok, felmeddelande).

    Rutorna skalas till 1280x720 med bibehållna proportioner — kamerorna
    byter upplösning ibland, och en video kan bara ha en storlek.
    """
    if len(filer) < 2:
        return False, "färre än två rutor"
    encoder, crf_standard, extra = KODEKAR[kodek]
    lista = ut.parent / f".{ut.stem}.txt"
    lista.write_text("".join(f"file '{f}'\nduration 1\n" for f in filer), encoding="utf-8")
    kord = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lista),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:v", encoder, "-crf", crf or crf_standard,
        "-pix_fmt", "yuv420p", "-r", "1", "-movflags", "+faststart",
        *extra, str(ut),
    ], capture_output=True, text=True)
    lista.unlink(missing_ok=True)
    if kord.returncode != 0:
        return False, f"ffmpeg: {kord.stderr.strip()[:200]}"

    # Lita aldrig på att videon blev rätt — räkna rutorna i den.
    n = antal_rutor(ut)
    if n != len(filer):
        ut.unlink(missing_ok=True)
        return False, f"{n} rutor i videon, {len(filer)} förväntade"
    return True, ""
