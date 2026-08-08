"""Construieste data/pov_post_list.json — ce se poate posta din pista povestitor.

Sursa de adevar e Drive-ul (fisierele exista sau nu) plus descrierile din sheet.
Se citesc SI folderul principal SI `posted/`: `posted/` inseamna "predat pentru
TikTok", nu "publicat peste tot", deci un fisier mutat acolo poate fi inca
nepostat pe Facebook. Fiecare canal isi tine propria evidenta in
`post_povestitor.py`, asa ca planul trebuie sa le contina pe toate.

Se exclud:
  - fisierele din `_duplicate/` si `_inlocuite_de_parti/` (acelasi continut de
    doua ori);
  - NR-urile stiute ca NU sunt in romana, din data/pov_inventory.json (limba a
    fost citita din audio cu whisper, nu ghicita din nume);
  - orice rand fara descriere in sheet — captionul ar fi gol.

Ordinea e cea de creare pe Drive, cea mai veche intai. NR-ul NU e ordinea de
creare.
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

OUT = _ROOT / "data" / "pov_post_list.json"
INV = _ROOT / "data" / "pov_inventory.json"
SHEET = "1QESHMIoCgnaS7fOU5ynQ7wBQ-rmqP6gGnwPG7Zll0wM"
TAB = "Sheet1"
NR_COL, DESC_COL = 0, 3
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+)|_part(\d+)of(\d+))?\.mp4$", re.I)
SARITE = {"_duplicate", "_inlocuite_de_parti"}


def nr_din_sheet(v):
    m = re.match(r"^(\d+)", (v or "").strip())
    return m.group(1) if m else None


vals = _service().spreadsheets().values().get(
    spreadsheetId=SHEET, range=f"'{TAB}'!A1:D400").execute().get("values", [])
desc_by_nr = {}
for r in vals[1:]:
    nr = nr_din_sheet(r[NR_COL] if len(r) > NR_COL else "")
    d = (r[DESC_COL].strip() if len(r) > DESC_COL and r[DESC_COL] else "")
    if nr and d:
        desc_by_nr[nr] = d

# NR-uri care nu sunt in romana (verificate cu whisper pe audio)
non_ro = set()
if INV.exists():
    for r in json.loads(INV.read_text(encoding="utf-8")):
        if r.get("lang") and r["lang"] != "ro" and r.get("nr"):
            non_ro.add(str(r["nr"]))

creds, _, err = _resolve_credentials()
if not creds:
    raise SystemExit(f"Google Drive: {err}")
drive = build("drive", "v3", credentials=creds, cache_discovery=False)
root_id = extract_folder_id(targets.get("povestitor_drive_folder"))


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
fisiere = [dict(f, unde="(root)") for f in radacina if f["name"].lower().endswith(".mp4")]
for f in radacina:
    if f["mimeType"].endswith("folder") and f["name"] not in SARITE:
        fisiere += [dict(x, unde=f["name"]) for x in listeaza(f["id"])
                    if x["name"].lower().endswith(".mp4")]

pe_nr, fara_desc, engleza = {}, set(), set()
for f in fisiere:
    m = NAME_RE.match(f["name"])
    if not m:
        continue                       # nume vechi, needucat dupa NR
    nr = m.group(1)
    if nr in non_ro:
        engleza.add(nr)
        continue
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
print(f"fisiere pe Drive: {len(fisiere)}   in plan: {len(pe_nr)} videoclipuri "
      f"({len(plan)} fisiere)")
if engleza:
    print(f"sarite, NU sunt in romana: {sorted(engleza, key=int)}")
if fara_desc:
    print(f"sarite, fara descriere in sheet: {sorted(fara_desc, key=int)[:14]}")
print("scris:", OUT)
