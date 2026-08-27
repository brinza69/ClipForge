r"""Tine placa B ocupata: cand un lot se termina, trece singur la urmatorul.

Randurile din sheet vin in loturi consecutive cu roluri diferite — 285-294 cer
narator+comentator, 295-302 cer povestitor — iar `dual_dispatch.py` are UN
singur set de presete si o singura fereastra de randuri
(`CLIPFORGE_DISPATCH_MIN_ROW` / `MAX_ROW`). Fara supraveghetor, dispecerul
termina lotul curent si placa sta degeaba pana vine cineva sa schimbe
configuratia de mana.

Aici se verifica periodic daca lotul curent mai are ceva de facut: un rand e
gata cand TOATE coloanele lui de rol sunt completate. Cand nu mai e nimic si
niciun job nu ruleaza, se scrie lotul urmator in `rig_state.json` si se
reporneste rigul, care ridica dispecerul cu noua configuratie.

Loturile stau in `data/loturi.json`, in ordinea in care trebuie facute.
Ultimul lot terminat = nu mai are ce avansa; scriptul o spune si iese.

    server\.venv\Scripts\python.exe scripts\avanseaza_loturi.py [--dry]
"""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))
sys.path.insert(0, str(_ROOT / "scripts"))
os.environ.setdefault("CLIPFORGE_DATA_DIR", str(_ROOT / "data"))

import targets  # noqa: E402
from services.sheets import _service  # noqa: E402

DRY = "--dry" in sys.argv
LOTURI = _ROOT / "data" / "loturi.json"
STARE = _ROOT / "data" / "rig_state.json"
BACKEND_B = "http://127.0.0.1:8421"
PAUZA = 600           # 10 minute: un rand dureaza ~20-30, deci nu are rost mai des
COL = {c: i for i, c in enumerate("ABCDEFGHIJKL")}


def spune(m):
    print(f"{time.strftime('%d %b %H:%M')} {m}", flush=True)


def randuri_gata(lot):
    """(ramase, total) pentru lotul asta: un rand e gata cand toate coloanele
    lui de rol au link. Se citeste sheet-ul, nu Drive-ul — sheet-ul e ce
    consulta si dispecerul ca sa decida."""
    v = _service().spreadsheets().values().get(
        spreadsheetId=targets.get("pov_sheet_id"),
        range=f"'{targets.get('pov_tab', 'Sheet1')}'!A1:L400").execute().get("values", [])
    ramase, total = 0, 0
    for i, r in enumerate(v, start=1):
        if not (lot["min_row"] <= i <= lot["max_row"]):
            continue
        g = lambda k: (r[k].strip() if len(r) > k and r[k] else "")  # noqa: E731
        if not g(1).startswith("http"):
            continue
        total += 1
        if not all(g(COL[c]) for c in lot["coloane"]):
            ramase += 1
    return ramase, total


def ocupat():
    try:
        with urllib.request.urlopen(BACKEND_B + "/api/jobs/?status=running,queued",
                                    timeout=15) as r:
            return bool(json.loads(r.read()))
    except Exception:  # noqa: BLE001
        return True       # daca nu stiu, presupun ocupat si nu misc nimic


def lot_curent(loturi):
    st = json.loads(STARE.read_text(encoding="utf-8")) if STARE.exists() else {}
    mn = str(st.get("CLIPFORGE_DISPATCH_MIN_ROW", ""))
    for i, l in enumerate(loturi):
        if str(l["min_row"]) == mn:
            return i
    return None


def treci_la(lot):
    py = str(_ROOT / "server" / ".venv" / "Scripts" / "python.exe")
    subprocess.run([py, str(_ROOT / "scripts" / "rig_state.py"), "set",
                    "--presets", lot["presets"],
                    "--min-row", str(lot["min_row"]),
                    "--max-row", str(lot["max_row"])], check=True)
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", str(_ROOT / "scripts" / "rig_boot.ps1")])


def main():
    loturi = json.loads(LOTURI.read_text(encoding="utf-8"))
    while True:
        i = lot_curent(loturi)
        if i is None:
            spune("configuratia curenta nu corespunde niciunui lot din loturi.json — nu ating nimic")
            return
        ramase, total = randuri_gata(loturi[i])
        spune(f"lot {i + 1}/{len(loturi)} «{loturi[i]['nume']}»: {ramase}/{total} ramase")
        if DRY:
            # o singura trecere: `--dry` e pentru "spune-mi unde stam", nu
            # pentru a sta o zi intr-o bucla care nu schimba nimic
            spune("(--dry) o singura verificare, nu astept si nu schimb nimic")
            return
        if ramase == 0:
            if i + 1 >= len(loturi):
                spune("toate loturile sunt gata — nu mai am ce avansa")
                return
            if ocupat():
                spune("lotul e gata dar placa B inca lucreaza — astept sa termine")
            else:
                urm = loturi[i + 1]
                spune(f"AVANSEZ la «{urm['nume']}» (presete {urm['presets']}, "
                      f"randuri {urm['min_row']}-{urm['max_row']})")
                treci_la(urm)
                time.sleep(60)
        time.sleep(PAUZA)


if __name__ == "__main__":
    main()
