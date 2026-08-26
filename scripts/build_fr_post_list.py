"""Construieste data/fr_post_list.json — ce se poate posta pe canalul francez.

Sursa de adevar e folderul Victoria de pe Drive (fisierele exista sau nu),
combinat cu descrierile franceze din sheet. Coloana STATUS a sheet-ului NU e de
incredere pentru "s-a postat": primele videoclipuri au fost puse manual si nu au
trecut niciodata prin scriptul care scrie acolo. Ce s-a dat efectiv lui Buffer se
citeste din Buffer, in `post_povestitor.py --channel franceza`.

Fara descriere nu se posteaza — captionul ar fi gol.
"""
import json
import os
import pathlib
import re
import sys

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from services.drive_upload import list_folder_files  # noqa: E402
from services.sheets import _service  # noqa: E402

OUT = _ROOT / "data" / "fr_post_list.json"
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+)|_part(\d+)of(\d+))?\.mp4$", re.I)

# descrierile din sheet
vals = _service().spreadsheets().values().get(
    spreadsheetId=targets.get("fr_sheet_id"),
    range=f"'{targets.get('fr_tab', 'Victoria')}'!A1:F400").execute().get("values", [])
desc_by_nr = {}
for r in vals[1:]:
    g = lambda k: (r[k].strip() if len(r) > k and r[k] else "")  # noqa: E731
    if g(0):
        desc_by_nr[g(0)] = g(3)

# Ordinea ceruta pe 25 aug 2026: intai tot ce vine de la Varizz, pana se
# epuizeaza, apoi HerStory. Harta o face `surse_franceza.py`; un NR care lipseste
# din ea (link adaugat intre timp) cade la coada si e raportat, ca sa nu treaca
# neobservat pe locul gresit.
SURSE = _ROOT / "data" / "surse_franceza.json"
surse = json.loads(SURSE.read_text(encoding="utf-8")) if SURSE.exists() else {}
PRIORITATE = {"Varizz": 0, "HerStory": 1}


def ordine(nr):
    return (PRIORITATE.get(surse.get(nr), 2), int(nr))


res = list_folder_files(targets.get("fr_drive_folder"))
if res.get("status") != "ok":
    raise SystemExit(f"nu pot lista Drive: {res.get('reason')}")

by_nr = {}
for f in res["files"]:
    m = NAME_RE.match(f.get("name") or "")
    if not m:
        continue
    nr = m.group(1)
    part = int(m.group(2) or m.group(3) or 1)
    by_nr.setdefault(nr, []).append({
        "name": f["name"], "id": f["id"], "part": part,
        "url": (f.get("download_url") or f.get("link") or "").strip(),
        "mb": round(int(f.get("size") or 0) / 1048576) or None,
        # data randarii — ordinea ceruta pe canale e "ultimele facute primele",
        # iar NR-ul nu spune nimic despre cand a fost facut clipul
        "created": f.get("created") or "",
    })

plan, fara_desc = [], []
for nr in sorted(by_nr, key=ordine):
    files = sorted(by_nr[nr], key=lambda x: x["part"])
    d = desc_by_nr.get(nr, "")
    if not d:
        fara_desc.append(nr)
        continue
    for f in files:
        plan.append({**f, "nr": nr, "parts": len(files), "desc": d})

OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"videoclipuri pe Drive: {len(by_nr)}   in plan: "
      f"{len({p['nr'] for p in plan})} ({len(plan)} fisiere)")
if fara_desc:
    print(f"sarite, fara descriere in sheet: {fara_desc}")
pe_sursa = {}
for nr in dict.fromkeys(p["nr"] for p in plan):
    pe_sursa.setdefault(surse.get(nr, "NECLASIFICAT"), []).append(nr)
for canal, lista in sorted(pe_sursa.items(), key=lambda x: PRIORITATE.get(x[0], 2)):
    print(f"  {canal:<13} {len(lista):>3}  primele: {lista[:6]}")
if "NECLASIFICAT" in pe_sursa:
    print("  ^ ruleaza scripts/surse_franceza.py ca sa le aseze corect")

multi = sorted({p["nr"] for p in plan if p["parts"] > 1}, key=int)
print(f"cu mai multe parti: {multi}")
print("scris:", OUT)
