r"""Tine coada `naratorul.ro` plina, in limita plafonului de 10.

Contul asta are plafon de **10 postari programate pe canal** — verificat pe
viu: Buffer raspunde "You have 10 scheduled posts out of 10 allowed" si refuza
a unsprezecea. (Celalalt cont Buffer, cel cu Povestitorul, are alt plan si
ajunge la 38 in coada. Plafonul e al planului, nu al uneltei.)

La 4 postari pe zi, 10 inseamna 2,5 zile de acoperire. Fara realimentare, coada
se goleste in weekend si canalul tace. Aici se verifica periodic si se adauga
exact cat incape, folosind chiar posterul — deci aceleasi sloturi, acelasi
sufix de parti, aceeasi evidenta a ce s-a postat deja.

Se opreste singur cand planul s-a epuizat: nu inventeaza continut, doar il
asaza pe cel existent.

    server\.venv\Scripts\python.exe scripts\umple_coada_narator.py [--dry]
"""
import os
import pathlib
import subprocess
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
os.environ.setdefault("CLIPFORGE_DATA_DIR", str(_ROOT / "data"))

DRY = "--dry" in sys.argv
PAUZA = 3 * 3600        # la 3 ore: intre doua sloturi trec minimum 2h30
PY = str(_ROOT / "server" / ".venv" / "Scripts" / "python.exe")
POSTER = str(_ROOT / "scripts" / "post_povestitor.py")


def spune(m):
    print(time.strftime("%d %b %H:%M") + " " + m, flush=True)


def o_trecere():
    """Ruleaza posterul o data. El insusi refuza sa depaseasca plafonul si sare
    peste ce e deja programat, deci rularea repetata e inofensiva."""
    cmd = [PY, POSTER, "--channel", "narator"]
    if DRY:
        cmd.append("--dry")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    ies = (p.stdout or "") + (p.stderr or "")
    for linie in ies.splitlines():
        if linie.strip():
            spune("   " + linie.strip())
    # posterul spune "programate: N" la final
    for linie in reversed(ies.splitlines()):
        if linie.startswith("programate:"):
            try:
                return int(linie.split(":")[1].split()[0])
            except (IndexError, ValueError):
                return 0
    return 0


def main():
    # INLOCUIT 30 aug 2026 de `umple_cozile.py`, care face acelasi lucru pentru
    # TOATE canalele contului, nu doar naratorul. Canalele franceze n-aveau
    # nicio realimentare si se goleau pur si simplu.
    #
    # Fisierul ramane fiindca nota de mai sus despre plafonul de 10 — masurat pe
    # viu, si diferit de celalalt cont — e singurul loc unde e scris.
    if "--si-inchise" not in sys.argv:
        print("scriptul e inlocuit de scripts/umple_cozile.py (30 aug 2026),")
        print("care umple TOATE cozile contului, nu doar naratorul.")
        print("adauga --si-inchise daca chiar vrei varianta veche.")
        return
    spune("supraveghez coada naratorul.ro (plafon 10, verific la 3h)")
    gol = 0
    while True:
        n = o_trecere()
        if n == 0:
            gol += 1
            # de doua ori la rand nimic de adaugat inseamna ori coada plina,
            # ori planul epuizat. Ambele sunt in regula; continuam sa verificam,
            # dar spunem o singura data.
            if gol == 2:
                spune("nimic de adaugat (coada plina sau plan epuizat)")
        else:
            gol = 0
            spune("adaugate " + str(n) + " postari")
        if DRY:
            return
        time.sleep(PAUZA)


if __name__ == "__main__":
    main()
