"""Asteapta sa se termine randarea pe ambele placi, apoi opreste PC-ul.

Ruleaza LOCAL si tacut: verifica la fiecare 5 minute daca vreun backend mai are
un job. Cand amandoua stau libere `RABDARE` verificari la rand (implicit 3 = ~15
minute), considera lotul terminat. Pragul exista fiindca intre doua randuri
placa e liber cateva zeci de secunde — o singura citire ar declara "gata" prea
devreme.

Inainte de oprire: salveaza starea rigului (ca `rig_boot.ps1` sa reia de unde a
ramas la logon) si ia un instantaneu de metrici.

    python scripts/render_then_shutdown.py [--rabdare 3] [--test]
        --test  face totul, dar NU opreste calculatorul
"""
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

_ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = _ROOT / "server" / ".venv" / "Scripts" / "python.exe"
LOG = _ROOT / "data" / "render_then_shutdown.log"
BACKENDS = {"A(:8420)": "http://127.0.0.1:8420", "B(:8421)": "http://127.0.0.1:8421"}
PAS = 300
RABDARE = int(sys.argv[sys.argv.index("--rabdare") + 1]) if "--rabdare" in sys.argv else 3
TEST = "--test" in sys.argv


def log(m):
    linie = f"{datetime.now():%Y-%m-%d %H:%M:%S} {m}"
    print(linie, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(linie + "\n")


def active(base):
    """Cate joburi ruleaza/asteapta pe backendul asta. None = nu raspunde."""
    try:
        with urllib.request.urlopen(base + "/api/jobs/?limit=6", timeout=25) as r:
            d = json.loads(r.read().decode())
        js = d if isinstance(d, list) else (d.get("jobs") or [])
        return sum(1 for j in js if j.get("status") in ("running", "queued"))
    except Exception:  # noqa: BLE001
        return None


log(f"astept terminarea randarii (verific la {PAS}s, prag {RABDARE} verificari libere)")
libere = 0
while True:
    stari = {n: active(b) for n, b in BACKENDS.items()}
    # Un backend care nu raspunde NU inseamna liber — poate fi doar ocupat si lent.
    necunoscut = any(v is None for v in stari.values())
    ocupat = any(v for v in stari.values() if v)
    if necunoscut or ocupat:
        libere = 0
    else:
        libere += 1
    log("  " + "  ".join(f"{n}={'?' if v is None else v}" for n, v in stari.items())
        + f"   verificari libere consecutive: {libere}/{RABDARE}")
    if libere >= RABDARE:
        break
    time.sleep(PAS)

log("randarea s-a terminat")

try:
    subprocess.run([str(PY), str(_ROOT / "scripts" / "track_metrics.py")],
                   cwd=str(_ROOT), timeout=600, capture_output=True)
    log("instantaneu de metrici salvat")
except Exception as e:  # noqa: BLE001
    log(f"metricile au esuat: {str(e)[:80]}")

# Opreste dispecerele si watchdog-ul, ca sa nu porneasca un rand nou in ultima clipa
ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
      "($_.CommandLine -match 'watchdog\\.ps1|dual_dispatch|herstory_dispatch') } | "
      "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }")
subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=180,
               capture_output=True)
log("dispecere si watchdog oprite")

if TEST:
    log("--test: NU opresc calculatorul")
    raise SystemExit(0)

subprocess.run(["shutdown", "/s", "/t", "120", "/c",
                "ClipForge: randare terminata, se opreste. Anulare: shutdown /a"],
               timeout=60)
log("oprire programata in 120 de secunde (anulare: shutdown /a)")
