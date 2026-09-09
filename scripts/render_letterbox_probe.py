"""The cost of the full letterbox, on the two go ghost clips, measured.

    python scripts/render_letterbox_probe.py pilotf81b 6053a598cf06 b23c14c41495

WHAT THIS IS FOR. `source_caption_survival` establishes that go ghost's subtitle
band is 1216 px wide and that the widest 9:16 crop a 2560x1440 frame allows is
810, so no crop of that source can contain it. The choice taken on 2026-09-05 is
to letterbox and leave our own caption layer suppressed — and Codex's condition
is that it be proven on two clips before the other thirteen, because a framing
that keeps the text and shrinks the speaker past watching has traded one defect
for another.

SO IT RENDERS THROUGH THE REAL PATH, `render_dynamic_clip` with the plan's own
shots forced to `fit`, not a hand-built filtergraph beside it. And it renders
BOTH: the plan as it ships and the full letterbox, because a comparison with
only the proposal in it is a choice between one thing and nothing.

WHAT IT MEASURES AND WHAT IT REFUSES TO. It reports, in delivered pixels of the
1080x1920 output, how tall the source's subtitle band becomes and how wide the
speaker's face becomes, under each framing — those are arithmetic. It does NOT
say whether either is legible or watchable. §R6's whole lesson is that a
geometric `kept` is not a caption that can be read, and the only instrument for
that is a person watching the file; the script's job is to put the two files and
the two numbers in front of one.

WHERE IT WRITES: `<project>/letterbox_probe/`, never `exports/`. Six places in
this repo read `exports/*.json` as a clip's sidecar and one stray file has taken
the R0 gate down before. Nothing here touches the clip's export, sidecar or row.
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

OUT_W, OUT_H = 1080, 1920


def _letterboxed(plan: dict) -> dict:
    """The same plan with every shot composed as `fit`.

    Then through `merge_equivalent_shots`, which is the shipping function for
    "these shots deliver the same picture": every fit shot on one source is the
    same full frame, so leaving them separate would schedule two dozen identical
    crop commands and — worse — record a shot count that suggests cuts a viewer
    cannot see. That is the exact defect R1 removed 116 of.
    """
    from services.clipper.dynamic_geometry import merge_equivalent_shots

    shots = [{**s, "composition": "fit"}
             for s in (plan.get("shots") or []) if isinstance(s, dict)]
    return merge_equivalent_shots({**plan, "shots": shots},
                                  int(plan.get("src_w") or 0),
                                  int(plan.get("src_h") or 0))


async def _plan_for(project_id: str, clip_id: str):
    from database import init_db
    from workers.clipper_render_plan import _decide_render, _load

    await init_db()
    clip, project = await _load(clip_id)
    if project.id != project_id:
        raise ValueError("clip belongs to another project")
    work = DATA / project_id / "letterbox_probe"
    work.mkdir(parents=True, exist_ok=True)
    decision = await _decide_render(clip, project, work)
    return clip, project, decision


def _delivered_sizes(plan: dict, band: Any) -> dict:
    """Subject width and subtitle height in OUTPUT pixels, per framing.

    Read through `evidence_map.crop_window`, so the scale is the one the
    renderer produces rather than the planner's rectangle. Both framings are
    reported; the ratio between them is the cost of the letterbox and it is the
    number the decision turns on.
    """
    from services.clipper import evidence_map as em

    sw = int(plan.get("src_w") or 0)
    sh = int(plan.get("src_h") or 0)
    face = ((plan.get("subject") or {}).get("face") or {})
    face_w = float(face.get("w") or 0.0)
    band_h = 0.0
    if isinstance(band, dict) and isinstance(band.get("extent"), dict):
        ext = band["extent"]
        try:
            band_h = (float(ext["y1"]) - float(ext["y0"])) * sh
        except (KeyError, TypeError, ValueError):
            band_h = 0.0

    out: dict[str, Any] = {"src_w": sw, "src_h": sh,
                           "face_w_src_px": round(face_w, 1),
                           "band_h_src_px": round(band_h, 1)}
    for name, shots in (("as_shipped", plan.get("shots") or []),
                        ("letterbox", _letterboxed(plan).get("shots") or [])):
        widths = []
        for shot in shots:
            crop = em.crop_window(shot, src_w=sw, src_h=sh,
                                  style=plan.get("style") or {})
            if not isinstance(crop, str):
                widths.append(float(crop[2]))
        if not widths:
            # NOT zero, and not skipped: a framing whose windows could not be
            # read has no scale, and a report that dropped the row would leave
            # the other one looking like the only option there was.
            out[name] = {"refused": "no_window_could_be_read"}
            continue
        # The narrowest window magnifies most; the widest magnifies least. Both,
        # because a clip that switches between them delivers both.
        out[name] = {
            "window_w_px": [round(min(widths), 1), round(max(widths), 1)],
            "face_w_out_px": [round(face_w * OUT_W / max(widths), 1),
                              round(face_w * OUT_W / min(widths), 1)],
            "band_h_out_px": [round(band_h * OUT_W / max(widths), 1),
                              round(band_h * OUT_W / min(widths), 1)],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    args = ap.parse_args()

    from services.clipper import source_caption_survival as scs
    from workers.clipper_render_output import render_export
    from services.clipper import source_captions as scap
    from workers.clipper_render_plan import _source_path

    out_dir = DATA / args.project / "letterbox_probe"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ONE detector run for the project, because the band is a property of the
    # source and not of the clip. It is also the input to every number below, so
    # a run that could not obtain it must say so rather than report the framing
    # cost as if the subtitle question had been settled.
    proxy = DATA / args.project / "proxy" / "proxy.mp4"
    detected = scap.detect(str(proxy)) if proxy.exists() else None
    band = (detected or {}).get("band")
    state = (detected or {}).get("state")
    print(f"source captions: {state or 'unavailable'}  band={json.dumps(band)}\n")

    rows: list[dict] = []
    failed = 0
    for clip_id in args.clips:
        clip, project, decision = asyncio.run(_plan_for(args.project, clip_id))
        plan = decision.get("dyn") or {}
        if not plan.get("shots"):
            print(f"{clip_id}: REFUSED — the re-plan has no shots")
            failed += 1
            continue
        src = _source_path(project)
        row: dict[str, Any] = {
            "clip": clip_id,
            "source_captions": state,
            "sizes": _delivered_sizes(plan, band),
            "survival": {}, "files": {}, "render_record": {},
            # NOT a recommendation. Whether the letterboxed text can be read and
            # whether the speaker is still worth watching is the question being
            # asked, and a script that answered it would answer for the person
            # it is asking.
            "verdict": None,
        }
        for name, use in (("as-shipped", plan), ("letterbox", _letterboxed(plan))):
            path = out_dir / f"{clip_id}.{name}.mp4"
            print(f"{clip_id}: rendering {name} "
                  f"({len(use.get('shots') or [])} shots) ...", flush=True)
            result = asyncio.run(render_export(
                clip, project, {**decision, "dyn": use}, path, src=str(src)))
            row["files"][name] = str(path.resolve())
            # FROM THE CALL THAT RAN. `caption_policy` says suppress; this says
            # whether the encode carried a subtitle filter, and the 15 stored
            # exports are why the two are recorded separately.
            rec = result.get("render_record") or {}
            row["render_record"][name] = {
                "caption_filter": rec.get("caption_filter"),
                "ass_path": rec.get("ass_path"),
                "offered": rec.get("ass_path_offered"),
            }
            row["survival"][name] = scs.survival(
                band, use.get("shots"), use.get("style"),
                use.get("src_w"), use.get("src_h"))
        rows.append(row)

        sizes = row["sizes"]
        print(f"  subtitle band {sizes['band_h_src_px']}px and face "
              f"{sizes['face_w_src_px']}px in the SOURCE")
        for name in ("as_shipped", "letterbox"):
            got = sizes.get(name) or {}
            if "refused" in got:
                print(f"  {name:11} REFUSED: {got['refused']}")
                continue
            print(f"  {name:11} band {got['band_h_out_px']}px, "
                  f"face {got['face_w_out_px']}px of {OUT_W}x{OUT_H}")
        for name in ("as-shipped", "letterbox"):
            s = row["survival"][name]
            print(f"  {name:11} band {s['state']}"
                  f" ({s['uncontained_shots']}/{s['measured']} shots uncontained,"
                  f" worst {s['worst_visible']})")
            print(f"  {name:11} caption filter in the call: "
                  f"{row['render_record'][name]['caption_filter']}")
        print()

    index = out_dir / "probe.json"
    index.write_text(json.dumps({
        "project": args.project, "clips": args.clips,
        "source_captions": detected, "rows": rows,
        "asked": "does the letterboxed source subtitle read, and is the "
                 "speaker still worth watching",
        "verdict": None,
    }, indent=1, default=str), encoding="utf-8")

    print(f"{len(rows)} clip(s) rendered both ways into {out_dir}")
    if failed:
        # A clip asked for and not rendered is a refusal, not a smaller probe.
        print(f"REFUSED {failed} of {len(args.clips)} — the numbers above are "
              f"not about the batch that was asked for")
        return 2
    if not rows:
        print("REFUSED: nothing was rendered")
        return 2
    if band is None or state != scap.PRESENT:
        # The whole probe is about preserving a subtitle. Without a band there
        # is no subtitle to preserve and the size table is about nothing.
        print(f"REFUSED: the source-caption verdict is {state!r}, so there is "
              f"no band the framing could be preserving")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
