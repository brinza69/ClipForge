"""How much of what the detector sees sits on the source's fixed anchor.

NOT "how much of it is the creator". Nobody has labelled these boxes; geometry
is all this knows. Anchor compatibility is what is measured, creator identity is
what is inferred from it, and the two must not be written as one sentence.

Batch R3a. `stable_track` finds the fixed webcam overlay; until R3a it only
moved the CENTRE of the face-cam family, so detections away from that overlay
still counted as a subject and held the crop. This prints how many there are,
per project, off the stored analysis.

    python scripts/measure_creator_presence.py pilotf81b pilotee0e pilot6b38 pilot2c8a

Reads only. Nothing here decides anything: it is the evidence for the anchor
tolerance and for the claim that the filtering changes what it is supposed to
change and nothing else.

WHICH TRACK, AND WHAT THAT COSTS. `analysis/faces.json` is the whole-VOD track,
and on the pilots its median gap is 6.7 SECONDS. Production frames on
`dynamic_window`'s 0.25s track. At 6.7s a hop, `ENTER_S` of 3.0s rounds to one
sample — there is effectively no hysteresis at all, where production applies
twelve samples of it.

So this reports ANCHOR COMPATIBILITY, which is a property of the boxes and does
not care about the sample rate. It deliberately does NOT report how much of the
presence timeline would change: that number came out of a first version of this
script and it was meaningless, for exactly the reason the `dynamic_subject`
docstring already warned about. The presence effect can only be measured on the
dense track, at render time.
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

from services.clipper import dynamic_subject as subject  # noqa: E402


def _track(project_id: str) -> list[dict]:
    path = DATA / project_id / "analysis" / "faces.json"
    if not path.exists():
        return []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, list):
        return [s for s in loaded if isinstance(s, dict)]
    for key in ("samples", "faces", "track"):
        if isinstance(loaded.get(key), list):
            return [s for s in loaded[key] if isinstance(s, dict)]
    return []


def _report(project_id: str) -> None:
    track = _track(project_id)
    if not track:
        print(f"{project_id:12} no face track on disk")
        return

    stable = subject.stable_track(track)
    anchored = subject.anchored_track(track, stable)

    samples = len(track)
    with_face = sum(1 for s in track if s.get("boxes"))
    boxes = sum(len(s.get("boxes") or []) for s in track)
    kept_samples = sum(1 for s in anchored if s.get("boxes"))
    kept_boxes = sum(len(s.get("boxes") or []) for s in anchored)

    if not stable:
        print(f"{project_id:12} samples {samples:5}  with a face {with_face:5}  "
              f"boxes {boxes:5}   no anchor — the track is unchanged")
        assert anchored == track, "no anchor must mean no filtering"
        return

    print(f"{project_id:12} samples {samples:5}  with a face {with_face:5}  "
          f"boxes {boxes:5}")
    print(f"{'':12} anchor-compatible: {kept_samples:5} samples "
          f"({100.0 * kept_samples / max(1, with_face):.0f}% of those with a face), "
          f"{kept_boxes} boxes of {boxes}")
    print(f"{'':12} anchor at ({stable['cx']:.0f}, {stable['cy']:.0f}) "
          f"w={stable['w']:.0f}  spread {stable['spread']} vs {stable['runner_up']}")
    print(f"{'':12} median gap {subject._hop_of(track):.1f}s — too coarse for a "
          f"presence figure; this is anchor compatibility only")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="+", help="project ids to measure")
    args = ap.parse_args()
    for project_id in args.projects:
        _report(project_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
