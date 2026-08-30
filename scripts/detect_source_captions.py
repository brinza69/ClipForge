"""Batch R6: which sources already carry burned-in subtitles.

Runs the detector over each project's analysis proxy and prints the verdict with
the evidence behind it. The known truth on the four pilots comes from the v2 human review — 15 of 15 go
ghost exports shipped with two caption systems — and NOT from
`docs/source-labels.md`, which identifies the sources but carries no caption
labels at all. This is the one place in the plan where a detector can be checked
against an answer somebody already knew.

    python scripts/detect_source_captions.py --all
    python scripts/detect_source_captions.py pilotf81b --expect present

`--expect` turns the run into a gate: it fails when the verdict disagrees with
the label. Without it the script only reports, because a detector that is
graded by the same person who tuned it is not being graded.

WHICH WAY ROUND THE ANSWERS GO, because an earlier draft had it backwards:
`present` means the source already has captions, so ClipForge's layer would be
DISABLED; `absent` means it keeps burning its own; `unknown` changes nothing. A
wrong `present` therefore ships a clip with no captions at all.

A project whose proxy is missing gets a row and fails the run. It does not
vanish — that mistake has been made six times in this plan already, always the
same way: something unreadable disappears and the result looks like a pass.
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

from services.clipper import source_captions as sc  # noqa: E402


def _measure(project_id: str, samples: int, reader) -> dict:
    proxy = DATA / project_id / "proxy" / "proxy.mp4"
    if not proxy.exists():
        return {"project": project_id, "state": sc.UNKNOWN,
                "why_unknown": "no_proxy_on_disk"}
    result = sc.detect(str(proxy), samples=samples, reader=reader)
    return {"project": project_id, **result}


def _report(row: dict) -> None:
    band = row.get("band")
    where = ("nothing" if not band else
             f"band {band['band']}/{sc.BANDS}, {band['frames']} of "
             f"{row.get('sampled', 0)} frames, widest line "
             f"{band['widest']} of the frame")
    print(f"{row['project']:14} {row['state']:8} {where}")
    if row.get("why_unknown"):
        print(f"{'':14} could not decide: {row['why_unknown']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--samples", type=int, default=14)
    # NOT `unknown`. An `unknown` result is counted as undecided, which forces
    # exit 2, so `--expect unknown` could never pass — a gate on "we could not
    # tell" is not a gate anyway.
    ap.add_argument("--expect", choices=(sc.PRESENT, sc.ABSENT),
                    help="fail the run when a verdict disagrees with this label")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    names = list(args.projects)
    if args.all or not names:
        names = sorted(p.name for p in DATA.glob("*") if p.is_dir())
    if not names:
        print(f"no projects under {DATA}")
        return 2

    reader = sc._reader()
    rows = [_measure(n, args.samples, reader) for n in names]
    assert len(rows) == len(names), "a project left the run without saying so"

    # Computed before the output mode branches: the code belongs to the run,
    # not to how it is printed.
    undecided = sum(1 for r in rows if r["state"] == sc.UNKNOWN)
    wrong = ([r for r in rows if r["state"] != args.expect] if args.expect
             else [])
    code = 2 if (wrong or (args.expect and undecided)) else 0

    if args.json:
        print(json.dumps(rows, indent=2))
        return code

    for row in rows:
        _report(row)
    if reader is None:
        print(f"\n{'':14} no text detector available — every verdict above is "
              f"`unknown`, which changes nothing downstream")
    if args.expect:
        print(f"\n{'':14} expected {args.expect}: "
              f"{len(rows) - len(wrong)} of {len(rows)} agree")
        for row in wrong:
            print(f"{'':14} DISAGREES: {row['project']} said {row['state']}")
    elif undecided:
        print(f"\n{'':14} {undecided} of {len(rows)} undecided — reported, and "
              f"an undecided source changes nothing: ClipForge keeps burning "
              f"its own layer, which is exactly today's behaviour")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
