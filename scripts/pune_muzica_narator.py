r"""Pune melodia de fundal pe clipurile narator care inca n-o au.

`finalizeaza_narator.py` porneste din `data/muzica_backup/`, si acolo sunt doar
povestile 276-283 — cele randate in ziua in care s-a introdus muzica. Clipurile
randate dupa aceea n-au backup, deci scriptul acela nu le poate atinge.

Aici originalul se ia de pe Drive. E sigur, fiindca fisierele astea n-au primit
niciodata coperta si n-au muzica: ce e pe Drive ESTE originalul. Se salveaza in
`muzica_backup/` inainte de orice, ca de-acum incolo sa existe si pentru ele o
cale inapoi fara re-randare.

Nu se ating cele deja trecute in `narator_finalizat.json`: a doua trecere ar
amesteca a doua oara melodia peste prima.

Implicit se lucreaza de la NR 284 in sus. Pe Drive sunt 174 de fisiere, din care
~140 publicate demult — a le redescarca si reurca pe toate ar insemna zeci de GB
pentru clipuri pe care nu le mai vede nimeni.

Cu `--bucla` ramane pornit si verifica din cand in cand, ca `umple_coada_narator`.
Fara el, un lot proaspat randat (NR 299-304 e in coada) ar ajunge pe Drive fara
melodie si posterul l-ar programa asa — diferit de toate cele 29 publicate.

    server\.venv\Scripts\python.exe scripts\pune_muzica_narator.py [--dry] [--de-la 284]
    server\.venv\Scripts\python.exe scripts\pune_muzica_narator.py --bucla
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

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
NAME_RE = re.compile(r"^(\d+)(?:_p(\d+))?\.mp4$", re.I)

DRY = "--dry" in sys.argv
BUCLA = "--bucla" in sys.argv
DE_LA = int(sys.argv[sys.argv.index("--de-la") + 1]) if "--de-la" in sys.argv else 284
PAUZA = 3 * 3600        # cat la umple_coada_narator: randarea unui lot tine ore


def main():
    LUCRU.mkdir(parents=True, exist_ok=True)
    BACKUP.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(MELODII.read_text(encoding="utf-8"))
    fin = json.loads(FINALIZAT.read_text(encoding="utf-8")) if FINALIZAT.exists() else {}

    res = list_folder_files(targets.get("narator_drive_folder"))
    if res.get("status") != "ok":
        raise SystemExit("nu pot lista Drive: " + str(res.get("reason")))
    de_facut = []
    for f in res["files"]:
        m = NAME_RE.match(f.get("name") or "")
        if m and int(m.group(1)) >= DE_LA and f["id"] not in fin:
            de_facut.append((int(m.group(1)), int(m.group(2) or 1), f))
    de_facut.sort()
    print("de pus muzica: " + str(len(de_facut)) + " fisiere, de la NR " + str(DE_LA))

    drive = None
    if not DRY:
        creds, _, err = _resolve_credentials()
        if not creds:
            raise SystemExit("Google Drive: " + str(err))
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    gata = 0
    for nr_i, _parte, f in de_facut:
        nr, nume = str(nr_i), f["name"]
        stare = cfg["stari"].get(nr, cfg.get("implicit"))
        mel = cfg["melodii"][stare]
        if DRY:
            print("  " + nume + ": " + pathlib.Path(mel["fisier"]).name +
                  " " + str(mel["db"]) + "dB")
            continue
        print("\n  " + nume + " [" + stare + "]", flush=True)
        orig = BACKUP / nume
        cu_muzica = LUCRU / ("m_" + nume)
        try:
            if not orig.exists():
                urllib.request.urlretrieve(f["download_url"], orig)
                print("     original salvat in muzica_backup/", flush=True)
            n0, par0 = cop.amprenta(orig)
            mf.pune_muzica(orig, cu_muzica, mel["db"], pathlib.Path(mel["fisier"]))
            n1, par1 = cop.amprenta(cu_muzica)
            if (n1, par1) != (n0, par0):
                print("     mixajul a atins imaginea: " + str(n0) + "->" + str(n1) +
                      " pachete, " + par0 + " -> " + par1 + " — nu urc")
                continue
            _, mx = mf.volum(cu_muzica)
            print("     %.1fs, %d pachete ca in original, max %s dB"
                  % (cop.durata(cu_muzica), n1, mx))
            drive.files().update(fileId=f["id"],
                                 media_body=MediaFileUpload(str(cu_muzica), resumable=True),
                                 fields="id").execute()
            fin[f["id"]] = {"nume": nume, "stare": stare,
                            "melodie": pathlib.Path(mel["fisier"]).name,
                            "db": mel["db"], "coperta": None}
            FINALIZAT.write_text(json.dumps(fin, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            cu_muzica.unlink(missing_ok=True)
            gata += 1
            print("     urcat peste acelasi id", flush=True)
        except Exception as e:  # noqa: BLE001
            print("     ESUAT: " + str(e)[:250], flush=True)

    print("\ngata: " + str(gata) + " fisiere cu muzica")


if __name__ == "__main__":
    if BUCLA and not DRY:
        print(time.strftime("%d %b %H:%M") + " supraveghez muzica narator "
              "(de la NR " + str(DE_LA) + ", verific la 3h)", flush=True)
        while True:
            # O trecere care pica nu opreste supravegherea: Drive-ul da 401 cand
            # expira tokenul, iar aia se repara singur dupa reconectare.
            try:
                main()
            except Exception as e:  # noqa: BLE001
                print(time.strftime("%d %b %H:%M") + " trecere esuata: "
                      + str(e)[:200], flush=True)
            time.sleep(PAUZA)
    else:
        main()
