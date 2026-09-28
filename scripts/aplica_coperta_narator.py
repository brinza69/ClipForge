r"""Lipeste coperta primita de la om la inceputul clipurilor narator.

Diferenta fata de scriptul din 25 august: acela re-encoda TOT clipul la
concatenare, adica o generatie de compresie pierduta pe fiecare video. Aici se
encodeaza DOAR cardul, cu exact aceiasi parametri ca randarea
(`encode_profile.video_args(60)`), iar lipirea se face cu `-c copy` — corpul
clipului trece neatins prin ffmpeg. Se verifica numarul de pachete (trebuie sa
creasca exact cu cadrele cardului) si parametrii fluxului. Daca nu se potrivesc,
nu se urca nimic.

Imaginile stau in `data/coperti_narator/<NR>.png|jpg`, UNA pe poveste. Badge-ul
"Partea N" il pune scriptul, ca partile aceleiasi povesti sa arate identic —
altfel omul ar trebui sa faca doua imagini aproape la fel.

Fisierele se urca peste ACELASI id de Drive, deci linkurile din sheet raman
valide. Evidenta in `data/coperti_narator_facute.json`: fara ea, o a doua rulare
ar lipi inca un card peste primul si clipul ar incepe cu doua coperti.

    server\.venv\Scripts\python.exe scripts\aplica_coperta_narator.py [--dry] [--nr 276]
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402
from services.drive_upload import _resolve_credentials, list_folder_files  # noqa: E402
from services.encode_profile import video_args  # noqa: E402

_CF = 0x08000000 if os.name == "nt" else 0
COPERTI = _ROOT / "data" / "coperti_narator"
LUCRU = _ROOT / "data" / "coperti_lucru"
STARE = _ROOT / "data" / "coperti_narator_facute.json"
FONT = str(_ROOT / "data" / "fonts" / "BebasNeue-Regular.ttf").replace("\\", "/").replace(":", "\\:")
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+))?\.mp4$", re.I)
DURATA = 0.9          # cat sta cardul; peste ~1s se simte ca o pauza
W, H, FPS = 1080, 1920, 60

DRY = "--dry" in sys.argv
DOAR = sys.argv[sys.argv.index("--nr") + 1] if "--nr" in sys.argv else None

# INCHIS 29 aug 2026: omul a cerut clipurile fara card la inceput, si asa raman.
# Copertile au fost scoase de pe toate cele 29 de fisiere narator in aceeasi zi
# (`scoate_coperta_narator.py`), deci o rulare din obisnuinta a scriptului asta
# le-ar pune la loc pe cele urmatoare.
#
# Fisierul NU se sterge: comentariile de mai sus tin masuratorile care au costat
# o sesiune — de ce nu se compara marimile pachetelor, cei 37 de octeti de
# SPS/PPS, cele 0,9 secunde ale cardului. Daca revine cererea de coperti, aici e
# tot ce trebuie stiut.
#
# Verificarea sta in `main()`, NU la nivel de modul: `pune_muzica_narator.py` si
# `scoate_coperta_narator.py` importa fisierul asta pentru `amprenta()`,
# `durata()` si `lipeste()`. Un SystemExit la import le-ar rupe exact pe cele
# care trebuie sa mearga mai departe.
INCHIS = ("fara coperti din 29 aug 2026 — cerut explicit. "
          "Pentru muzica singura foloseste scripts/pune_muzica_narator.py")


def rul(cmd, timeout=1800):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       creationflags=_CF)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-500:])
    return p


def amprenta(video):
    """(numar de pachete video, profil, latime, inaltime, fps) — cat trebuie ca
    sa stim ca lipirea a mers si n-a stricat nimic.

    NU se compara marimile pachetelor. `-c copy` nu poate re-encoda — ori
    copiaza, ori esueaza — deci garantia e in flag. In schimb, la imbinarea a
    doua fisiere ffmpeg insereaza setul de parametri (SPS/PPS) in cadrele cheie,
    ceea ce schimba marimea cu cateva zeci de octeti fara nicio pierdere de
    calitate. Prima varianta compara pachetele si respingea din cauza asta un
    fisier perfect bun (280_p2: 118848 vs 118885, adica 37 octeti).

    Ce ramane de verificat e ce chiar se poate strica: un flux trunchiat, sau
    parametri schimbati.
    """
    n = rul(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", str(video)]).stdout
    p = rul(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=profile,width,height,r_frame_rate", "-of", "csv=p=0",
             str(video)]).stdout.strip()
    return len([x for x in n.split() if x]), p


def durata(v):
    return float(rul(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nk=1:nw=1", str(v)]).stdout.strip())


def fa_card(imagine, dest, parte=None, total=1):
    """Cardul: imaginea adusa la 1080x1920 prin acoperire (fara benzi negre),
    plus badge-ul 'Partea N' cand povestea are mai multe parti."""
    vf = ["scale=" + str(W) + ":" + str(H) + ":force_original_aspect_ratio=increase",
          "crop=" + str(W) + ":" + str(H), "setsar=1"]
    if total > 1 and parte:
        vf.append("drawbox=x=(w-560)/2:y=h-330:w=560:h=150:color=black@0.72:t=fill")
        vf.append("drawtext=fontfile='" + FONT + "':text='Partea " + str(parte) + "'"
                  ":fontcolor=white:fontsize=104:borderw=8:bordercolor=black"
                  ":x=(w-text_w)/2:y=h-305")
    rul(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", str(DURATA), "-i", str(imagine),
         "-f", "lavfi", "-t", str(DURATA), "-i", "anullsrc=r=44100:cl=stereo",
         "-vf", ",".join(vf), "-r", str(FPS), "-preset", "medium",
         *video_args(FPS, crf_fallback="18"), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
         "-shortest", "-movflags", "+faststart", str(dest)])


def lipeste(card, video, dest):
    """Concat FARA re-encodare. Cardul are aceiasi parametri ca randarea, deci
    fluxurile se pot copia cap la cap."""
    lst = LUCRU / "lista.txt"
    lst.write_text("file '" + str(card).replace("\\", "/") + "'\n"
                   "file '" + str(video).replace("\\", "/") + "'\n", encoding="utf-8")
    rul(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-movflags", "+faststart", str(dest)])
    lst.unlink(missing_ok=True)


def main():
    if "--si-inchise" not in sys.argv:
        print("scriptul e inchis: " + INCHIS)
        print("adauga --si-inchise daca chiar asta vrei.")
        return
    LUCRU.mkdir(parents=True, exist_ok=True)
    facute = json.loads(STARE.read_text(encoding="utf-8")) if STARE.exists() else {}
    imagini = {}
    for p in sorted(COPERTI.glob("*")):
        m = re.match(r"^(\d+)\.(png|jpg|jpeg)$", p.name, re.I)
        if m:
            imagini[m.group(1)] = p
    if not imagini:
        raise SystemExit("nicio coperta in " + str(COPERTI))

    res = list_folder_files(targets.get("narator_drive_folder"))
    if res.get("status") != "ok":
        raise SystemExit("nu pot lista Drive: " + str(res.get("reason")))
    pe_nr = {}
    for f in res["files"]:
        m = NAME_RE.match(f.get("name") or "")
        if m and m.group(1) in imagini:
            pe_nr.setdefault(m.group(1), []).append((int(m.group(2) or 1), f))

    print("coperti gasite: " + str(sorted(imagini, key=int)))
    drive = None
    if not DRY:
        creds, _, err = _resolve_credentials()
        if not creds:
            raise SystemExit("Google Drive: " + str(err))
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    for nr in sorted(pe_nr, key=int):
        if DOAR and nr != DOAR:
            continue
        parti = sorted(pe_nr[nr])
        total = len(parti)
        for parte, f in parti:
            eticheta = f["name"] + " (" + str(parte) + "/" + str(total) + ")"
            if f["id"] in facute:
                print("  " + eticheta + ": are deja coperta, sar")
                continue
            if DRY:
                print("  " + eticheta + ": as lipi " + imagini[nr].name)
                continue
            print("\n  " + eticheta, flush=True)
            src = LUCRU / f["name"]
            card = LUCRU / ("card_" + nr + "_" + str(parte) + ".mp4")
            out = LUCRU / ("out_" + f["name"])
            try:
                if not src.exists():
                    urllib.request.urlretrieve(f["download_url"], src)
                n0, par0 = amprenta(src)
                d0 = durata(src)
                fa_card(imagini[nr], card, parte, total)
                lipeste(card, src, out)
                d1 = durata(out)
                n1, par1 = amprenta(out)
                cadre_card = round(DURATA * FPS)
                if par1 != par0 or abs(n1 - n0 - cadre_card) > 2:
                    print("     IESIRE GRESITA: " + str(n0) + "->" + str(n1) +
                          " pachete (asteptam +" + str(cadre_card) + "), " +
                          par0 + " -> " + par1 + " — nu urc")
                    continue
                print("     durata %.1fs -> %.1fs (+%.2fs), corp neatins" % (d0, d1, d1 - d0))
                drive.files().update(fileId=f["id"],
                                     media_body=MediaFileUpload(str(out), resumable=True),
                                     fields="id").execute()
                facute[f["id"]] = {"nume": f["name"], "coperta": imagini[nr].name}
                STARE.write_text(json.dumps(facute, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
                for x in (src, card, out):
                    x.unlink(missing_ok=True)
                print("     urcat peste acelasi id", flush=True)
            except Exception as e:  # noqa: BLE001
                print("     ESUAT: " + str(e)[:250], flush=True)
    print("\ngata: " + str(len(facute)) + " fisiere cu coperta")


if __name__ == "__main__":
    main()
