"""Videokodning av bildrutor, och kontrollen av att den blev rätt.

Uppmätt på 16 rutor från Essingeleden, 1280x720, 110 KB/ruta som JPEG.
Kvalitetsmatchat, alla tre kring PSNR 39,6 och SSIM 0,963:

    av1  crf 32 -> 15,6 KB/ruta
    h265 crf 36 -> 19,0 KB/ruta
    h264 crf 34 -> 26,2 KB/ruta   <- standard

H.264 är störst av de tre och valt ändå. Apple har ingen mjukvaruavkodare
för AV1, så varje iPhone äldre än 15 Pro hade stått utan arkiv, och H.265
saknas i Firefox. H.264 spelas av allt som finns. Skillnaden är 1,7x på
lagring som är gratis och obegränsad — priset för att låsa ute halva
publiken är inte det.

Vid 200 % förstoring är enda synliga skillnaden mot originalet att
sensorbruset är borta.

Antalet rutor per video spelar större roll än avståndet mellan dem: fyra
rutor kostar 32 KB styck, sexton kostar 15,6, eftersom nyckelbilden delas av
fler. Därför kodas sextimmarsperioder, inte enskilda hämtningar.
"""

import subprocess

# kodek: (ffmpeg-encoder, standard-CRF, extra flaggor)
KODEKAR = {
    "av1": ("libsvtav1", "32", ["-preset", "6"]),
    "h265": ("libx265", "30", ["-tag:v", "hvc1", "-x265-params", "log-level=error"]),
    "h264": ("libx264", "34", ["-preset", "veryslow"]),
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
