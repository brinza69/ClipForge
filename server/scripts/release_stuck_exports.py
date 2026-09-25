"""Release Clipper clips stuck in `exporting` with no export job — by name, by a person.

Since R4b (data/claude-master-20260924/codex-verdict-wave2.md) a clip's claim,
`clips.export_job_id` and its job row commit together, and every path that ends
an export job frees its clip by that identity, so no new orphan forms. What is
left are claims from BEFORE: a backend that claimed and then failed to enqueue,
or startup recovery failing a job without freeing its clip. They carry no
attempt identity and none is invented here — no age rule, no guessed job, no
endpoint a UI could call blindly.

    python scripts/release_stuck_exports.py                        # list them
    python scripts/release_stuck_exports.py --release ID... --writers-stopped

Run the release only after every backend built before R4b is stopped: such a
writer claims first and writes its job a moment later, and until it does that
clip looks exactly like an orphan. `--writers-stopped` is the person saying so;
the run is refused anyway while any export job holds a live lease. Released
clips become `failed`, from which Export is allowed again.

Exit: 0 listed, or every named clip released; 1 some named clip refused (its
own row says why); 2 the whole run refused, nothing written.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, update  # noqa: E402

from database import async_session  # noqa: E402
from models import ClipModel, ClipStatus, JobModel, JobStatus, JobType  # noqa: E402
from services.clipper.clip_mutations import _live_export_job, begin_write  # noqa: E402

_EXPORTING = ClipStatus.exporting.value


async def _list(session) -> int:
    rows = (await session.execute(
        select(ClipModel.id, ClipModel.project_id, ClipModel.updated_at)
        .where(ClipModel.status == _EXPORTING).order_by(ClipModel.id))).all()
    orphans = [r for r in rows if await session.scalar(_live_export_job(r.id).limit(1)) is None]
    print(f"exporting clips: {len(rows)}")
    print(f"without a queued/running export job: {len(orphans)}")
    for r in orphans:
        print(f"{r.id}\t{r.project_id}\tupdated {r.updated_at}")
    return 0


async def _why_not(session, clip_id: str) -> str:
    clip = await session.get(ClipModel, clip_id, populate_existing=True)
    if clip is None:
        return "no such clip"
    if clip.status != _EXPORTING:
        return f"status is {clip.status}, not exporting"
    return "it has a queued/running export job"


async def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--release", nargs="+", default=[], metavar="CLIP_ID")
    parser.add_argument("--writers-stopped", action="store_true",
                        help="every backend built before R4b is stopped")
    args = parser.parse_args(argv)

    async with async_session() as session:
        if not args.release:
            return await _list(session)
        print(f"named: {len(args.release)}")
        if not args.writers_stopped:
            print("REFUSED\tpass --writers-stopped once every pre-R4b backend is "
                  "stopped; nothing was written")
            return 2
        # Under the write lock: no writer can claim or enqueue between the
        # lease check and the releases.
        await begin_write(session)
        live = await session.scalar(
            select(func.count()).select_from(JobModel)
            .where(JobModel.type == JobType.clipper_export.value,
                   JobModel.status == JobStatus.running.value,
                   JobModel.lease_expires_at > datetime.utcnow()))
        if live:
            await session.rollback()
            print(f"REFUSED\t{live} export job(s) hold a live lease: a writer is "
                  "running; nothing was written")
            return 2
        released = 0
        for clip_id in args.release:
            result = await session.execute(
                update(ClipModel)
                .where(ClipModel.id == clip_id, ClipModel.status == _EXPORTING,
                       ~_live_export_job(clip_id).exists())
                .values(status=ClipStatus.failed.value))
            if result.rowcount == 1:
                released += 1
                print(f"{clip_id}\treleased\trowcount 1")
            else:
                print(f"{clip_id}\trefused\t{await _why_not(session, clip_id)}")
        await session.commit()
    print(f"released: {released} of {len(args.release)}")
    return 0 if released == len(args.release) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
