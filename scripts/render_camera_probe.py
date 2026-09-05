"""The same clip planned with and without the geometric second camera.

    python scripts/render_camera_probe.py pilotf81b 30d7c6d4eae5

WHY IT EXISTS. `layout_policy` was reported on the strength of a PLAN
measurement — 48.1 s of delivered windows that exclude the subject, dropping to
0.0 s — and a plan is not the delivered artefact. Codex said so in the same
words the repo already uses about `move: push` and `caption_plan.y_pct`: the
guard stops the `game` family being CHOSEN, and that on its own demonstrates
neither "the rhythm is untouched" nor "the empty background is gone from the
export". This renders both, through `render_dynamic_clip`, so the second claim
is about a file.

AND IT ASKS THE FRAMES, NOT ONLY THE PLANS. `off_subject_seconds` compares a
delivered window with the clip's average face centre — geometry. Rendering two
files does not by itself turn that into an observation of the frames, so this
also runs the face detector over both renders and reports the difference between
them at the same timestamps.

WHAT IT REPORTS BESIDE THE FILES. The boundary times of both plans, matched
within 40 ms, because equal shot COUNTS prove nothing — the same number of cuts
at different moments is a different edit. And how many shots deliver the
same picture by `dynamic_geometry.visual_key`, measured over TIME rather than by
shot index — the moment one boundary disappears the shots stop corresponding
positionally, and comparing them by index measures an alignment error.

A boundary with no partner is also not a boundary that MOVED: both sides are
counted, and `only_in_b == 0` is what makes it a removal.

Writes into `<project>/camera_probe/`, never `exports/`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Two boundaries this far apart are the same cut. Plans are written with three
#: decimals and 40 ms is a frame and a half at 60 fps — below what a viewer can
#: attribute to anything.
SAME_CUT_S = 0.04


def _bounds(plan: dict) -> list[float]:
    return [round(float(s["t0"]), 3) for s in (plan.get("shots") or [])][1:]


def _match_boundaries(ba: list[float], bb: list[float]) -> dict:
    """`{matched, only_in_a, only_in_b}` — and a boundary with no partner is
    NOT thereby a boundary that moved.

    The first version reported `len(ba) - kept` as "moved", which is a claim
    about where a cut went. On `30d7c6d4eae5` eight boundaries coincide exactly
    and the one at 11.930 s simply DISAPPEARS: no new boundary shows up
    anywhere, so nothing moved and one cut was removed. Both sides are counted
    here precisely so a reader can tell those apart — `only_in_b` empty is what
    makes it a removal.
    """
    used: set[int] = set()
    matched = 0
    for t in ba:
        for i, u in enumerate(bb):
            if i not in used and abs(t - u) <= SAME_CUT_S:
                used.add(i)
                matched += 1
                break
    return {"matched": matched, "only_in_a": len(ba) - matched,
            "only_in_b": len(bb) - len(used)}


def _timeline(plan: dict) -> list[tuple[float, float, Any]]:
    """`[(t0, t1, visual_key)]` — the delivered picture over TIME."""
    from services.clipper import dynamic_geometry as dg

    style = plan.get("style") or {}
    sw, sh = int(plan["src_w"]), int(plan["src_h"])
    out = []
    for shot in plan.get("shots") or []:
        try:
            t0, t1 = float(shot["t0"]), float(shot["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((t0, t1, dg.visual_key(shot, style, sw, sh)))
    return out


def _same_picture_seconds(a: dict, b: dict) -> dict:
    """How many SECONDS the two plans deliver the same picture.

    NOT `zip(shots_a, shots_b)`, which is what the first version did: it
    compares positions in a list, and the moment one boundary disappears the
    shots stop corresponding by index, so "4 of 9 identical" was measuring an
    alignment error. Compared over the overlapping time intervals instead —
    on `30d7c6d4eae5` that is 11.305 s identical and 6.805 s different of
    18.110 s, a smaller change than the shot count suggested.

    It compares the framing COMMANDS, not decoded pixels. Two identical
    `visual_key`s schedule the same crop; whether the encoder produced the same
    bytes is `output_identity`'s question and the byte-identical control on
    `b23c14c41495` is the case where it happens to answer both.
    """
    ta, tb = _timeline(a), _timeline(b)
    same = diff = unknown = 0.0
    for a0, a1, ka in ta:
        for b0, b1, kb in tb:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi <= lo:
                continue
            if ka is None or kb is None:
                # A shot whose size timeline moves has no key. Not "different":
                # nobody can say, and folding it into either column would be an
                # answer nobody has.
                unknown += hi - lo
            elif ka == kb:
                same += hi - lo
            else:
                diff += hi - lo
    return {"same_s": round(same, 3), "different_s": round(diff, 3),
            "unknown_s": round(unknown, 3),
            "overlap_s": round(same + diff + unknown, 3)}


def _jumps(plan: dict) -> dict:
    """Apparent-size ratio between ADJACENT delivered windows.

    The crop's width is what sets the magnification — the output is a fixed
    1080 wide — so the ratio of neighbouring widths is how much bigger or
    smaller the picture suddenly reads. A `crop`-to-`fit` junction is the 3.16x
    a human already objected to; this is the same quantity at every cut.
    """
    from services.clipper import evidence_map as em

    sw, sh = int(plan.get("src_w") or 0), int(plan.get("src_h") or 0)
    style = plan.get("style") if isinstance(plan.get("style"), dict) else {}
    widths = []
    for shot in plan.get("shots") or []:
        crop = em.crop_window(shot, src_w=sw, src_h=sh, style=style)
        widths.append(None if isinstance(crop, str) else float(crop[2]))
    ratios = [max(a, b) / min(a, b) for a, b in zip(widths, widths[1:])
              if a and b]
    if not ratios:
        # NOT zero jumps. A clip whose windows could not be read has an unknown
        # amount of scale change in it.
        return {"pairs": None, "median": None, "worst": None, "over_1_5": None}
    ordered = sorted(ratios)
    return {"pairs": len(ratios), "median": ordered[len(ordered) // 2],
            "worst": max(ratios),
            "over_1_5": sum(1 for r in ratios if r >= 1.5)}


def _faces_in(path: Path, seconds: float, hz: float = 4.0) -> dict:
    """Is the speaker IN the delivered frames — read off the mp4, not the plan.

    `off_subject_seconds` is geometry: a delivered window compared with the
    clip's average face centre. Rendering two files does not turn that into an
    observation of frames, and this is the instrument that does.

    WHAT IT CANNOT SAY. A frame with no detected face is not proof the speaker
    is absent: the detector fails, and it fails hardest on a face at the edge of
    frame, which is exactly the population under test. So the two renders are
    compared against EACH OTHER at the same timestamps — the difference is the
    finding and the absolute count is a floor.

    AND A MISSING FILE IS NOT AN EMPTY RESULT. The first version of this had a
    broken path, the detector logged and returned nothing, and the summary
    printed "0 of 71 frames" for both files: a missing input rendered as a
    measurement of absence, inside the diagnostic written to catch that.
    """
    from services.clipper.signals import face_presence

    out: dict[str, Any] = {"sampled": 0, "seen": 0, "at": {}, "why": None}
    if not path.is_file():
        out["why"] = "the_render_is_not_there"
        return out
    times = [round(i / hz, 2) for i in range(1, max(2, int(seconds * hz)))]
    got = face_presence(str(path), times)
    if not got:
        out["why"] = "the_detector_returned_nothing_for_this_file"
        return out
    seen = {round(float(s.get("t", 0.0)), 2): bool(s.get("boxes")) for s in got}
    out["at"] = seen
    out["sampled"] = len(seen)
    out["seen"] = sum(1 for v in seen.values() if v)
    return out


async def _plans(project_id: str, clip_id: str):
    from database import init_db
    from workers.clipper_render_plan import _dynamic_plan, _load

    await init_db()
    clip, project = await _load(clip_id)
    w, h = int(project.width or 1920), int(project.height or 1080)
    with_second = await _dynamic_plan(clip, project, w, h,
                                      no_second_camera=False)
    without = await _dynamic_plan(clip, project, w, h, no_second_camera=True)
    return clip, project, with_second, without


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clips", nargs="+")
    args = ap.parse_args()

    from services.clipper import dynamic_render, layout_policy as lp
    from workers.clipper_render_plan import _source_path

    out_dir = DATA / args.project / "camera_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    refused = 0

    for clip_id in args.clips:
        clip, project, a, b = asyncio.run(_plans(args.project, clip_id))
        if not (a and b and a.get("shots") and b.get("shots")):
            print(f"{clip_id}: REFUSED — one of the two plans has no shots")
            refused += 1
            continue
        ba, bb = _bounds(a), _bounds(b)
        cuts = _match_boundaries(ba, bb)
        picture = _same_picture_seconds(a, b)
        oa, ob = lp.off_subject_seconds(a), lp.off_subject_seconds(b)

        src = _source_path(project)
        start = float(clip.start_time or 0.0)
        files: dict[str, str] = {}
        for name, plan in (("with-second-camera", a),
                           ("no-second-camera", b)):
            path = out_dir / f"{clip_id}.{name}.mp4"
            print(f"{clip_id}: rendering {name} "
                  f"({len(plan['shots'])} shots) ...", flush=True)
            dynamic_render.render_dynamic_clip(
                str(src), plan, str(path), start=start, work_dir=str(out_dir),
                ass_path=None,
                src_w=int(project.width or 1920),
                src_h=int(project.height or 1080))
            files[name] = str(path.resolve())

        da = [float(s["t1"]) - float(s["t0"]) for s in a["shots"]]
        db = [float(s["t1"]) - float(s["t0"]) for s in b["shots"]]
        # THE SCALE JUMPS, because a face-building alternation replaced by
        # face-to-larger-face is a different viewing experience even when the
        # cuts land at the same moments. Measured between ADJACENT shots, never
        # across the clip's extremes: a range and an adjacency claim in one
        # sentence measures only the range.
        jumps = {name: _jumps(plan) for name, plan in
                 (("with", a), ("without", b))}
        row = {
            "clip": clip_id, "files": files,
            "boundaries": {"with": len(ba), "without": len(bb), **cuts},
            "median_shot_s": [round(statistics.median(da), 2),
                              round(statistics.median(db), 2)],
            "same_picture": picture,
            "scale_jumps": jumps,
            "off_subject_s": [oa["seconds"], ob["seconds"]],
            # NOT a recommendation. Whether the replacement shot is the right
            # one is what the files are for.
            "verdict": None,
        }
        rows.append(row)
        print(f"  boundaries {len(ba)} -> {len(bb)}: {cuts['matched']} match "
              f"within {SAME_CUT_S * 1000:.0f}ms, {cuts['only_in_a']} only "
              f"before, {cuts['only_in_b']} only after"
              + ("  (removed, not moved: nothing new appeared)"
                 if cuts["only_in_a"] and not cuts["only_in_b"] else ""))
        print(f"  median shot {row['median_shot_s'][0]}s -> "
              f"{row['median_shot_s'][1]}s")
        print(f"  same picture for {picture['same_s']}s of "
              f"{picture['overlap_s']}s, different for "
              f"{picture['different_s']}s, unknown {picture['unknown_s']}s "
              f"(over TIME, not by shot index)")
        for name in ("with", "without"):
            j = jumps[name]
            if j["pairs"] is None:
                print(f"  scale jumps ({name}): none could be compared")
                continue
            print(f"  scale jumps ({name}): median {j['median']:.2f}, "
                  f"worst {j['worst']:.2f}, {j['over_1_5']} of {j['pairs']} "
                  f"pairs at 1.5x or more")
        print(f"  off-subject {oa['seconds']}s -> {ob['seconds']}s "
              f"(PLAN geometry, against the clip's AVERAGE face centre)")

        # AND THE SAME QUESTION ASKED OF THE FRAMES. The line above is a plan
        # measurement; rendering two files does not turn it into an observation
        # of the delivered picture. This one opens them.
        secs = sum(db)
        fa = _faces_in(Path(files["with-second-camera"]), secs)
        fb = _faces_in(Path(files["no-second-camera"]), secs)
        row["faces_in_frames"] = {"with": fa, "without": fb}
        if fa["why"] or fb["why"]:
            print(f"  frames: REFUSED — {fa['why'] or fb['why']}")
        else:
            shared = sorted(set(fa["at"]) & set(fb["at"]))
            gained = [t for t in shared if fb["at"][t] and not fa["at"][t]]
            lost = [t for t in shared if fa["at"][t] and not fb["at"][t]]
            row["faces_in_frames"]["gained_at"] = gained
            row["faces_in_frames"]["lost_at"] = lost
            print(f"  frames: face detected {fa['seen']}/{fa['sampled']} -> "
                  f"{fb['seen']}/{fb['sampled']};  present only after: "
                  f"{len(gained)}, only before: {len(lost)}")
        print()

    (out_dir / "probe.json").write_text(
        json.dumps({"project": args.project, "rows": rows, "verdict": None},
                   indent=1, default=str), encoding="utf-8")
    print(f"{len(rows)} clip(s) rendered both ways into {out_dir}")
    if refused or not rows:
        print(f"REFUSED {refused} of {len(args.clips)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
