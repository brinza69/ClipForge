r"""Tine PLINE cozile Buffer de pe contul asta, pe toate canalele.

Inlocuieste `umple_coada_narator.py`, care facea acelasi lucru doar pentru
naratorul.ro. Canalele franceze n-aveau nicio realimentare: se goleau si
ramaneau goale pana venea cineva sa ruleze posterul de mana.

Pentru fiecare canal se face intai planul, apoi se posteaza din el — planul se
reconstruieste de fiecare data fiindca rigul randeaza continuu si fisiere noi
apar pe Drive intre treceri.

    canal          plan                          poster
    narator        build_narator_post_list.py    --channel narator
    facebook_fr    build_fr_post_list.py         --channel facebook_fr
    franceza       build_fr_post_list.py         --channel franceza

Povestitorul NU e aici: e pe celalalt cont Buffer, administrat de alta sesiune.

Posterul refuza singur sa treaca de plafonul contului si sare peste ce e deja
programat, deci rularea repetata e inofensiva. Cand un canal n-are ce adauga,
ori coada e plina, ori planul s-a epuizat — ambele sunt in regula.

    server\.venv\Scripts\python.exe scripts\umple_cozile.py [--dry] [--canale narator,franceza]
"""
import os
import pathlib
import subprocess
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
os.environ.setdefault("CLIPFORGE_DATA_DIR", str(_ROOT / "data"))

PY = str(_ROOT / "server" / ".venv" / "Scripts" / "python.exe")
POSTER = str(_ROOT / "scripts" / "post_povestitor.py")
_CF = 0x08000000 if os.name == "nt" else 0

# (canal, scriptul care ii face planul). Planul francez e comun celor doua
# canale, deci se construieste o singura data pe trecere.
CANALE = [
    ("narator", "build_narator_post_list.py"),
    ("facebook_fr", "build_fr_post_list.py"),
    ("franceza", "build_fr_post_list.py"),
]

DRY = "--dry" in sys.argv
PAUZA = 3 * 3600        # intre doua sloturi trec minimum 2h30


def spune(m):
    print(time.strftime("%d %b %H:%M") + " " + m, flush=True)


def ruleaza(cmd, timeout=3600):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       creationflags=_CF)
    return (p.stdout or "") + (p.stderr or "")


def o_trecere(canale):
    total, facute = 0, set()
    for canal, builder in canale:
        if builder not in facute:
            ies = ruleaza([PY, str(_ROOT / "scripts" / builder)])
            for ln in ies.splitlines():
                if "in plan" in ln or "videoclipuri" in ln:
                    spune("   [" + builder.replace("build_", "").replace("_post_list.py", "")
                          + "] " + ln.strip())
            facute.add(builder)
        cmd = [PY, POSTER, "--channel", canal]
        if DRY:
            cmd.append("--dry")
        ies = ruleaza(cmd)
        n = 0
        for ln in reversed(ies.splitlines()):
            if ln.startswith("programate:"):
                try:
                    n = int(ln.split(":")[1].split()[0])
                except (IndexError, ValueError):
                    n = 0
                break
        # Un canal neconectat sau inchis nu e o eroare: se spune o data si
        # se merge mai departe, ca sa nu blocheze celelalte canale.
        for cheie in ("nu e conectat in Buffer", "e inchis"):
            if cheie in ies:
                spune("   " + canal + ": sarit — " + cheie)
                break
        else:
            spune("   " + canal + ": " + str(n) + " adaugate")
        total += n
    return total


def main():
    arg = sys.argv[sys.argv.index("--canale") + 1] if "--canale" in sys.argv else None
    canale = [c for c in CANALE if not arg or c[0] in arg.split(",")]
    if not canale:
        raise SystemExit("--canale: alege dintre " + ", ".join(c[0] for c in CANALE))

    spune("supraveghez cozile: " + ", ".join(c[0] for c in canale) + " (verific la 3h)")
    gol = 0
    while True:
        try:
            n = o_trecere(canale)
        except Exception as e:  # noqa: BLE001
            spune("trecere esuata: " + str(e)[:200])
            n = 0
        if n == 0:
            gol += 1
            if gol == 2:
                spune("nimic de adaugat pe niciun canal (cozi pline sau planuri epuizate)")
        else:
            gol = 0
            spune("total adaugate: " + str(n))
        if DRY:
            return
        time.sleep(PAUZA)


if __name__ == "__main__":
    main()
