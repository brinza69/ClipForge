"""Re-render stored exports with the CURRENT renderer, from their own sidecars.

    python scripts/rerender_pilots.py pilot2c8a pilot6b38
    python scripts/rerender_pilots.py --all --dry-run

WHY THIS EXISTS. The exports on disk were rendered before the caption fix of
31 August 2026, and 27 of the 88 stored clips have seconds with no caption at
all — `dynamic_render` burned the subtitles onto a frame whose letterbox bars
are transparent, so the text kept an alpha of zero and the overlay composited it
away. Every gate that reads those files is reading a picture the renderer would
no longer produce.

IT RE-RENDERS FROM THE SIDECAR, not from a re-analysis. The plan, the caption
file and the source window are exactly the ones that produced the original, so
the ONLY difference in the output is the renderer. That is what makes a
before/after comparison mean anything.

THE ORIGINALS ARE MOVED, NOT OVERWRITTEN. Each project's `exports/` is copied to
`exports_pre_caption_fix/` first, and the run refuses to start if that directory
already holds a copy — a second run would otherwise back up the NEW files over
the only record of the old ones, and the evidence for the defect would be gone.

A CLIP THAT CANNOT BE RE-RENDERED KEEPS ITS ROW AND FAILS THE RUN. It does not
vanish and it is not left half-written: the render goes to a temporary name and
is moved into place only when ffmpeg returns zero.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"
BACKUP = "exports_pre_caption_fix"



def _needed(side: dict) -> str | None:
    """Why this sidecar cannot be re-rendered, or None."""
    plan = side.get("dynamic_plan")
    if not isinstance(plan, dict) or not plan.get("shots"):
        return "no_dynamic_plan"
    if not (plan.get("src_w") and plan.get("src_h")):
        return "no_source_dimensions"
    src = (side.get("source") or {}).get("path")
    if not src or not Path(src).exists():
        return "source_missing"
    for key in ("source_start", "duration"):
        if not isinstance(side.get(key), (int, float)):
            return f"no_{key}"
    policy = side.get("caption_policy")
    if not isinstance(policy, dict) or policy.get("action") not in ("burn", "suppress"):
        return "caption_policy_missing_replan_instead_of_guessing_from_an_ass_file"
    options = side.get("render")
    if not isinstance(options, dict) or any(options.get(k) is None
            for k in ("fps", "crf", "preset", "watermark")):
        return "encoder_options_missing_replan_instead"
    return None


def _frozen_end_tail(path: Path, side: dict) -> dict | None:
    """EN3's scoring record, carried from the frozen sidecar (codex-verdict-next-29 §3), as the clip's
    `reasoning`: the new render binds it to ITS OWN window and file (`end_tail.sidecar_block`).

    Only `end_tail.recorded` is carried, with where it came from. The old `binding` and `delivered_s` describe
    the old file and are never copied; today's settings are never read. No block — a sidecar from before
    EN3T — is None, which binds `absent`. A block whose record is not one is carried as it is, and binds
    `invalid_record`: a record that cannot be read is not the same fact as no record.
    """
    block = side.get("end_tail")
    if block is None:
        return None
    rec = block.get("recorded") if isinstance(block, dict) else block
    if isinstance(block, dict) and block.get("binding") == "absent" and rec is None:
        return None
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = None
    return {"end_tail": rec if isinstance(rec, dict) else {"unreadable_record": rec},
            "end_tail_provenance": {"from": "frozen_sidecar", "sidecar": str(path), "sidecar_sha256": digest,
                                    "field": "end_tail.recorded"}}


def _render(path: Path, side: dict, dry: bool) -> dict:
    row = {"project": path.parent.parent.name, "clip": path.stem}
    why = _needed(side)
    if why:
        row["refused"] = why
        return row

    plan = side["dynamic_plan"]
    mp4 = path.with_suffix(".mp4")
    ass = path.with_suffix(".ass")
    row["had_ass"] = ass.exists()
    if dry:
        row["would_render"] = str(mp4)
        return row

    # Replay the stored decision. Reading today's project settings would make
    # this a re-plan under the old plan's name. An undeclared caption policy is
    # refused above: a stale ASS is not permission to burn another layer.
    source = side["source"]
    policy = side["caption_policy"]
    if policy["action"] == "burn" and not ass.is_file():
        row["refused"] = "burn_requested_but_ass_missing"
        return row
    clip = SimpleNamespace(
        id=side.get("clip_id") or path.stem, selection_run_id=side.get("selection_run_id"),
        start_time=side["source_start"], end_time=side.get("source_end"),
        duration=side["duration"], title=side.get("title"), headline_text=side.get("headline"),
        transcript_text=side.get("transcript"), overall_score=side.get("overall_score"),
        sub_scores=side.get("sub_scores"), score_reason=side.get("score_reason"),
        caption_plan=side.get("caption_plan"), content_type=side.get("content_type"),
        ranker_version=side.get("ranker_version"), reasoning=_frozen_end_tail(path, side))
    project = SimpleNamespace(id=row["project"], source_url=source.get("url"),
                              width=plan["src_w"], height=plan["src_h"],
                              analysis_version=side.get("analysis_version"))
    decision = {"dyn": plan, "plan": side.get("layout_plan"),
                "fps": side["render"]["fps"], "render": side["render"],
                "watermark": side["render"]["watermark"],
                "drop": side.get("drop_spans"), "caption_y": side.get("caption_y"),
                "ass_path": str(ass) if policy["action"] == "burn" else None,
                "caption_policy": policy,
                **{k: side.get(k) for k in ("layout_policy", "edit_profile", "creator_view",
                                           "regime_view", "rhythm_view")}}
    from workers.clipper_render_output import render_export

    started = time.time()
    try:
        result = asyncio.run(render_export(clip, project, decision, mp4, src=source["path"]))
    except Exception as exc:
        row["refused"] = f"{type(exc).__name__}: {exc}"
    else:
        row["bytes"] = result["size"]
    row["seconds"] = round(time.time() - started, 1)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    projects = sorted({p.parent.parent.name
                       for p in DATA.glob("*/exports/*.json")})
    missing: list[str] = []
    if not args.all:
        wanted = list(dict.fromkeys(args.projects))
        if not wanted:
            ap.error("name a project or pass --all")
        missing = [n for n in wanted if n not in projects]
        projects = [n for n in projects if n in set(wanted)]

    bad = [f"project asked for and not found: {n}" for n in missing]
    rows: list[dict] = []

    for name in projects:
        exports = DATA / name / "exports"
        if not args.dry_run:
            # PRESERVE THE GENERATION THIS RUN REPLACES, into a directory that
            # does not exist. The version before this REFUSED when the backup
            # was there, which protected the data and meant a second run could
            # never preserve anything at all — the mirror of the bug in
            # `replan_and_rerender`, and the reason both now share one
            # implementation.
            from services.clipper import export_generations as eg

            got = eg.preserve(DATA / name, label="pre_caption_fix")
            if got["why"]:
                bad.append(f"{name}: nothing preserved — {got['why']}")
                continue
            print(f"{name}: preserved {got['files']} files "
                  f"({got['bytes'] / 1e6:.0f} MB) to "
                  f"{Path(got['destination']).name}/", flush=True)

        for path in sorted(exports.glob("*.json")):
            try:
                side = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                rows.append({"project": name, "clip": path.stem,
                             "refused": f"sidecar_unreadable_{type(exc).__name__}"})
                continue
            row = _render(path, side if isinstance(side, dict) else {},
                          args.dry_run)
            rows.append(row)
            # PRINTED AS IT GOES. A render run is long, and a report that only
            # arrives at the end is a report an interruption destroys.
            mark = row.get("refused") or f"{row.get('seconds', 0)}s"
            print(f"  {row['project']}/{row['clip']:14} {mark}", flush=True)

    refused = [r for r in rows if r.get("refused")]
    for r in refused:
        bad.append(f"{r['project']}/{r['clip']}: {r['refused']}")
    if not rows and not bad:
        bad.append("nothing to render — a pass over nothing is not a pass")

    print()
    print(f"clips {len(rows)}   refused {len(refused)}")
    print(f"seconds spent {round(sum(r.get('seconds', 0) for r in rows))}")
    for line in bad:
        print(f"FAIL: {line}")
    if not bad:
        print("every clip re-rendered; the originals are in "
              f"<project>/{BACKUP}/")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
