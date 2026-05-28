"""Quick inspection of auto-transcribe output."""
import json
import sys
import urllib.request

req = urllib.request.Request(
    "http://localhost:8420/api/captions/auto-transcribe",
    data=json.dumps({
        "session_id": "5e3641d0ed65",
        "template_id": "inter_blk_italic",
        "words_per_chunk": 1,
        "x_pct": 0.5,
        "y_pct": 0.85,
        "scale": 1.0,
        "language": "ro",
    }).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=180) as r:
    d = json.loads(r.read())

print(f"language: {d['language']}  word_count: {d['word_count']}  overlays: {len(d['overlays'])}\n")
print("First 10 chunks:")
for o in d["overlays"][:10]:
    print(f"  {o['start_t']:6.2f}-{o['end_t']:6.2f}  template={o['template_id']}  text={o['text']!r}")
print("\n...last 5:")
for o in d["overlays"][-5:]:
    print(f"  {o['start_t']:6.2f}-{o['end_t']:6.2f}  template={o['template_id']}  text={o['text']!r}")

print("\nFull transcript (first 400 chars):")
print(d.get("full_text", "")[:400])
