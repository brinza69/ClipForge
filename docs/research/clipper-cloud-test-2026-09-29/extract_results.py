"""Pull the board and the transcript facts out of the cloud-test backend.

Usage: python extract_results.py PROJECT_ID path/to/clipforge.db

Writes board.json (primary clips, rank order) and prints a readable summary,
plus every "clip that / clip it" style phrase in the transcript (CA1).
Reads the board from the running backend on 127.0.0.1:8420.
"""
import json
import re
import sqlite3
import sys
import urllib.request

API = "http://127.0.0.1:8420"
PID = sys.argv[1]
DB = sys.argv[2]

CLIP_PHRASE = re.compile(r"\b(clip (that|it|this)|somebody clip|someone clip|clip clip|"
                         r"clip the|clipped|clippers?)\b", re.I)


def mmss(t: float) -> str:
    t = int(round(t))
    return f"{t // 60:02d}:{t % 60:02d}"


clips = json.loads(urllib.request.urlopen(f"{API}/api/clipper/clips?project_id={PID}",
                                          timeout=60).read())
primary = [c for c in clips if not c.get("is_alternative")]
alts = [c for c in clips if c.get("is_alternative")]
primary.sort(key=lambda c: (c.get("rank_position") is None, c.get("rank_position") or 0,
                            c.get("start_time") or 0))
board = []
print(f"clips total {len(clips)}: primary {len(primary)}, alternatives {len(alts)}")
for i, c in enumerate(primary, 1):
    subs = c.get("sub_scores") or {}
    top = sorted(subs.items(), key=lambda kv: -(kv[1] or 0))[:3]
    board.append({"rank": i, "start": c["start_time"], "end": c["end_time"],
                  "score": c.get("overall_score"), "id": c["id"]})
    text = " ".join((c.get("transcript_text") or "").split())
    print(f"\n#{i}  {mmss(c['start_time'])}-{mmss(c['end_time'])}  ({c['duration']:.0f}s)  "
          f"score {c.get('overall_score')}  type {c.get('content_type')}")
    print(f"    headline: {c.get('headline_text')}")
    print(f"    reason:   {c.get('score_reason')}")
    print(f"    top sub-scores: {', '.join(f'{k} {v:.0f}' for k, v in top)}")
    print(f"    text: {text[:220]}{'…' if len(text) > 220 else ''}")
    warns = c.get("warnings") or []
    if warns:
        print(f"    warnings: {warns[:3]}")
json.dump(board, open("board.json", "w"), indent=2)

con = sqlite3.connect(DB)
row = con.execute("SELECT segments, word_count, failed_chunks, language FROM transcripts "
                  "WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (PID,)).fetchone()
if not row:
    print("\ntranscript: unavailable")
    sys.exit(0)
segments = json.loads(row[0]) if isinstance(row[0], str) else row[0]
if isinstance(segments, dict):
    segments = segments.get("segments") or []
print(f"\ntranscript: {len(segments)} segments, {row[1]} words, language {row[3]}, "
      f"failed chunks {row[2]}")
hits = []
for s in segments:
    text = s.get("text") or ""
    for m in CLIP_PHRASE.finditer(text):
        hits.append((float(s.get("start") or 0.0), m.group(0), text.strip()[:120]))
print(f"'clip' phrases in transcript: {len(hits)}")
for t, phrase, ctx in hits[:25]:
    inside = [b["rank"] for b in board if b["start"] <= t <= b["end"] + 5]
    print(f"  {mmss(t)}  «{phrase}»  board clip: {inside or '-'}  | {ctx}")
