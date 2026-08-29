r"""Scoate coperta de la inceputul clipurilor narator si le urca inapoi curate.

Omul a cerut clipurile fara card la inceput: "lasa-l normal". Coperta nu se
poate taia din fisierul de pe Drive fara sa reencodezi — asa ca nu se taie
nimic. Se porneste din nou de la ORIGINALUL din `data/muzica_backup/`, care n-a
avut niciodata coperta, se pune muzica peste el si se urca peste acelasi id de
Drive. Rezultatul e bit-identic cu ce ar fi iesit daca n-as fi lipit cardul.

De asta exista backup-ul: fara el, singura cale inapoi ar fi fost re-randarea
tuturor celor 15 fisiere, adica din nou creditele de voce.

Muzica RAMANE — omul a cerut doar coperta scoasa.

Verificarea e aceeasi ca la pus: amprenta fluxului video (numar de pachete +
profil/dimensiuni/fps) trebuie sa fie IDENTICA cu a originalului. La pus se
astepta un plus de 54 de cadre (cardul); aici se asteapta zero. Daca difera,
nu se urca.

    server\.venv\Scripts\python.exe scripts\scoate_coperta_narator.py [--dry] [--nr 280]
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
LUCRU = _ROOT / "data" / "coperti_lucru"
MELODII = _ROOT / "data" / "melodii.json"
FINALIZAT = _ROOT / "data" / "narator_finalizat.json"
CU_COPERTA = _ROOT / "data" / "coperti_narator_facute.json"
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+))?\.mp4$", re.I)

DRY = "--dry" in sys.argv
DOAR = sys.argv[sys.argv.index("--nr") + 1] if "--nr" in sys.argv else None


def _scrie(cale, date):
    cale.write_text(json.dumps(date, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    LUCRU.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(MELODII.read_text(encoding="utf-8"))
    fin = json.loads(FINALIZAT.read_text(encoding="utf-8")) if FINALIZAT.exists() else {}
    cu_cop = json.loads(CU_COPERTA.read_text(encoding="utf-8")) if CU_COPERTA.exists() else {}

    res = list_folder_files(targets.get("narator_drive_folder"))
    if res.get("status") != "ok":
        raise SystemExit("nu pot lista Drive: " + str(res.get("reason")))
    pe_nr = {}
    for f in res["files"]:
        m = NAME_RE.match(f.get("name") or "")
        if m:
            pe_nr.setdefault(m.group(1), []).append((int(m.group(2) or 1), f))

    drive = None
    if not DRY:
        creds, _, err = _resolve_credentials()
        if not creds:
            raise SystemExit("Google Drive: " + str(err))
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    curatate = 0
    for nr in sorted(pe_nr, key=int):
        if DOAR and nr != DOAR:
            continue
        stare = cfg["stari"].get(nr, cfg.get("implicit"))
        mel = cfg["melodii"][stare]
        for parte, f in sorted(pe_nr[nr]):
            nume = f["name"]
            are_coperta = f["id"] in cu_cop or (fin.get(f["id"]) or {}).get("coperta")
            if not are_coperta:
                print("  " + nume + ": n-are coperta, sar")
                continue
            orig = BACKUP / nume
            if not orig.exists():
                print("  " + nume + ": LIPSESTE originalul — sar")
                continue
            if DRY:
                print("  " + nume + ": as urca fara coperta, cu " +
                      pathlib.Path(mel["fisier"]).name + " " + str(mel["db"]) + "dB")
                continue
            print("\n  " + nume, flush=True)
            curat = LUCRU / ("curat_" + nume)
            try:
                n0, par0 = cop.amprenta(orig)
                mf.pune_muzica(orig, curat, mel["db"], pathlib.Path(mel["fisier"]))
                n1, par1 = cop.amprenta(curat)
                if (n1, par1) != (n0, par0):
                    print("     imaginea s-a schimbat: " + str(n0) + "->" + str(n1) +
                          " pachete, " + par0 + " -> " + par1 + " — nu urc")
                    continue
                _, mx = mf.volum(curat)
                print("     %.1fs, %d pachete ca in original, max %s dB"
                      % (cop.durata(curat), n1, mx))
                drive.files().update(fileId=f["id"],
                                     media_body=MediaFileUpload(str(curat), resumable=True),
                                     fields="id").execute()
                fin[f["id"]] = {"nume": nume, "stare": stare,
                                "melodie": pathlib.Path(mel["fisier"]).name,
                                "db": mel["db"], "coperta": None}
                cu_cop.pop(f["id"], None)
                _scrie(FINALIZAT, fin)
                _scrie(CU_COPERTA, cu_cop)
                curat.unlink(missing_ok=True)
                curatate += 1
                print("     urcat curat peste acelasi id", flush=True)
            except Exception as e:  # noqa: BLE001
                print("     ESUAT: " + str(e)[:250], flush=True)

    print("\ngata: " + str(curatate) + " fisiere fara coperta")


if __name__ == "__main__":
    main()
