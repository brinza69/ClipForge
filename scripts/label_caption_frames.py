"""The agent's confirmation of which observed text is dialogue, and the inventory.

    python scripts/label_caption_frames.py pilotf81b 6053a598cf06 b23c14c41495

WHAT THIS RECORDS AND WHAT IT DOES NOT. §A asks for text GROUPS labelled
`dialogue` / `non_dialogue` / `uncertain`, with the time, the frame and the
PROVENANCE of the confirmation. This writes that record with `by: agent`, and
the confirmation behind it is an inspection of every sampled frame through
`dump_caption_frames`' contact sheets — 5 sheets for `6053a598cf06` and 6 for
`b23c14c41495`, 118 frames in total, all of them looked at.

THE RULE IS A SUMMARY OF THAT INSPECTION, NOT A SUBSTITUTE FOR IT, and the
difference matters enough to state: the label is applied by position, and the
position rule is written down here BECAUSE it was checked against every frame
rather than assumed from a few. On these two clips the source's burned subtitle
occupies one band at the bottom, and the only text anywhere else is the Apple
Watch face the speaker holds up in the last two shots of `b23c14c41495`. The
mechanical count agrees exactly with the sheets: 82 boxes on the first clip and
101 on the second, of which precisely 2 sit above the band — shot 29 at 240.0 s
and shot 30 at 241.2 s, both the watch.

Run against a THIRD clip, the rule would be an assumption again. It is not a
detector and must not be reused without the same inspection.

AND `non_dialogue` IS NOT PERMISSION TO CROP. The watch reading is the whole
point of that stretch: the speaker holds it up to show 20,958 steps. Excluding
it from the caption measurement says nothing about whether the framing may lose
it, which is why `caption_labels` keeps excluded groups in the inventory with
their reason instead of dropping them.

WHAT IS STILL UNMEASURED, and both were seen on the sheets rather than inferred:
the detector's boxes UNDER-cover the line — this source's captions highlight the
spoken words and dim the rest, and at the shipping thresholds the union is a
median 1.04x and up to 1.37x narrower than at a looser setting — and it misses
whole lines: at 1161.1 s a caption is plainly on screen and no box was returned.
So "no text was seen" is a weaker statement than it looks, and a region built
from these boxes is built from an under-estimate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Below this fraction of the frame height, a box is not in the subtitle band.
#: Chosen from the inspection above — every one of the 183 boxes is either well
#: below it (the caption, y around 0.91-0.97) or well above (the watch, 0.36 and
#: 0.42). Nothing sits near the line, so the threshold is not doing any deciding.
BAND_TOP = 0.85

INSPECTED = {
    "6053a598cf06": "5 contact sheets, 56 frames, all inspected",
    "b23c14c41495": "6 contact sheets, 62 frames, all inspected",
}


async def _shots_for(project_id: str, clip_id: str):
    from database import init_db
    from services.clipper import storage
    from workers.clipper_render_plan import _decide_render, _load

    await init_db()
    clip, project = await _load(clip_id)
    paths = storage.paths(project_id)
    decision = await _decide_render(clip, project, paths["exports_dir"])
    return clip, (decision.get("dyn") or {})


def _labels(observation: dict) -> list[dict]:
    """One label per group per sample, by the inspected position rule."""
    out: list[dict] = []
    for sample in observation.get("samples") or []:
        boxes = sample.get("boxes")
        at = sample.get("t_decoded")
        if not boxes or at is None:
            continue
        band, other = [], []
        for i, box in enumerate(boxes):
            mid = (float(box["y0"]) + float(box["y1"])) / 2.0
            (band if mid > BAND_TOP else other).append(i)
        if band:
            out.append({"at": at, "boxes": band, "label": "dialogue",
                        "by": "agent",
                        "why": "the_sources_burned_subtitle_band_at_the_foot_"
                               "of_the_frame_confirmed_on_the_contact_sheets"})
        if other:
            out.append({"at": at, "boxes": other, "label": "non_dialogue",
                        "by": "agent",
                        "why": "the_apple_watch_face_the_speaker_holds_up_"
                               "which_is_a_device_readout_and_not_a_subtitle_"
                               "and_which_this_label_does_not_licence_cropping"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    args = ap.parse_args()

    from services.clipper import caption_labels as cl

    frames_dir = DATA / args.project / "caption_frames"
    report: list[dict] = []
    refused = 0

    for clip_id in args.clips:
        path = frames_dir / f"{clip_id}.samples.json"
        if not path.exists():
            print(f"{clip_id}: REFUSED — no samples; run dump_caption_frames")
            refused += 1
            continue
        if clip_id not in INSPECTED:
            # A clip nobody looked at may not borrow another clip's inspection.
            print(f"{clip_id}: REFUSED — the position rule is a summary of an "
                  f"inspection, and this clip has not had one")
            refused += 1
            continue
        blob = json.loads(path.read_text(encoding="utf-8"))
        obs = blob["observation"]
        labels = _labels(obs)
        applied = cl.apply(obs, labels)
        clip, plan = asyncio.run(_shots_for(args.project, clip_id))
        shots = [s for s in (plan.get("shots") or []) if isinstance(s, dict)]
        inv = cl.inventory(shots, obs, applied, float(clip.start_time or 0.0))

        out = {"clip": clip_id, "inspected": INSPECTED[clip_id],
               "band_top": BAND_TOP, "labels": labels,
               "counts": applied["counts"], "unmatched": applied["unmatched"],
               "inventory": inv}
        (frames_dir / f"{clip_id}.labels.json").write_text(
            json.dumps(out, indent=1, default=str), encoding="utf-8")
        report.append(out)

        print(f"\n{clip_id}  ({INSPECTED[clip_id]})")
        print(f"  boxes: {applied['counts']['dialogue']} dialogue, "
              f"{applied['counts']['non_dialogue']} non-dialogue, "
              f"{applied['counts']['uncertain']} uncertain")
        if applied["unmatched"]:
            print(f"  UNMATCHED labels: {len(applied['unmatched'])}")
        c = inv["counts"]
        print(f"  inventory over {inv['shots']} shots — "
              f"{c[cl.CONFIRMED]} confirmed dialogue, "
              f"{c[cl.OTHER_TEXT]} other text only, "
              f"{c[cl.NO_TEXT]} no text observed, "
              f"{c[cl.UNEVIDENCED]} unevidenced")
        for row in inv["rows"]:
            if row["row"] != cl.CONFIRMED:
                print(f"    shot {row['shot']:>3}  {row['row']}  "
                      f"(samples {row['samples']}, dialogue {row['dialogue']}, "
                      f"excluded {row['excluded']})")

    total = sum(r["inventory"]["shots"] for r in report)
    print(f"\n{total} shots inventoried across {len(report)} clip(s)")
    if refused or not report:
        print(f"REFUSED {refused} of {len(args.clips)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
