r"""Construieste data/narator_post_list.json — ce se poate posta pe narator.

Sursa: folderul narator de pe Drive plus descrierile ROMANESTI din coloana D a
lui Sheet1. Fisier separat de `build_pov_post_list.py` fiindca e alt folder si
alt canal — acelasi NR exista in patru foldere cu continut complet diferit, deci
potrivirea e pe (folder + nume), niciodata pe nume.

Ordinea ceruta pe 27 august: **de la NR 276 in sus, partea 1 intai**, spre cele
mai noi. Deci NR crescator, parti crescator — nu dupa data crearii, ca pe pista
franceza.

Pragul se schimba cu `--de-la N` (implicit 276). Randurile fara descriere sunt
raportate, nu inventate.

    server\.venv\Scripts\python.exe scripts\build_narator_post_list.py [--de-la 276]
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

OUT = _ROOT / "data" / "narator_post_list.json"
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+)|_part(\d+)of(\d+))?\.mp4$", re.I)
DE_LA = int(sys.argv[sys.argv.index("--de-la") + 1]) if "--de-la" in sys.argv else 276

vals = _service().spreadsheets().values().get(
    spreadsheetId=targets.get("pov_sheet_id"),
    range=f"'{targets.get('pov_tab', 'Sheet1')}'!A1:D400").execute().get("values", [])
desc_by_nr = {}
for r in vals[1:]:
    g = lambda k: (r[k].strip() if len(r) > k and r[k] else "")  # noqa: E731
    m = re.match(r"^(\d+)", g(0))
    if m and g(3):
        desc_by_nr[m.group(1)] = g(3)

res = list_folder_files(targets.get("narator_drive_folder"))
if res.get("status") != "ok":
    raise SystemExit(f"nu pot lista Drive: {res.get('reason')}")

pe_nr = {}
for f in res["files"]:
    m = NAME_RE.match(f.get("name") or "")
    if not m or int(m.group(1)) < DE_LA:
        continue
    pe_nr.setdefault(m.group(1), []).append({
        "name": f["name"], "id": f["id"],
        "part": int(m.group(2) or m.group(3) or 1),
        "created": f.get("created") or "",
        "mb": round(int(f.get("size") or 0) / 1048576) or None,
        "url": (f.get("download_url") or "").strip(),
    })

plan, fara_desc = [], []
for nr in sorted(pe_nr, key=int):          # NR crescator: 276 -> cele mai noi
    files = sorted(pe_nr[nr], key=lambda x: x["part"])
    d = desc_by_nr.get(nr, "")
    if not d:
        fara_desc.append(nr)
        continue
    for f in files:
        plan.append({**f, "nr": nr, "parts": len(files), "desc": d})

OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"narator, de la NR {DE_LA}: {len(pe_nr)} videoclipuri pe Drive   "
      f"in plan: {len({p['nr'] for p in plan})} ({len(plan)} fisiere)")
if fara_desc:
    print(f"SARITE, fara descriere in coloana D: {sorted(fara_desc, key=int)}")
multi = sorted({p["nr"] for p in plan if p["parts"] > 1}, key=int)
print(f"cu mai multe parti: {multi}")
print(f"la 4 postari/zi: {len(plan) / 4:.1f} zile")
print("scris:", OUT)
