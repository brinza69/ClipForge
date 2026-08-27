r"""Pune o melodie sub vocea unui clip deja randat, fara sa reencodeze imaginea.

De ce nu prin pipeline: `bg_music_db` din `remix_pipeline` amesteca sunetul
ORIGINAL AL SURSEI sub voce, nu un fisier de muzica — si sunetul sursei e
anulat deliberat pe toate pistele. Deci muzica se adauga aici, peste clipurile
gata, cu `-c:v copy`: imaginea nu se atinge, deci nu pierde o generatie de
compresie si dureaza secunde, nu minute.

Melodia se repeta cat tine clipul (`-stream_loop -1`) si primeste fade la
capete, altfel intra si se taie brusc. `amix` cu normalize=0, altfel vocea ar
fi injumatatita mecanic de numarul de intrari.

    server\.venv\Scripts\python.exe scripts\muzica_fundal.py intrare.mp4 iesire.mp4 [--db -20]
"""
import subprocess
import sys
import pathlib

_CF = 0x08000000 if __import__("os").name == "nt" else 0
MELODIE = pathlib.Path(r"C:\Users\mihai\Downloads\Countless.m4a")
FADE = 2.0            # secunde, la intrarea si la iesirea melodiei


def durata(cale):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nk=1:nw=1", str(cale)],
                         capture_output=True, text=True, timeout=120,
                         creationflags=_CF).stdout.strip()
    return float(out)


def volum(cale):
    """(mean_volume, max_volume) in dB — ca sa putem verifica, nu ghici."""
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", str(cale), "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True,
                       timeout=600, creationflags=_CF)
    mean = mx = None
    for line in p.stderr.splitlines():
        if "mean_volume:" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0])
        if "max_volume:" in line:
            mx = float(line.split("max_volume:")[1].split("dB")[0])
    return mean, mx


def pune_muzica(intrare, iesire, db=-20.0, melodie=MELODIE):
    d = durata(intrare)
    fade_out = max(0.0, d - FADE)
    filtru = (f"[1:a]volume={db}dB,afade=t=in:st=0:d={FADE},"
              f"afade=t=out:st={fade_out:.3f}:d={FADE}[muz];"
              f"[0:a][muz]amix=inputs=2:normalize=0:duration=first[aout]")
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-i", str(intrare),
           "-stream_loop", "-1", "-i", str(melodie),
           "-filter_complex", filtru,
           "-map", "0:v", "-map", "[aout]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(iesire)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                       creationflags=_CF)
    if p.returncode != 0:
        raise SystemExit(f"ffmpeg: {p.stderr[-400:]}")
    return iesire


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        raise SystemExit(__doc__)
    db = float(a[a.index("--db") + 1]) if "--db" in a else -20.0
    src, dst = pathlib.Path(a[0]), pathlib.Path(a[1])
    if not MELODIE.exists():
        raise SystemExit(f"lipseste melodia: {MELODIE}")
    print(f"melodie: {MELODIE.name}  ({durata(MELODIE):.0f}s, se repeta)")
    m0, x0 = volum(src)
    pune_muzica(src, dst, db)
    m1, x1 = volum(dst)
    print(f"  inainte: mean {m0} dB, max {x0} dB")
    print(f"  dupa   : mean {m1} dB, max {x1} dB   (muzica la {db} dB)")
    print(f"  scris: {dst}")
