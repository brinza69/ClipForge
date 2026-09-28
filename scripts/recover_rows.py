"""Recupereaza randuri ale caror videoclipuri exista pe Drive dar nu s-au scris in sheet.

Se intampla cand dispecerul moare intre terminarea jobului si writeback: fisierele
ajung pe Drive, dar coloanele F/G/H si descrierea raman goale, iar randul pare
nefacut. Re-randarea ar fi o risipa — fisierele sunt deja acolo.

Descrierea se reface din naratiunea ROMANEASCA deja randata (`captions.ass` al
proiectului), nu din sursa engleza, si NU costa credite ElevenLabs: doar
transcrierea exista deja, iar descrierea o scrie un LLM text.

    python scripts/recover_rows.py 233,234 [--write]
"""
import asyncio
import json
import os
import pathlib
import re
import sqlite3
import sys

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from services.drive_upload import list_folder_files  # noqa: E402
from services.sheets import _service, write_cell  # noqa: E402

SHEET = targets.get("pov_sheet_id")
TAB = "Sheet1"
ROLE_COLS = {"narator": "F", "comentator": "G", "povestitor": "H"}
DBS = {"data": _ROOT / "data" / "db" / "clipforge.db",
       "data_b": _ROOT / "data_b" / "db" / "clipforge.db"}
NAME_RE = re.compile(r"^(\d+)(?:_p\d+|_part\d+of\d+)?\.mp4$", re.I)


def nr_key(v):
    m = re.match(r"^(\d+)", (v or "").strip())
    return m.group(1) if m else None


def captions_text(nr, link):
    """Naratiunea romana a randului, luata din subtitrarile randate."""
    for d, db in DBS.items():
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=8)
            row = con.execute("SELECT id FROM projects WHERE source_url = ?", (link,)).fetchone()
            con.close()
        except Exception:  # noqa: BLE001
            continue
        if not row:
            continue
        for ass in sorted((_ROOT / d / "media" / row[0]).glob("v*/captions.ass")):
            txt = ass.read_text(encoding="utf-8", errors="replace")
            cuvinte = [re.sub(r"\{[^}]*\}", "", l.split(",,", 1)[1]).strip()
                       for l in txt.splitlines() if l.startswith("Dialogue:") and ",," in l]
            # subtitrarile repeta cuvintele (efect karaoke) — pastreaza ordinea, scoate dublurile
            out, vazut = [], set()
            for c in cuvinte:
                if c and c not in vazut:
                    vazut.add(c)
                    out.append(c)
            if out:
                return " ".join(out)
    return ""


async def descriere(text):
    from services.descriptions import generate_video_descriptions
    # nu avem descrierea originala a sursei — doar naratiunea randata conteaza
    r = await generate_video_descriptions(original_description="", transcript=text,
                                          engine="openai", target_language="ro")
    return ((r or {}).get("ai_generated") or "").strip()


def main():
    tinte = [s.strip() for s in sys.argv[1].split(",")] if len(sys.argv) > 1 else []
    write = "--write" in sys.argv
    if not tinte:
        raise SystemExit("foloseste: recover_rows.py 233,234 [--write]")

    vals = _service().spreadsheets().values().get(
        spreadsheetId=SHEET, range=f"'{TAB}'!A1:I400").execute().get("values", [])
    randuri = {}
    for i, r in enumerate(vals[1:], start=2):
        g = lambda k: (r[k].strip() if len(r) > k and r[k] else "")  # noqa: E731
        nr = nr_key(g(0))
        if nr in tinte:
            randuri[nr] = {"row": i, "link": g(1), "desc": g(3),
                           "F": g(5), "G": g(6), "H": g(7)}

    # ce exista pe Drive, pe rol
    pe_rol = {}
    for rol in ROLE_COLS:
        p = json.loads((_ROOT / "data" / "variant_presets" / f"{rol}.json")
                       .read_text(encoding="utf-8"))
        res = list_folder_files(p["drive_folder"])
        if res.get("status") != "ok":
            print(f"  {rol}: nu pot lista Drive ({res.get('status')})")
            continue
        d = {}
        for f in res["files"]:
            m = NAME_RE.match(f.get("name") or "")
            if m:
                d.setdefault(m.group(1), []).append(f)
        pe_rol[rol] = d

    for nr in tinte:
        info = randuri.get(nr)
        if not info:
            print(f"NR {nr}: nu e in sheet")
            continue
        print(f"\nNR {nr}  (rand {info['row']})")
        for rol, col in ROLE_COLS.items():
            fisiere = (pe_rol.get(rol) or {}).get(nr) or []
            are = info[col]
            if not fisiere:
                print(f"   {rol:11} lipseste de pe Drive")
                continue
            if are:
                print(f"   {rol:11} deja scris in sheet")
                continue
            linkuri = "\n".join(
                (f.get("download_url") or f.get("link") or "").strip() for f in fisiere)
            print(f"   {rol:11} {len(fisiere)} fisier(e) -> coloana {col}")
            if write:
                write_cell(SHEET, TAB, col, info["row"], linkuri)

        if info["desc"]:
            print("   descriere    deja exista")
            continue
        tx = captions_text(nr, info["link"])
        if not tx:
            print("   descriere    NU pot: nu gasesc subtitrarile randate")
            continue
        print(f"   descriere    din naratiune ({len(tx)} caractere)…")
        d = asyncio.run(descriere(tx))
        if not d:
            print("   descriere    generarea a esuat")
            continue
        print(f"   descriere    {d[:70]}…")
        if write:
            write_cell(SHEET, TAB, "D", info["row"], d)

    print("\n(fara --write) nimic scris" if not write else "\ngata")


if __name__ == "__main__":
    main()
