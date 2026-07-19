"""ClipForge — local-LLM bug watch (dual-GPU Qwen orchestrator).

Feeds the rig's recent error signals (backend logs, failed jobs, dispatcher +
watchdog logs) to the two LOCAL Qwen instances started by
scripts/start_ollama_dual.ps1 and appends their findings to
data/qwen_findings.md. Free, offline, no API keys.

Split of work (both run in PARALLEL, one per GPU):
  qwen3:8b  (:11434, big GPU)   — deep root-cause analysis of errors/failures
  qwen3:4b  (:11435, small GPU) — ops triage + improvement suggestions

VRAM guard: an analysis is skipped for a round when its GPU has too little
free VRAM (the video rig / ComfyUI have priority). Single-GPU PCs: everything
runs on instance A.

Usage:
  python scripts/qwen_bug_watch.py            # one round
  python scripts/qwen_bug_watch.py --loop 30  # every 30 minutes, forever
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\clipforge")
OUT = ROOT / "data" / "qwen_findings.md"

BIG = {"url": "http://127.0.0.1:11434", "model": "qwen3:8b", "min_free_mb": 6000}
SMALL = {"url": "http://127.0.0.1:11435", "model": "qwen3:4b", "min_free_mb": 3000}

LOG_TAIL_LINES = 120
GEN_TIMEOUT_S = 600


def tail(path: Path, n: int = LOG_TAIL_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return "(missing)"


def failed_jobs(db: Path, limit: int = 12) -> list[dict]:
    try:
        con = sqlite3.connect(str(db), timeout=8)
        rows = con.execute(
            "SELECT id, type, error, updated_at FROM jobs WHERE status='failed' "
            "ORDER BY updated_at DESC LIMIT ?", (limit,),
        ).fetchall()
        con.close()
        return [{"id": r[0], "type": r[1], "error": (r[2] or "")[:300], "at": r[3]} for r in rows]
    except Exception as e:
        return [{"error": f"db read failed: {e}"}]


def gpu_free_mb() -> list[int]:
    """Free MiB per GPU index, [] when nvidia-smi is unavailable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        ).stdout
        return [int(x.strip()) for x in out.splitlines() if x.strip()]
    except Exception:
        return []


def instance_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5):
            return True
    except Exception:
        return False


def ask(inst: dict, prompt: str) -> str:
    body = json.dumps({
        "model": inst["model"],
        "prompt": prompt + "\n/no_think",
        "stream": False,
        "keep_alive": "2m",
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(
        f"{inst['url']}/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=GEN_TIMEOUT_S) as resp:
        text = json.load(resp).get("response", "")
    # qwen3 may emit a <think> block even with /no_think — strip it.
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def gather_evidence() -> tuple[str, str]:
    """(error_evidence, ops_evidence) — split so each model gets one half."""
    errors = "\n\n".join([
        "## backend A err tail\n" + tail(ROOT / "data" / "backend.err.log"),
        "## backend B err tail\n" + tail(ROOT / "data_b" / "backend.err.log"),
        "## failed jobs A\n" + json.dumps(failed_jobs(ROOT / "data" / "db" / "clipforge.db"), indent=1),
        "## failed jobs B\n" + json.dumps(failed_jobs(ROOT / "data_b" / "db" / "clipforge.db"), indent=1),
    ])
    ops = "\n\n".join([
        "## dispatch log tail\n" + tail(ROOT / "data" / "dispatch.log"),
        "## dispatch err tail\n" + tail(ROOT / "data" / "dispatch.err.log", 40),
        "## watchdog log tail\n" + tail(ROOT / "data" / "watchdog.log", 60),
    ])
    return errors, ops


DEEP_PROMPT = """You are a senior engineer reviewing a Windows dual-GPU video pipeline
(FastAPI + FFmpeg + faster-whisper + ElevenLabs TTS + Google Drive/Sheets).
Below are recent ERROR LOGS and FAILED JOBS. Find the distinct BUGS:
- group repeated failures into one root cause each
- for each: symptom -> most likely root cause -> concrete fix suggestion
- flag anything that will LOSE user data or money (API quota) first
Reply in Romanian, concise markdown, max ~30 lines. Evidence:

"""

OPS_PROMPT = """You are a devops reviewer for a self-healing video pipeline rig
(PowerShell watchdog + Python dispatcher driving 2 FastAPI backends).
Below are the DISPATCHER and WATCHDOG logs. Report:
- signs of instability (restarts, enqueue failures, rows skipped/failed)
- 3 concrete improvements to make the pipeline more reliable/faster
Reply in Romanian, concise markdown, max ~25 lines. Evidence:

"""


def pick_instances() -> tuple[dict | None, dict | None, list[str]]:
    """(deep_inst, ops_inst, notes) honoring aliveness + VRAM guard.
    Falls back to sharing one instance when only one is usable."""
    notes: list[str] = []
    free = gpu_free_mb()
    big_ok = instance_alive(BIG["url"])
    small_ok = instance_alive(SMALL["url"])
    if big_ok and free and max(free) < BIG["min_free_mb"]:
        notes.append(f"GPU busy ({max(free)}MB free) — deep analysis deferred to keep the video rig safe")
        big_ok = False
    if small_ok and free and min(free) < SMALL["min_free_mb"] and len(free) > 1:
        notes.append("small GPU busy — ops triage routed to the big instance" if big_ok else "small GPU busy — ops triage skipped")
        small_ok = False
    deep = BIG if big_ok else (SMALL if small_ok else None)
    ops = SMALL if small_ok else (BIG if big_ok else None)
    if not deep and not ops:
        notes.append("no usable Qwen instance (not running, or GPUs too busy) — run scripts/start_ollama_dual.ps1")
    return deep, ops, notes


def one_round() -> str:
    errors, ops_evidence = gather_evidence()
    deep_inst, ops_inst, notes = pick_instances()

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {}
        if deep_inst:
            futs["deep"] = pool.submit(ask, deep_inst, DEEP_PROMPT + errors)
        if ops_inst:
            futs["ops"] = pool.submit(ask, ops_inst, OPS_PROMPT + ops_evidence)
        for key, fut in futs.items():
            try:
                results[key] = fut.result(timeout=GEN_TIMEOUT_S + 30)
            except Exception as e:
                results[key] = f"(analysis failed: {e})"

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    section = [f"\n\n---\n\n# Qwen bug watch — {stamp}\n"]
    for note in notes:
        section.append(f"> ⚠️ {note}\n")
    if "deep" in results:
        section.append(f"## Analiză buguri ({(deep_inst or {}).get('model')})\n\n{results['deep']}\n")
    if "ops" in results:
        section.append(f"## Triaj operațional ({(ops_inst or {}).get('model')})\n\n{results['ops']}\n")
    if len(section) == 1:
        section.append("(nicio analiză în această rundă)\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write("\n".join(section))
    return f"round written -> {OUT}"


def main() -> None:
    if "--loop" in sys.argv:
        try:
            minutes = int(sys.argv[sys.argv.index("--loop") + 1])
        except Exception:
            minutes = 30
        print(f"[qwen_bug_watch] loop every {minutes} min; report: {OUT}", flush=True)
        while True:
            print(one_round(), flush=True)
            time.sleep(minutes * 60)
    else:
        print(one_round(), flush=True)


if __name__ == "__main__":
    main()
