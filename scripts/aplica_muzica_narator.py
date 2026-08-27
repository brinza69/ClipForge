r"""Pune muzica de fundal pe clipurile narator de la un NR in sus, pe Drive.

Fiecare fisier: se descarca, se amestecă melodia sub voce cu `-c:v copy` (deci
imaginea si encodarea raman bit-identice), si se URCA PESTE ACELASI id de Drive.
Peste acelasi id, nu ca fisier nou: asa linkurile din sheet si orice postare deja
programata raman valide.

Originalul descarcat NU se sterge — ramane in `data/muzica_backup/` pana cand
esti sigur ca nivelul e bun. Fara asta, o schimbare de parere ar insemna
re-randare de la zero.

Se sare peste fisierele care au deja muzica (tinute in `data/muzica_facute.json`),
deci rularea se poate relua oricand fara sa dubleze melodia peste ea insasi.

    server\.venv\Scripts\python.exe scripts\aplica_muzica_narator.py [--de-la 276] [--db -20] [--dry]
"""
import json
import os
import pathlib
import re
import sys
import urllib.request

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from services.drive_upload import _resolve_credentials, list_folder_files  # noqa: E402
from muzica_fundal import pune_muzica, volum  # noqa: E402

NAME_RE = re.compile(r"^(\d+)(?:_p(\d+))?\.mp4$", re.I)
STARE = _ROOT / "data" / "muzica_facute.json"
BACKUP = _ROOT / "data" / "muzica_backup"
LUCRU = _ROOT / "data" / "muzica_lucru"

DRY = "--dry" in sys.argv
DE_LA = int(sys.argv[sys.argv.index("--de-la") + 1]) if "--de-la" in sys.argv else 276
DB = float(sys.argv[sys.argv.index("--db") + 1]) if "--db" in sys.argv else -20.0

facute = json.loads(STARE.read_text(encoding="utf-8")) if STARE.exists() else {}
res = list_folder_files(targets.get("narator_drive_folder"))
if res.get("status") != "ok":
    raise SystemExit(f"nu pot lista Drive: {res.get('reason')}")

tinte = []
for f in res["files"]:
    m = NAME_RE.match(f.get("name") or "")
    if m and int(m.group(1)) >= DE_LA:
        tinte.append(f)
tinte.sort(key=lambda x: (int(NAME_RE.match(x["name"]).group(1)),
                          int(NAME_RE.match(x["name"]).group(2) or 1)))

de_facut = [f for f in tinte if f["id"] not in facute]
print(f"narator de la NR {DE_LA}: {len(tinte)} fisiere, {len(de_facut)} fara muzica, "
      f"nivel {DB} dB")
for f in de_facut:
    print(f"   {f['name']:<14} {round(f['size']/1048576)}MB")
if DRY:
    print("(--dry) nu ating nimic")
    raise SystemExit

BACKUP.mkdir(parents=True, exist_ok=True)
LUCRU.mkdir(parents=True, exist_ok=True)
creds, _, err = _resolve_credentials()
if not creds:
    raise SystemExit(f"Google Drive: {err}")
drive = build("drive", "v3", credentials=creds, cache_discovery=False)

for i, f in enumerate(de_facut, 1):
    nume, fid = f["name"], f["id"]
    orig = BACKUP / nume
    iesire = LUCRU / nume
    print(f"\n[{i}/{len(de_facut)}] {nume}", flush=True)
    try:
        if not orig.exists():
            urllib.request.urlretrieve(f["download_url"], orig)
        print(f"   descarcat {orig.stat().st_size // 1048576}MB", flush=True)
        pune_muzica(orig, iesire, DB)
        m0, x0 = volum(orig)
        m1, x1 = volum(iesire)
        print(f"   sunet: mean {m0} -> {m1} dB, max {x0} -> {x1} dB", flush=True)
        if x1 is not None and x1 > -0.1:
            print("   ATENTIE: varful e la limita, risc de saturare — nu urc, verifica")
            continue
        drive.files().update(fileId=fid,
                             media_body=MediaFileUpload(str(iesire), resumable=True),
                             fields="id,size").execute()
        facute[fid] = {"nume": nume, "db": DB}
        STARE.write_text(json.dumps(facute, ensure_ascii=False, indent=1), encoding="utf-8")
        iesire.unlink(missing_ok=True)
        print("   urcat peste acelasi id", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"   ESUAT: {str(e)[:200]}", flush=True)

print(f"\ngata: {len(facute)} fisiere cu muzica. Originalele in {BACKUP}")
