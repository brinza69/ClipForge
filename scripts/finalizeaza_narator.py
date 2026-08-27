r"""Lantul complet pentru un clip narator: muzica + coperta, intr-o singura trecere.

Porneste de la ORIGINALUL fara muzica din `data/muzica_backup/`, nu de la ce e
pe Drive. Altfel, cand o poveste isi schimba melodia, s-ar amesteca a doua
melodie peste prima si n-ai mai putea scoate nimic.

Ordinea conteaza: intai muzica, apoi coperta. Cardul e mut, iar daca ar fi lipit
inainte, mixajul ar intinde melodia si peste el.

Encodarea nu se atinge: muzica se pune cu `-c:v copy`, cardul se encodeaza
singur cu aceiasi parametri ca randarea, iar lipirea e tot `-c copy`. Se
verifica pe pachetele fluxului video — daca nu sunt identice, nu se urca nimic.

Starea povestii (trista / energica) decide melodia, din `data/melodii.json`.
Ce nu e trecut explicit primeste `implicit` — asa un lot intreg poate merge pe o
singura melodie fara sa enumeri fiecare NR.

    server\.venv\Scripts\python.exe scripts\finalizeaza_narator.py [--dry] [--nr 282]
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
from googleapiclient.http import MediaFileUpload  # noqa: E402
from services.drive_upload import _resolve_credentials, list_folder_files  # noqa: E402
import muzica_fundal as mf  # noqa: E402
import aplica_coperta_narator as cop  # noqa: E402

BACKUP = _ROOT / "data" / "muzica_backup"
COPERTI = _ROOT / "data" / "coperti_narator"
LUCRU = _ROOT / "data" / "coperti_lucru"
MELODII = _ROOT / "data" / "melodii.json"
STARE = _ROOT / "data" / "narator_finalizat.json"
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+))?\.mp4$", re.I)

DRY = "--dry" in sys.argv
DOAR = sys.argv[sys.argv.index("--nr") + 1] if "--nr" in sys.argv else None


def main():
    LUCRU.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(MELODII.read_text(encoding="utf-8"))
    facute = json.loads(STARE.read_text(encoding="utf-8")) if STARE.exists() else {}

    imagini = {}
    for p in sorted(COPERTI.glob("*")):
        m = re.match(r"^(\d+)\.(png|jpg|jpeg)$", p.name, re.I)
        if m:
            imagini[m.group(1)] = p

    res = list_folder_files(targets.get("narator_drive_folder"))
    if res.get("status") != "ok":
        raise SystemExit("nu pot lista Drive: " + str(res.get("reason")))
    pe_nr = {}
    for f in res["files"]:
        m = NAME_RE.match(f.get("name") or "")
        if m and (m.group(1) in cfg["stari"] or cfg.get("implicit")):
            pe_nr.setdefault(m.group(1), []).append((int(m.group(2) or 1), f))

    drive = None
    if not DRY:
        creds, _, err = _resolve_credentials()
        if not creds:
            raise SystemExit("Google Drive: " + str(err))
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    for nr in sorted(pe_nr, key=int):
        if DOAR and nr != DOAR:
            continue
        # `exclus`: povesti care NU se mai ating. 276 publica in seara asta, iar
        # o re-urcare in timp ce Buffer descarca fisierul ar servi ceva pe
        # jumatate scris.
        if nr in cfg.get("exclus", []):
            print("  NR " + nr + ": exclus, nu-l ating")
            continue
        stare = cfg["stari"].get(nr, cfg.get("implicit"))
        mel = cfg["melodii"][stare]
        parti = sorted(pe_nr[nr])
        total = len(parti)
        for parte, f in parti:
            nume = f["name"]
            et = nume + " [" + stare + " " + str(mel["db"]) + "dB]"
            if f["id"] in facute:
                print("  " + et + ": gata deja, sar")
                continue
            orig = BACKUP / nume
            if not orig.exists():
                print("  " + et + ": LIPSESTE originalul " + str(orig) + " — sar")
                continue
            if nr not in imagini:
                print("  " + et + ": lipseste coperta pentru NR " + nr + " — sar")
                continue
            if DRY:
                print("  " + et + " + coperta " + imagini[nr].name)
                continue
            print("\n  " + et, flush=True)
            cu_muzica = LUCRU / ("m_" + nume)
            card = LUCRU / ("card_" + nr + "_" + str(parte) + ".mp4")
            out = LUCRU / ("gata_" + nume)
            try:
                n0, par0 = cop.amprenta(orig)
                mf.pune_muzica(orig, cu_muzica, mel["db"], pathlib.Path(mel["fisier"]))
                nm, parm = cop.amprenta(cu_muzica)
                if (nm, parm) != (n0, par0):
                    print("     mixajul a atins imaginea — nu urc")
                    continue
                cop.fa_card(imagini[nr], card, parte, total)
                cop.lipeste(card, cu_muzica, out)
                n1, par1 = cop.amprenta(out)
                cadre = round(cop.DURATA * cop.FPS)
                if par1 != par0 or abs(n1 - n0 - cadre) > 2:
                    print("     lipirea a iesit gresit: " + str(n0) + "->" + str(n1) +
                          " pachete, " + par0 + " -> " + par1 + " — nu urc")
                    continue
                d0, d1 = cop.durata(orig), cop.durata(out)
                m1, x1 = mf.volum(out)
                print("     %.1fs -> %.1fs (+%.2fs), max %s dB, imagine neatinsa"
                      % (d0, d1, d1 - d0, x1))
                drive.files().update(fileId=f["id"],
                                     media_body=MediaFileUpload(str(out), resumable=True),
                                     fields="id").execute()
                facute[f["id"]] = {"nume": nume, "stare": stare,
                                   "melodie": pathlib.Path(mel["fisier"]).name,
                                   "db": mel["db"], "coperta": imagini[nr].name}
                STARE.write_text(json.dumps(facute, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
                for x in (cu_muzica, card, out):
                    x.unlink(missing_ok=True)
                print("     urcat peste acelasi id", flush=True)
            except Exception as e:  # noqa: BLE001
                print("     ESUAT: " + str(e)[:250], flush=True)

    print("\ngata: " + str(len(facute)) + " fisiere finalizate")


if __name__ == "__main__":
    main()
