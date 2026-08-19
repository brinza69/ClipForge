"""Adauga linkuri noi intr-un sheet, verificand ca n-au mai fost trimise.

    python scripts/add_links.py ro  <url> <url> ...   [--write]
    python scripts/add_links.py fr  --fisier lista.txt [--write]

Deduplicarea se face pe ID-ul videoclipului, nu pe URL: acelasi clip poate veni
ca link lung, ca link scurt sau cu parametri (`?t=5&feature=share`) si ar parea
de fiecare data altul. Se verifica in AMANDOUA sheet-urile — un clip folosit pe
pista franceza nu trebuie refacut pe cea romana.

Randurile pre-numerotate dar fara link se umplu primele: NR-ul e numele
fisierului pe Drive, deci renumerotarea ar strica deduplicarea de la randare.
"""
import os
import pathlib
import re
import sys

os.environ.setdefault("CLIPFORGE_DATA_DIR", "data")
_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))

import targets  # noqa: E402
from services import sheets_config as _scfg  # noqa: E402
from services.sheets import _service, write_cell  # noqa: E402

YT = re.compile(r"(?:youtube\.com/(?:shorts/|watch\?v=|live/)|youtu\.be/)([A-Za-z0-9_-]{6,})")
TT = re.compile(r"tiktok\.com/@[\w.]+/video/(\d+)")
SCURT = re.compile(r"(?:vm|vt)\.tiktok\.com/[A-Za-z0-9]+")


def vid(u):
    for rx in (YT, TT):
        m = rx.search(u or "")
        if m:
            return m.group(1)
    return None


def id_uri_din(sid, rng):
    vals = _service().spreadsheets().values().get(
        spreadsheetId=sid, range=rng).execute().get("values", [])
    ids, scurte = set(), []
    for r in vals:
        for cell in r:
            if not isinstance(cell, str) or "tiktok.com" not in cell and "youtu" not in cell:
                continue
            v = vid(cell)
            if v:
                ids.add(v)
            elif SCURT.search(cell):
                scurte.append(SCURT.search(cell).group(0))
    return ids, scurte


def rezolva(scurte):
    """Un link scurt nu contine id-ul — fara rezolvare, un clip deja folosit pare nou."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    def unul(u):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return vid(r.geturl())
        except Exception:  # noqa: BLE001
            return None

    if not scurte:
        return set()
    with ThreadPoolExecutor(max_workers=8) as ex:
        return {v for v in ex.map(unul, scurte) if v}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("ro", "fr"):
        raise SystemExit("foloseste: add_links.py ro|fr <url>... [--write]")
    pista = sys.argv[1]
    write = "--write" in sys.argv

    linkuri = [a for a in sys.argv[2:] if a.startswith("http")]
    if "--fisier" in sys.argv:
        f = pathlib.Path(sys.argv[sys.argv.index("--fisier") + 1])
        linkuri += [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
                    if ln.strip().startswith("http")]
    if not linkuri:
        raise SystemExit("niciun link dat")

    if pista == "ro":
        cfg = _scfg.load() or {}
        sid, tab, rng = cfg.get("spreadsheet_id"), cfg.get("tab", "Sheet1"), "A1:B400"
    else:
        sid, tab, rng = targets.get("fr_sheet_id"), targets.get("fr_tab", "Victoria"), "A1:F400"

    # id-uri deja folosite, din AMANDOUA sheet-urile
    cfg = _scfg.load() or {}
    folosite, scurte = id_uri_din(cfg.get("spreadsheet_id"), "Sheet1!A1:C400")
    a, b = id_uri_din(targets.get("fr_sheet_id"),
                      f"'{targets.get('fr_tab', 'Victoria')}'!A1:F400")
    folosite |= a
    scurte += b
    folosite |= rezolva(scurte)
    print(f"id-uri deja folosite: {len(folosite)} (din ambele sheet-uri, "
          f"inclusiv {len(scurte)} linkuri scurte rezolvate)")

    vals = _service().spreadsheets().values().get(
        spreadsheetId=sid, range=f"'{tab}'!{rng}").execute().get("values", [])
    libere, max_nr, ultim = [], 0, 1
    for i, r in enumerate(vals[1:], start=2):
        nr = (r[0].strip() if len(r) > 0 and r[0] else "")
        link = (r[1].strip() if len(r) > 1 and r[1] else "")
        m = re.match(r"^(\d+)", nr)
        if m:
            max_nr = max(max_nr, int(m.group(1)))
        if link:
            ultim = i
        else:
            libere.append((i, nr))
    # Doar golurile de DUPA ultimul rand cu link. Un gol din mijlocul sheet-ului
    # are un NR vechi, care poate avea deja fisiere pe Drive — atunci randul nou
    # ar fi considerat gata si sarit. Si tine lotul nou grupat la coada, ca sa se
    # poata porni randarea exact de la el.
    libere = [(i, nr) for i, nr in libere if i > ultim]

    # Linkurile scurte (partajare de pe telefon) nu contin id-ul, deci nici nu se
    # pot verifica de dubluri si nici nu se pot compara intre ele. Se rezolva
    # ACUM, si in sheet intra forma lunga.
    lungi = []
    for u in linkuri:
        if SCURT.search(u) and not vid(u):
            import urllib.request
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    final = r.geturl()
                if vid(final):
                    print(f"  rezolvat {u} -> {final.split('?')[0]}")
                    lungi.append(final)
                    continue
            except Exception as e:  # noqa: BLE001
                print(f"  NU pot rezolva {u}: {str(e)[:60]}")
                continue
            print(f"  NU pot rezolva {u} (nu gasesc id in {final[:60]})")
            continue
        lungi.append(u)
    linkuri = lungi

    plan, nr = [], max_nr
    for u in linkuri:
        v = vid(u)
        if not v:
            print(f"  SAR: nu extrag id din {u[:60]}")
            continue
        if v in folosite:
            print(f"  DEJA TRIMIS: {u[:64]}")
            continue
        folosite.add(v)
        if libere:
            rand, nr_ex = libere.pop(0)
            nr_final = nr_ex if nr_ex.isdigit() else str(nr := nr + 1)
        else:
            nr += 1
            rand, nr_final = ultim + len(plan) + 1, str(nr)
        plan.append({"row": rand, "nr": nr_final, "url": u.split("?")[0]})

    for p in plan:
        print(f"  rand {p['row']:>4}  NR {p['nr']:<5} {p['url']}")
    print(f"\n{len(plan)} de adaugat, {len(linkuri) - len(plan)} sarite")
    if not plan:
        return
    if not write:
        print("(fara --write) nimic scris")
        return
    for p in plan:
        write_cell(sid, tab, "A", p["row"], p["nr"])
        write_cell(sid, tab, "B", p["row"], p["url"])
    print(f"scris: {len(plan)} randuri  (primul rand nou: {plan[0]['row']})")


if __name__ == "__main__":
    main()
