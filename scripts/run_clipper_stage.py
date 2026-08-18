"""Run one clipper pipeline stage in-process, correctly.

Three traps live between you and a working harness, and this exists because
all three were paid for on 2026-08-17 rather than read about:

1. **The schema.** `init_db()` runs ONLY in the app's lifespan, so a stage
   invoked in-process meets whatever schema the backend last started with.
   SQLAlchemy selects every mapped column, so a model one migration ahead of
   the DB breaks EVERY read of that table, not the new field. Four re-analysis
   runs died instantly on `no such column: transcripts.failed_chunks`, a column
   added the previous day. It self-heals when the backend restarts, which is
   what keeps it invisible to anyone working through the API.

2. **The backend on 8420 is not yours.** Two uvicorn processes claim the port
   and the winner runs on the system Python. The job queue is shared through
   the DB, so anything enqueued races a worker that may be on older code — it
   cost session 3 a whole analysis run. Running the handler here bypasses the
   queue entirely.

3. **`spawn` on Windows.** The transcriber starts its whisper worker with
   `mp.get_context("spawn")`, which re-imports the calling module in the child.
   Without the `__main__` guard at the bottom the child re-runs the whole
   pipeline from ingest and then dies, and the error surfaces as "the worker
   ran out of memory", which it had not.

    python scripts/run_clipper_stage.py analyze 39c89ae2e16e
    python scripts/run_clipper_stage.py export 39c89ae2e16e --clip 30d045851a64
    python scripts/run_clipper_stage.py score 39c89ae2e16e --keep-status

The project's status is restored afterwards unless `--keep-status` is given:
a stage that hands off to the next one leaves the project mid-pipeline, and
parking it in a state nobody chose is worse than leaving it alone.
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))

DB = _REPO / "data" / "db" / "clipforge.db"

STAGES = {
    "ingest": ("workers.clipper_pipeline", "handle_ingest"),
    "transcribe": ("workers.clipper_pipeline", "handle_transcribe"),
    "analyze": ("workers.clipper_pipeline", "handle_analyze"),
    "score": ("workers.clipper_build", "handle_score"),
    "export": ("workers.clipper_render_jobs", "handle_export"),
    "preview": ("workers.clipper_render_jobs", "handle_preview"),
}


class StubQueue:
    """What the handlers need from a queue, and nothing that touches the DB."""

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    async def update_progress(self, job_id, progress, message="") -> None:
        if not self.quiet:
            print(f"  {float(progress):5.0%}  {message}", flush=True)

    def is_cancelled(self, job_id) -> bool:
        return False

    async def enqueue(self, **kw) -> str:
        # Swallowed on purpose: a stage that enqueues the next one would hand
        # the work to the very worker this harness exists to avoid.
        if not self.quiet:
            print(f"  (would enqueue {kw.get('job_type')})", flush=True)
        return "stub"


def status_of(project_id: str) -> str | None:
    db = sqlite3.connect(DB)
    try:
        row = db.execute("select status from projects where id=?",
                         (project_id,)).fetchone()
        return row[0] if row else None
    finally:
        db.close()


def restore(project_id: str, status: str) -> None:
    db = sqlite3.connect(DB)
    try:
        db.execute("update projects set status=? where id=?", (status, project_id))
        db.commit()
    finally:
        db.close()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=sorted(STAGES))
    ap.add_argument("project_id")
    ap.add_argument("--clip", default=None, help="required by export and preview")
    ap.add_argument("--keep-status", action="store_true",
                    help="leave the project where the stage left it")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # FIRST, and the reason this file exists. Idempotent.
    from database import init_db

    await init_db()

    if args.stage in ("export", "preview") and not args.clip:
        ap.error(f"{args.stage} needs --clip")

    module_name, handler_name = STAGES[args.stage]
    module = __import__(module_name, fromlist=[handler_name])
    handler = getattr(module, handler_name)

    before = status_of(args.project_id)
    if before is None:
        print(f"no project {args.project_id}", file=sys.stderr)
        return 1

    print(f"=== {args.stage} on {args.project_id} (status {before})", flush=True)
    try:
        await handler("harness", args.project_id, args.clip, {},
                      StubQueue(args.quiet))
    finally:
        if not args.keep_status and before:
            restore(args.project_id, before)
    print("=== done", flush=True)
    return 0


if __name__ == "__main__":       # MANDATORY on Windows — see the module docstring
    raise SystemExit(asyncio.run(main()))
