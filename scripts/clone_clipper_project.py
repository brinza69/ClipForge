"""Clone a clipper project so a stage can be re-run without touching the original.

Re-scoring a real project rewrites its `clips` rows, and those rows are what the
board shows and what the feedback log points at. A clone is how you satisfy a
gate that says "produce this artefact on both existing sources" without spending
someone else's data to do it.

    python scripts/clone_clipper_project.py slice4h00test
    python scripts/clone_clipper_project.py slice4h00test --id mytrial
    python scripts/clone_clipper_project.py --drop mytrial

WHAT IS COPIED. The project row, the transcript row, and the analysis artefacts
and sampled frames — HARDLINKED, not copied, so a 14 MB analysis directory costs
nothing. That is safe in both directions: `storage.write_artifact` writes a temp
file and renames it over the target, which replaces the clone's directory entry
and leaves the original file, still linked from the source project, untouched.

WHAT IS NOT COPIED. `clips` rows, feedback, exports and previews. A clone is for
re-deriving, not for inheriting a verdict — and copying the feedback would put
the same human judgement into the training set twice.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

_LINKED = ("analysis", "frames", "proxy", "audio", "source", "thumbs")


def _link_tree(src: Path, dst: Path) -> int:
    """Hardlink every file under `src` into `dst`. Falls back to copying on the
    filesystems that refuse (a hardlink across volumes, or a FAT target)."""
    if not src.is_dir():
        return 0
    linked = 0
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        target = dst / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        try:
            os.link(item, target)
        except OSError:
            shutil.copy2(item, target)
        linked += 1
    return linked


async def clone(source_id: str, clone_id: str) -> int:
    from database import async_session, init_db
    from models import ProjectModel, TranscriptModel
    from services.clipper import storage
    from sqlalchemy import select

    await init_db()

    async with async_session() as session:
        src = await session.get(ProjectModel, source_id)
        if src is None:
            print(f"no project {source_id}", file=sys.stderr)
            return 1
        if await session.get(ProjectModel, clone_id) is not None:
            print(f"{clone_id} already exists", file=sys.stderr)
            return 1

        session.add(ProjectModel(
            id=clone_id,
            title=f"[clone] {src.title or source_id}",
            source_url=src.source_url,
            source_type=src.source_type,
            source_kind=src.source_kind,
            status=src.status,
            processing_mode=src.processing_mode,
            clipper_settings=dict(src.clipper_settings or {}),
            rights_confirmed=src.rights_confirmed,
            analysis_version=src.analysis_version,
            duration=src.duration,
            width=src.width,
            height=src.height,
            fps=src.fps,
            content_type=src.content_type,
            content_type_confidence=src.content_type_confidence,
            content_type_override=src.content_type_override,
        ))

        row = (await session.execute(
            select(TranscriptModel)
            .where(TranscriptModel.project_id == source_id).limit(1)
        )).scalar_one_or_none()
        if row is None:
            print(f"{source_id} has no transcript — the score stage needs one",
                  file=sys.stderr)
            return 1
        session.add(TranscriptModel(
            project_id=clone_id,
            language=row.language,
            segments=row.segments,
            full_text=row.full_text,
        ))
        await session.commit()

    storage.ensure_dirs(clone_id)
    src_root = storage.paths(source_id)["root"]
    dst_root = storage.paths(clone_id)["root"]
    total = sum(_link_tree(src_root / name, dst_root / name) for name in _LINKED)

    print(f"cloned {source_id} -> {clone_id} ({total} files linked)")
    print(f"  {dst_root}")
    return 0


async def drop(clone_id: str) -> int:
    """Delete a clone: its rows, its clips, its feedback and its directory."""
    from database import async_session, init_db
    from models import ClipFeedbackModel, ClipModel, ProjectModel, TranscriptModel
    from services.clipper import storage
    from sqlalchemy import delete

    await init_db()
    async with async_session() as session:
        project = await session.get(ProjectModel, clone_id)
        if project is None:
            print(f"no project {clone_id}", file=sys.stderr)
            return 1
        # A guard rail, not politeness: the whole point of this script is that
        # the ORIGINALS survive, and a mistyped id here would delete one.
        if not str(project.title or "").startswith("[clone] "):
            print(f"{clone_id} is not a clone — refusing to delete it",
                  file=sys.stderr)
            return 1
        for model in (ClipFeedbackModel, ClipModel, TranscriptModel):
            await session.execute(
                delete(model).where(model.project_id == clone_id))
        await session.execute(
            delete(ProjectModel).where(ProjectModel.id == clone_id))
        await session.commit()

    root = storage.paths(clone_id)["root"]
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    print(f"dropped {clone_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", help="source project, or the clone to drop")
    parser.add_argument("--id", default=None, help="clone id (default: random)")
    parser.add_argument("--drop", action="store_true",
                        help="delete a clone made by this script")
    args = parser.parse_args()

    if args.drop:
        return asyncio.run(drop(args.project_id))
    return asyncio.run(clone(args.project_id,
                             args.id or f"cl{uuid.uuid4().hex[:10]}"))


if __name__ == "__main__":
    raise SystemExit(main())
