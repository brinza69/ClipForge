"""Raport complet: ce e randat, ce mai e de randat, ce e programat.

Trei intrebari intr-un singur loc, fiindca raspunsul lor sta in trei sisteme
diferite (Drive, sheet-uri, Buffer) si nu se pot deduce unul din altul.

    python scripts/status.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from buffer_api import channels, default_org, gql  # noqa: E402
from services.drive_upload import list_folder_files  # noqa: E402

PY = _ROOT / "server" / ".venv" / "Scripts" / "python.exe"
NR = re.compile(r"^(\d+)(?:_p\d+|_part\d+of\d+)?\.mp4$", re.I)


def numara(folder):
    """(fisiere mp4, NR-uri distincte) — inclusiv subfolderele, fiindca `posted/`
    tine deja o buna parte din arhiva si altfel ar lipsi din numaratoare."""
    res = list_folder_files(folder)
    if res.get("status") != "ok":
        return None, None
    fis, nrs, subf = [], set(), []
    for f in res["files"]:
        n = f.get("name") or ""
        if n.lower().endswith(".mp4"):
            fis.append(n)
            m = NR.match(n)
            if m:
                nrs.add(m.group(1))
        elif "." not in n:
            subf.append(f)
    for s in subf:
        r2 = list_folder_files(s["id"]) if s.get("id") else {}
        for f in (r2.get("files") or []):
            n = f.get("name") or ""
            if n.lower().endswith(".mp4"):
                fis.append(n)
                m = NR.match(n)
                if m:
                    nrs.add(m.group(1))
    return len(fis), len(nrs)


print("=" * 68)
print("1. RANDAT — ce exista pe Drive")
print("=" * 68)
total_f = 0
for rol in ("narator", "comentator", "povestitor"):
    p = json.loads((_ROOT / "data" / "variant_presets" / f"{rol}.json")
                   .read_text(encoding="utf-8"))
    f, n = numara(p["drive_folder"])
    if f is None:
        print(f"   {rol:12} Drive indisponibil")
        continue
    total_f += f
    print(f"   {rol:12} {f:>4} fisiere   {n:>3} videoclipuri")
f, n = numara(targets.get("fr_drive_folder"))
if f is not None:
    total_f += f
    print(f"   {'francez':12} {f:>4} fisiere   {n:>3} videoclipuri")
print(f"   {'TOTAL':12} {total_f:>4} fisiere")

print()
print("=" * 68)
print("2. DE RANDAT — randuri fara video")
print("=" * 68)
for eticheta, script, extra in (("romana", "dual_dispatch.py", []),
                                ("franceza", "herstory_dispatch.py", [])):
    try:
        r = subprocess.run([str(PY), str(_ROOT / "scripts" / script), "--dry"] + extra,
                           cwd=str(_ROOT), capture_output=True, text=True, timeout=420,
                           env={**os.environ, "CLIPFORGE_DISPATCH_MIN_ROW": "2",
                                "CLIPFORGE_PRESETS": "narator,comentator"})
        out = (r.stdout or "") + (r.stderr or "")
        if "PENDING:" in out:
            lst = out.split("PENDING:")[1].strip().splitlines()[0]
            n = len(json.loads(lst)) if lst.startswith("[") else 0
            print(f"   {eticheta:10} {n:>3} randuri")
        elif "de lucru:" in out:
            n = int(out.split("de lucru:")[1].split()[0])
            print(f"   {eticheta:10} {n:>3} randuri")
        else:
            print(f"   {eticheta:10} necunoscut")
    except Exception as e:  # noqa: BLE001
        print(f"   {eticheta:10} esuat: {str(e)[:60]}")

print()
print("=" * 68)
print("3. PROGRAMAT — in Buffer")
print("=" * 68)
org = default_org()
tot = 0
for c in sorted(channels(org), key=lambda x: x["name"]):
    r = gql("query P($i: PostsInput!){posts(input:$i,first:100){edges{node{dueAt}}}}",
            {"i": {"organizationId": org,
                   "filter": {"channelIds": [c["id"]], "status": ["scheduled"]}}})
    n = len(r["posts"]["edges"])
    tot += n
    zile = round(n / 3, 1) if n else 0
    print(f"   {c['name']:<24} {n:>3} postari  ≈{zile} zile la 3/zi")
print(f"   {'TOTAL':<24} {tot:>3}")
