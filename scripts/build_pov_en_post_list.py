r"""Construieste data/pov_en_post_list.json — pista povestitor in ENGLEZA.

Acelasi tipar ca `build_pov_post_list.py`, cu doua diferente care sunt tot
motivul pentru care e un fisier separat si nu un argument:

  - videoclipurile stau in ALT folder de Drive (`povestitor_en_drive_folder`),
    nu in cel romanesc. Un fisier cu acelasi NR exista in ambele foldere si are
    alt continut, deci potrivirea se face pe (folder + nume), niciodata pe nume.
  - descrierea vine din coloana **L** a lui Sheet1 (descriere engleza), nu din D.

Fara descriere nu se pune in plan — captionul ar iesi gol.

    server\.venv\Scripts\python.exe scripts\build_pov_en_post_list.py
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
from googleapiclient.discovery import build  # noqa: E402
from services.drive_upload import _resolve_credentials, extract_folder_id  # noqa: E402
from services.sheets import _service  # noqa: E402

OUT = _ROOT / "data" / "pov_en_post_list.json"
SHEET = targets.get("pov_sheet_id")
TAB = targets.get("pov_tab", "Sheet1")
NR_COL, DESC_EN_COL = 0, 11          # A si L
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+)|_part(\d+)of(\d+))?\.mp4$", re.I)
POSTED = "posted"


def nr_din_sheet(v):
    m = re.match(r"^(\d+)", (v or "").strip())
    return m.group(1) if m else None


vals = _service().spreadsheets().values().get(
    spreadsheetId=SHEET, range=f"'{TAB}'!A1:L400").execute().get("values", [])
desc_by_nr = {}
for r in vals[1:]:
    nr = nr_din_sheet(r[NR_COL] if len(r) > NR_COL else "")
    d = (r[DESC_EN_COL].strip() if len(r) > DESC_EN_COL and r[DESC_EN_COL] else "")
    if nr and d:
        desc_by_nr[nr] = d

creds, _, err = _resolve_credentials()
if not creds:
    raise SystemExit(f"Google Drive: {err}")
drive = build("drive", "v3", credentials=creds, cache_discovery=False)
root_id = extract_folder_id(targets.get("povestitor_en_drive_folder"))


def listeaza(fid):
    out, page = [], None
    while True:
        r = drive.files().list(
            q=f"'{fid}' in parents and trashed = false",
            fields="nextPageToken, files(id,name,mimeType,size,createdTime)",
            pageSize=1000, pageToken=page).execute()
        out += r.get("files", [])
        page = r.get("nextPageToken")
        if not page:
            return out


radacina = listeaza(root_id)
# `posted/` inseamna "predat pentru TikTok", nu "publicat peste tot": Facebook
# isi tine evidenta separat, deci fisierele de acolo raman in plan.
fisiere = [dict(f, unde="(root)") for f in radacina if f["name"].lower().endswith(".mp4")]
for f in radacina:
    if f["mimeType"].endswith("folder") and f["name"] == POSTED:
        fisiere += [dict(x, unde=POSTED) for x in listeaza(f["id"])
                    if x["name"].lower().endswith(".mp4")]

pe_nr, fara_desc = {}, set()
for f in fisiere:
    m = NAME_RE.match(f["name"])
    if not m:
        continue
    nr = m.group(1)
    if nr not in desc_by_nr:
        fara_desc.add(nr)
        continue
    pe_nr.setdefault(nr, []).append({
        "name": f["name"], "id": f["id"], "nr": nr,
        "part": int(m.group(2) or m.group(3) or 1),
        "created": (f.get("createdTime") or "")[:19],
        "mb": round(int(f.get("size") or 0) / 1048576) or None,
        "url": (f"https://drive.usercontent.google.com/download"
                f"?id={f['id']}&export=download&confirm=t"),
    })

plan = []
for nr in sorted(pe_nr, key=lambda n: min(x["created"] for x in pe_nr[n])):
    files = sorted(pe_nr[nr], key=lambda x: x["part"])
    for f in files:
        plan.append({**f, "parts": len(files), "desc": desc_by_nr[nr]})

OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"fisiere in folderul EN: {len(fisiere)}   in plan: {len(pe_nr)} videoclipuri "
      f"({len(plan)} fisiere)")
if fara_desc:
    print(f"sarite, fara descriere ENGLEZA in coloana L: {sorted(fara_desc, key=int)}")
multi = sorted({p["nr"] for p in plan if p["parts"] > 1}, key=int)
print(f"cu mai multe parti: {multi}")
print("scris:", OUT)
