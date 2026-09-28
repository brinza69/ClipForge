"""Batch R6: which rendered exports have a video player's chrome in them.

    python scripts/detect_source_chrome.py --all
    python scripts/detect_source_chrome.py pilot2c8a --expect detected
    python scripts/detect_source_chrome.py --all --every 2.0

RUNS ON THE EXPORTS, NOT THE PROXIES, and that is the whole reason this file
found anything. The first attempt sampled eight uniform frames of each source
proxy, found nothing, and concluded the corpus had no positive — while the v2
human review had already listed browser UI among the v3 renderer's defects. A
sample that misses the thing it looked for is not evidence the thing is absent.

`--expect` turns the run into a gate. Without it the script only reports,
because a detector graded by whoever tuned it is not being graded.

THE KNOWN ANSWER, and it is the one this can be checked against: all 14 Moist
exports carry YouTube player chrome — `Watch later` at the top, `SHARE` and
`SAVE` at the bottom — and 27 exports across the ten other projects carry one
isolated `Search` between them.

AN EXPORT WHOSE VIDEO CANNOT BE READ GETS A ROW AND FAILS THE RUN. It does not
vanish; that mistake has been made seven times in this plan, always the same
way, and always by something unreadable disappearing until the result looked
like a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

from services.clipper import source_chrome as sc  # noqa: E402


def _measure(path: Path, every: float, reader) -> dict:
    got = sc.detect(str(path), every_s=every, reader=reader)
    return {"project": path.parent.parent.name, "export": path.stem, **got}


def _print(row: dict) -> None:
    hits = row["hits"]
    words = sorted({h["text"].lower() for h in hits})
    print(f"{row['project']:16}{row['export']:14}{row['state']:14}"
          f"{row['frames_with_a_control']:>4}/{row['frames_analysed']:<4} frames"
          f"  {', '.join(words) if words else ''}")
    if row["why_unavailable"]:
        print(f"{'':30}could not decide: {row['why_unavailable']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--every", type=float, default=sc.EVERY_S,
                    help=("seconds between sampled frames; the threshold was "
                          f"measured at {sc.EVERY_S} and any other cadence is "
                          "refused rather than rescaled"))
    ap.add_argument("--expect", choices=sc.STATES)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = sorted(DATA.glob("*/exports/*.mp4"))
    missing: list[str] = []
    if not args.all:
        wanted = list(dict.fromkeys(args.projects))
        if not wanted:
            ap.error("name a project or pass --all")
        present = {p.parent.parent.name for p in paths}
        # A PROJECT ASKED FOR AND NOT FOUND IS A REFUSAL, not a smaller corpus.
        missing = [name for name in wanted if name not in present]
        paths = [p for p in paths if p.parent.parent.name in set(wanted)]

    reader = sc._reader()
    # PRINTED AS THEY ARE MEASURED. The first version collected every row and
    # printed at the end, so an interruption threw away hours of OCR — which is
    # how this measurement was nearly lost twice.
    # AND IN `--json` MODE TOO, as JSONL on stderr. The first version printed
    # rows only in text mode, so a `--json` run over the whole corpus held every
    # result until the end and an interruption threw away the hours that
    # produced them. stderr rather than stdout, because stdout has to stay a
    # single parsable document.
    rows = []
    for path in paths:
        row = _measure(path, args.every, reader)
        rows.append(row)
        if args.json:
            print(json.dumps({k: v for k, v in row.items() if k != "hits"}),
                  file=sys.stderr, flush=True)
        else:
            _print(row)

    states = {s: len([r for r in rows if r["state"] == s]) for s in sc.STATES}
    out = {
        "exports": len(rows),
        "projects_not_found": missing,
        "states": states,
        "frames_analysed": sum(r["frames_analysed"] for r in rows),
        "frames_sampled": sum(r["frames_sampled"] for r in rows),
        "thresholds": {"frames_min": sc.FRAMES_MIN, "conf_min": sc.CONF_MIN,
                       "calibrated": False},
        "rows": rows,
    }

    bad: list[str] = []
    for name in missing:
        bad.append(f"project asked for and not found: {name}")
    if not rows:
        bad.append("no exports found — a pass over nothing is not a pass")
    if states[sc.UNAVAILABLE]:
        bad.append(f"{states[sc.UNAVAILABLE]} export(s) could not be decided")
    if args.expect:
        wrong = [r for r in rows if r["state"] != args.expect]
        for r in wrong:
            bad.append(f"{r['project']}/{r['export']}: expected "
                       f"{args.expect}, got {r['state']}")
    out["failures"] = bad

    if args.json:
        print(json.dumps(out, indent=1))
    else:
        print()
        print(f"exports {len(rows)}   "
              + "   ".join(f"{s} {states[s]}" for s in sc.STATES))
        print(f"frames analysed {out['frames_analysed']} of "
              f"{out['frames_sampled']} sampled")
        print(f"thresholds: {sc.FRAMES_MIN} frames at confidence "
              f"{sc.CONF_MIN}, chosen with the answer visible — not calibrated")
        print()
        for line in bad:
            print(f"FAIL: {line}")
        if not bad:
            # NOT "no chrome found". `not_detected` is a weak look, not a clean
            # bill: the recogniser reads this content badly and recall is
            # undemonstrated.
            print("every export was decided; `not_detected` is not `clean`")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
