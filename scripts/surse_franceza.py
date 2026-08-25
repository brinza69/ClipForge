r"""Care rand din sheet-ul francez vine de la Varizz si care de la HerStory.

Postarea merge Varizz intai, pana se epuizeaza, apoi HerStory — ceruta explicit
pe 25 august 2026. Sheet-ul nu retine canalul sursa, iar coloana
`projects.channel_name` din baza de date exista dar nu e populata niciodata de
downloader, deci sursa se afla doar de la YouTube.

Nu se intreaba link cu link. Se cere O SINGURA data lista de shorts a canalului
Varizz (~4800 id-uri) si se intersecteaza cu id-urile din sheet: 91 de
interogari de retea devin una. Ce nu e in lista e HerStory.

Rezultatul se scrie in `data/surse_franceza.json` si e citit de
`build_fr_post_list.py`. Ruleaza-l din nou dupa ce adaugi linkuri noi.

    server\.venv\Scripts\python.exe scripts\surse_franceza.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from services.sheets import _service  # noqa: E402

OUT = _ROOT / "data" / "surse_franceza.json"
YTDLP = _ROOT / "server" / ".venv" / "Scripts" / "yt-dlp.exe"
VARIZZ = "https://www.youtube.com/channel/UC1IRs70Doav_GHdcAXbbBcQ/shorts"
VID_RE = re.compile(r"(?:shorts/|v=|youtu\.be/|/live/)([A-Za-z0-9_-]{11})")
# O treime din randuri sunt linkuri TikTok, nu YouTube. Acolo canalul e chiar in
# adresa, deci nu are rost sa intrebam pe nimeni.
TT_RE = re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)/")
HANDLE = {"herytstory": "HerStory"}
_CF = 0x08000000 if os.name == "nt" else 0


def id_video(url):
    m = VID_RE.search(url or "")
    return m.group(1) if m else None


def canal_tiktok(url):
    m = TT_RE.search(url or "")
    if not m:
        return None
    h = m.group(1).lower()
    return HANDLE.get(h, h)


def ids_varizz():
    """Toate id-urile canalului, dintr-o singura rulare yt-dlp.

    Iesirea trece prin fisier, nu prin `stdout=PIPE`: sunt zeci de mii de octeti
    si tiparul din proiect e sa nu lasam un copil vorbaret pe o teava de 64KB."""
    exe = str(YTDLP) if YTDLP.exists() else "yt-dlp"
    with tempfile.TemporaryDirectory() as d:
        lista = pathlib.Path(d) / "ids.txt"
        with open(lista, "w", encoding="utf-8") as f:
            p = subprocess.run([exe, "--no-warnings", "--flat-playlist",
                                "--print", "%(id)s", VARIZZ],
                               stdout=f, stderr=subprocess.DEVNULL,
                               timeout=900, creationflags=_CF)
        if p.returncode != 0:
            raise SystemExit("yt-dlp nu a putut lista canalul Varizz")
        return {x.strip() for x in lista.read_text(encoding="utf-8").splitlines() if x.strip()}


vals = _service().spreadsheets().values().get(
    spreadsheetId=targets.get("fr_sheet_id"),
    range=f"'{targets.get('fr_tab', 'Victoria')}'!A1:F400").execute().get("values", [])

randuri = []
for r in vals[1:]:
    g = lambda k: (r[k].strip() if len(r) > k and r[k] else "")  # noqa: E731
    if g(0) and g(1).startswith("http"):
        randuri.append((g(0), g(1)))

varizz = ids_varizz()
print(f"shorts pe canalul Varizz: {len(varizz)}")

def sursa(url):
    tt = canal_tiktok(url)
    if tt:
        return tt
    vid = id_video(url)
    if vid and vid in varizz:
        return "Varizz"
    return "HerStory" if vid else "NECUNOSCUT"


def desfa(url):
    """Un link scurt (`vm.tiktok.com/...`) nu contine nici id, nici handle.

    Se urmareste redirectarea o singura data, doar pentru ce a ramas
    neclasificat — altfel un clip ajunge la coada listei fara motiv."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.geturl()
    except Exception:  # noqa: BLE001
        return url


harta = {nr: sursa(url) for nr, url in randuri}
for nr, url in randuri:
    if harta[nr] == "NECUNOSCUT":
        harta[nr] = sursa(desfa(url))
OUT.write_text(json.dumps(harta, ensure_ascii=False, indent=1), encoding="utf-8")

pe_canal = {}
for nr, c in harta.items():
    pe_canal.setdefault(c, []).append(nr)
for c, lista in sorted(pe_canal.items(), key=lambda x: -len(x[1])):
    print(f"  {c:<10} {len(lista):>3} randuri")
necunoscut = [nr for nr, c in harta.items() if c == "NECUNOSCUT"]
if necunoscut:
    print(f"  link nerecunoscut: {necunoscut}")
print("scris:", OUT)
