r"""Alimenteaza pista franceza (Contouse) cu cele mai vizionate shorts de la Varizz.

Canalul are ~4800 de shorts. Ordinea implicita a tabului `/shorts` e cronologica,
deci nu spune nimic despre popularitate. Aici se ia listarea INTREAGA cu
`--flat-playlist` (78 de secunde pentru tot canalul, fiindca nu descarca nimic,
doar metadate) si se sorteaza dupa `view_count` — asta reproduce fila „Popular"
din interfata, dar cu cifrele exacte, nu cu ordinea afisata.

Verificat: primele sase ies 159M, 113M, 102M, 73M, 70M, 53M — exact ce arata
fila „Popular" pe 30 aug 2026.

Deduplicarea si scrierea NU se rescriu aici: se cheama `add_links.py fr
--fisier`, care verifica id-urile din AMANDOUA sheet-urile (un clip folosit pe
pista romana nu se reface pe cea franceza), rezolva linkurile scurte si umple
intai randurile pre-numerotate fara link. Aici se face doar alegerea.

RUTINA (`--bucla`): la fiecare trecere adauga cate `--cate` linkuri noi, apoi
asteapta. Se opreste singur cand nu mai are ce adauga — ori canalul s-a epuizat,
ori restul e sub pragul de vizualizari. Praguri:

  --minim 1000000   sub un milion de vizualizari nu se mai ia (implicit)
  --cate 100        cate se adauga la o trecere

    server\.venv\Scripts\python.exe scripts\varizz_populare.py [--cate 100] [--dry]
    server\.venv\Scripts\python.exe scripts\varizz_populare.py --bucla
"""
import json
import os
import pathlib
import subprocess
import sys
import time

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from add_links import id_uri_din, rezolva  # noqa: E402
from services import sheets_config as _scfg  # noqa: E402

CANAL = "https://www.youtube.com/@Varizzz/shorts"
PY = str(_ROOT / "server" / ".venv" / "Scripts" / "python.exe")
CACHE = _ROOT / "data" / "varizz_shorts.json"
LUCRU = _ROOT / "data" / "varizz_de_adaugat.txt"
_CF = 0x08000000 if os.name == "nt" else 0

CATE = int(sys.argv[sys.argv.index("--cate") + 1]) if "--cate" in sys.argv else 100
MINIM = int(sys.argv[sys.argv.index("--minim") + 1]) if "--minim" in sys.argv else 1_000_000
DRY = "--dry" in sys.argv
BUCLA = "--bucla" in sys.argv
PAUZA = 12 * 3600      # canalul publica zilnic; de doua ori pe zi e destul
CACHE_ORE = 12


def listeaza(fortat=False):
    """(id, vizualizari, titlu) pentru tot canalul. Cache-uit: listarea dureaza
    ~80s si nu se schimba de la o ora la alta."""
    if CACHE.exists() and not fortat:
        varsta = (time.time() - CACHE.stat().st_mtime) / 3600
        if varsta < CACHE_ORE:
            return json.loads(CACHE.read_text(encoding="utf-8"))
    print("listez canalul (dureaza ~80s, nu descarca nimic)...", flush=True)
    p = subprocess.run(
        [PY, "-m", "yt_dlp", "--flat-playlist", "--no-warnings",
         "--print", "%(id)s|%(view_count)s|%(title).90s", CANAL],
        capture_output=True, text=True, timeout=900, creationflags=_CF)
    randuri = []
    for ln in (p.stdout or "").splitlines():
        parti = ln.split("|", 2)
        if len(parti) == 3 and parti[0] and parti[1].isdigit():
            randuri.append({"id": parti[0], "views": int(parti[1]), "titlu": parti[2]})
    if not randuri:
        raise SystemExit("listarea n-a intors nimic:\n" + (p.stderr or "")[-400:])
    CACHE.write_text(json.dumps(randuri, ensure_ascii=False), encoding="utf-8")
    return randuri


def folosite():
    """Id-urile deja trimise, din AMANDOUA sheet-urile."""
    cfg = _scfg.load() or {}
    ids, scurte = id_uri_din(cfg.get("spreadsheet_id"), "Sheet1!A1:C400")
    a, b = id_uri_din(targets.get("fr_sheet_id"),
                      "'%s'!A1:F400" % targets.get("fr_tab", "Victoria"))
    return (ids | a | rezolva(scurte + b))


def o_trecere():
    randuri = sorted(listeaza(), key=lambda x: -x["views"])
    deja = folosite()
    noi = [r for r in randuri if r["id"] not in deja and r["views"] >= MINIM][:CATE]
    peste_prag = sum(1 for r in randuri if r["views"] >= MINIM and r["id"] not in deja)
    print("canal: %d shorts | peste %s vizualizari si nefolosite: %d | iau %d"
          % (len(randuri), "{:,}".format(MINIM), peste_prag, len(noi)), flush=True)
    if not noi:
        print("nimic de adaugat — ori s-a epuizat canalul, ori restul e sub prag.")
        return 0
    print("  primul: %s (%s vizualizari)" % (noi[0]["titlu"][:56], "{:,}".format(noi[0]["views"])))
    print("  ultimul: %s (%s vizualizari)" % (noi[-1]["titlu"][:56], "{:,}".format(noi[-1]["views"])))
    LUCRU.write_text("\n".join("https://www.youtube.com/shorts/" + r["id"] for r in noi),
                     encoding="utf-8")
    cmd = [PY, str(_ROOT / "scripts" / "add_links.py"), "fr", "--fisier", str(LUCRU)]
    if not DRY:
        cmd.append("--write")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, creationflags=_CF)
    for ln in (p.stdout or "").splitlines()[-4:]:
        print("  " + ln)
    if p.returncode != 0:
        print("  add_links a esuat: " + (p.stderr or "")[-300:])
        return 0
    return len(noi)


def main():
    if not BUCLA:
        o_trecere()
        return
    print(time.strftime("%d %b %H:%M") + " rutina Varizz -> Contouse "
          "(cate %d, prag %s vizualizari, verific la 12h)"
          % (CATE, "{:,}".format(MINIM)), flush=True)
    gol = 0
    while True:
        try:
            n = o_trecere()
        except Exception as e:  # noqa: BLE001
            print("trecere esuata: " + str(e)[:200], flush=True)
            n = 0
        if n == 0:
            gol += 1
            if gol == 2:
                print("nimic nou de doua treceri la rand — canalul e epuizat peste prag.",
                      flush=True)
        else:
            gol = 0
        time.sleep(PAUZA)


if __name__ == "__main__":
    main()
