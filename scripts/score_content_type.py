"""Score the content classifier against docs/source-labels.md.

The companion to `score_facecam.py`, and it exists for the same reason: every
threshold in the classifier was fitted before there were labels, and without a
scoreboard a change that "looks better" cannot be told from one that is.

`content_type` is a column on the PROJECT and it picks a whole weight row in
`scoring.PROFILES`, so a wrong answer scores every clip from that source
against the wrong profile, silently.

The truth here is the DOMINANT type over the whole file, which is the honest
comparison for a per-file verdict — several of these sources are two or three
things in a row, and `source-labels.md` says so per stretch.
"""
from __future__ import annotations

import glob
import json
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))

from services.clipper.content_type import detect_content_type   # noqa: E402

# (project, name, dominant type over the whole file)
SOURCES = [
    ("39c89ae2e16e", "EARLY STREAM", "gaming"),
    ("2c8af11153a3", "moistcr1tikal", "commentary"),
    ("slice4h00test", "Minecraft 4h", "gaming"),
    ("2d3375ee3420", "Minecraft 12m", "gaming"),
    ("0c9685df852b", "gym", "irl"),
    ("f81b86d27877", "go ghost", "talking_head"),
    ("ee0e599b3ecb", "apartament", "irl"),
    ("6b3844793c6a", "Jensen Huang", "interview"),
    ("5f4c2770254d", "Jynxzi", "gaming"),
    ("9a414e1f8a86", "Kai Cenat", "irl"),
    ("e84693f38e2b", "IRL World Cup", "irl"),
]


def _transcript(pid: str) -> dict:
    db = sqlite3.connect(f"file:{_REPO / 'data' / 'db' / 'clipforge.db'}?mode=ro", uri=True)
    try:
        row = db.execute(
            "select segments from transcripts where project_id=?", (pid,)).fetchone()
    finally:
        db.close()
    return {"segments": json.loads(row[0])} if (row and row[0]) else {}


def main() -> None:
    right = 0
    print(f"{'source':18s} {'truth':13s} {'got':14s} {'conf':>5s} {'speech':>7s}")
    for pid, name, truth in SOURCES:
        base = _REPO / "data" / "clipper" / pid
        frames = sorted(glob.glob(str(base / "frames" / "*.jpg")))
        sig_path = base / "analysis" / "signals.json"
        if not frames or not sig_path.exists():
            print(f"{name:18s} {truth:13s} (no frames or signals)")
            continue
        signals = json.loads(sig_path.read_text(encoding="utf-8"))
        verdict = detect_content_type(frames, signals, _transcript(pid))
        got = verdict["content_type"]
        right += got == truth
        print(f"{name:18s} {truth:13s} {('OK ' if got == truth else 'XX ') + got:14s} "
              f"{verdict['confidence']:5.2f} "
              f"{signals.get('speech_coverage', float('nan')):7.3f}")
    print(f"\n--> {right}/{len(SOURCES)}")


if __name__ == "__main__":
    main()
