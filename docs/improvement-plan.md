# ClipForge — Improvement Plan

> **Audience:** A future Claude (or other) session that will execute these
> tasks. This document is intentionally over-specified so a weaker model
> can follow it without making design decisions.
>
> **Last updated:** 2026-06-07 (after the Sheets / Settings / Whisper-UI
> session). The branch state at write time is `claude/parallel-processing`
> with PR #21 open.

---

## 0. Required reading before you start

Read these IN THIS ORDER. Do not skim — the rules matter.

1. **`/CLAUDE.md`** — project conventions. Pay special attention to:
   - Rule 2: file size limit 500 lines (hard rule; split before you cross)
   - Rule 4: any new clip field flows through 4 layers
   - Rule 6: captioner overrides
   - Rule 8: no speculative abstractions
   - Rule 9: commit format `feat(scope): description` or `fix(scope): description`
   - Rule 10: token efficiency — do NOT re-read whole files; use Grep/Glob
2. **`/docs/session-handover.md`** — full context of what was built in sessions 1 + 2.
3. **`/SESSION-HANDOVER.md`** — root-level mirror of the above (same content).
4. **This file** — the work queue below.

After reading, run `git log --oneline -20` to see the recent commits and
their style. Match that style for your own commits.

---

## 1. Project orientation (1-minute version)

- **Frontend** (`src/`): Next.js 15 Turbopack on `:3000`. Pages call backend
  through `/worker-api/*` proxy (rewrite in `next.config.ts` → `:8420/api/*`).
  **Never** call `localhost:8420` directly — some browser extensions kill
  cross-port fetches.
- **Backend** (`server/`): FastAPI on `:8420`, async, SQLite via
  `aiosqlite`. Entrypoint `main.py`. Routers in `routers/`, business logic
  in `services/`, pipeline orchestrators in `workers/`.
- **Pipeline**: `download → transcribe → erase ∥ TTS → speed-match → caption-burn → commentator`.
  Two orchestrators: `remix_pipeline.py` (single output) and
  `parallel_pipeline.py` (N outputs sharing the front half).
- **DB**: SQLite at `data/db/clipforge.db`. Tables: `projects`, `clips`
  (dormant since S2.9), `jobs`. Migrations are inline `ALTER TABLE` in
  `database.py:init_db()`.
- **Models / configs persisted on disk**:
  - `data/tts_config.json` — ElevenLabs key
  - `data/transcript_config.json` — OpenAI + Anthropic keys
  - `data/drive_oauth_client.json` — Google OAuth Desktop client
  - `data/drive_oauth_token.json` — Google OAuth user token
  - `data/sheets_config.json` — Sheets integration config + `next_row`
  - `data/whisper_config.json` — Whisper model + device overrides
  - `data/variant_presets/*.json` — saved variant bundles
  - `data/caption_templates/*.json` — caption style templates
  - `data/commentators/*/` — commentator video assets
  - All of `data/` is gitignored.

---

## 2. Dev workflow

Backend + frontend launcher: `./dev.sh start|stop|restart|status|logs`.
Designed for WSL/Linux. Logs at `logs/{backend,frontend}.log`.

Backend now starts with `uvicorn --reload`, so most `.py` edits hot-reload.
A backend restart is only required when adding a NEW file the import
graph hasn't seen.

If a port is stuck (zombie process), `./dev.sh stop` now force-kills it
(`fuser -k 8420/tcp` fallback).

```bash
./dev.sh restart           # both services
./dev.sh restart backend   # backend only
./dev.sh logs backend      # tail backend log
./dev.sh status
```

If Turbopack misses changes:
```bash
./dev.sh stop && rm -rf .next && ./dev.sh start
```
Then **close the tab and reopen** (not just hard-refresh).

---

## 3. Conventions you MUST follow

### Commits
- Format: `feat(scope): desc`, `fix(scope): desc`, `refactor(scope): desc`,
  `perf(scope): desc`, `chore(scope): desc`, `docs(scope): desc`.
- One coherent feature per commit. Multiple small fixes can group under one
  commit IF they're in the same area (e.g. all dev.sh fixes together).
- Always include this trailer:
  ```
  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  ```
- Never `--amend` after a hook fails. Make a new commit.
- Never `--no-verify`.

### Frontend
- All fetches: `/worker-api/...` (never `localhost:8420`).
- Sliders: `value={[x]}` + `onValueChange={([v]) => ...}` — always array form.
- Toast strings: title in title case ("Save failed"); description in
  sentence case.
- Files ≤ 500 lines. If a file approaches it, extract a component into
  `src/components/<area>/...`.

### Backend
- Async functions everywhere FastAPI is involved.
- Blocking calls (subprocess, googleapiclient, faster-whisper) wrapped in
  `loop.run_in_executor(None, fn)` or moved to subprocesses.
- All new DB columns: add to BOTH `models.py` AND `database.py:_clip_migrations`.
- Files ≤ 500 lines.

### Tooling
- TypeScript check: `node node_modules/typescript/bin/tsc --noEmit`.
- Python syntax check: `python -m py_compile path/to/file.py`.
- Bash syntax check: `bash -n dev.sh`.
- ALWAYS run all three after a multi-file change.

---

## 4. Priority tiers

- **P0**: Affects the user TODAY in a way they'll notice within a week.
  Do these first.
- **P1**: Quality / safety. Won't break the app but worth doing soon.
- **P2**: Polish + long-term hygiene. Optional unless paired with a
  P0/P1 in the same area.

---

## 5. Tasks

For each task below: read it fully, do the work, run the verification
checks, commit, push. Then move to the next.

> **Tip:** at the start, list all P0 tasks. Pick the first one with no
> incomplete dependencies (the "Depends on" line). When done, mark
> it `[x]` in this file before moving on.

---

### [ ] T1. Subprocess timeouts (P0)

**Goal:** No subprocess call can hang the pipeline forever.

**Why:** 20+ FFmpeg / yt-dlp / fontconfig calls across 13 files run
without timeouts. A hung ffmpeg blocks the job indefinitely; the job
queue's stuck-recovery only kicks in after 30 min.

**Files to touch (all in `server/`):**
- `services/bg_removal.py` (lines around 103, 176, 227)
- `services/caption_overlays.py` (lines 236, 252, 317)
- `services/commentators.py` (lines 147, 165, 184)
- `services/commentator_overlay.py` (lines 177, 195)
- `services/downloader.py` (line 305 — yt-dlp probe call)
- `services/inpaint.py` (lines 94, 102, 385, 391 — note 385/391 are `Popen`, not `run`)
- `services/metadata.py` (line 79)
- `services/silence_remover.py` (lines 186, 200, 269)
- `services/speed_match.py`
- `workers/parallel_pipeline.py` (line ~101 in `_split_video`)
- `workers/remix_pipeline.py` (search `subprocess.run` — multiple)
- `workers/utility_jobs.py`
- `services/transcriber.py` (line 269 in `_split_audio_to_chunks`)

**Steps:**

1. Find every `subprocess.run(...)` without a `timeout=` kwarg:
   ```bash
   grep -n "subprocess.run" server/services/ server/workers/ | grep -v "timeout="
   ```

2. For each one, decide the right timeout based on what the command does:
   - **ffprobe** (metadata probe, milliseconds): `timeout=30`
   - **ffmpeg single-file probe / preview frame** (seconds): `timeout=60`
   - **ffmpeg encode pass** (minutes for long clips): `timeout=1200` (20 min)
   - **ffmpeg split into parts** (per-part): `timeout=300` (5 min)
   - **yt-dlp probe** (network-bound): `timeout=60`
   - **yt-dlp download** (network + decode): `timeout=900` (15 min)
   - **fontconfig listing**: `timeout=10`

3. Wrap each with try/except `subprocess.TimeoutExpired` and raise a clear
   exception so the job_queue marks the job as failed with a useful
   message. Example pattern:
   ```python
   try:
       r = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=300, creationflags=_creationflags())
   except subprocess.TimeoutExpired as e:
       raise RuntimeError(
           f"ffmpeg timed out after {e.timeout}s on {cmd[0]}. "
           f"Last stderr: {(e.stderr or '')[-200:] if e.stderr else 'n/a'}"
       )
   ```

4. For `subprocess.Popen` (inpaint.py lines 385, 391 — the streaming
   decoder/encoder pair): Popen doesn't support `timeout=`. Add a
   wall-clock check inside the read loop:
   ```python
   import time
   start = time.time()
   while True:
       chunk = dec.stdout.read(BUF)
       if not chunk:
           break
       if time.time() - start > 1800:  # 30 min cap for the whole encode
           dec.kill(); enc.kill()
           raise RuntimeError("Inpaint encode exceeded 30-minute wall clock")
       enc.stdin.write(chunk)
   ```

**Acceptance:**
- `grep -rn "subprocess.run" server/services/ server/workers/ | grep -v "timeout=" | grep -v ".venv"` returns ZERO lines.
- Backend boots and a normal Remix run still completes (no false-positive timeouts).

**Estimated effort:** 1.5–2 hours. ~25 call sites.

**Commit message:** `fix(pipeline): subprocess timeouts everywhere — no more silent hangs`

---

### [ ] T2. Retry external API calls (P0)

**Goal:** A transient 5xx from ElevenLabs / OpenAI / Anthropic / Ollama
must not kill a 10-minute pipeline run.

**Why:** Today, any non-200 from these APIs surfaces as a top-level
exception and the whole job fails. Users have to re-run from scratch.

**Files:**
- `server/services/elevenlabs.py`
- `server/services/transcript_cleaner.py`
- New file: `server/services/retry.py` (shared helper)

**Steps:**

1. Create `server/services/retry.py`:
   ```python
   """Shared retry decorator for external HTTP APIs."""
   from __future__ import annotations
   import asyncio
   import logging
   import random
   from typing import Callable, TypeVar, Awaitable

   import httpx

   logger = logging.getLogger("clipforge.retry")
   T = TypeVar("T")

   # 4xx errors are NEVER retried (auth, quota, bad request).
   # 5xx + connection errors ARE retried with exponential backoff.
   _RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
   _RETRYABLE_EXC = (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)


   async def with_retry(
       fn: Callable[[], Awaitable[T]],
       *,
       max_attempts: int = 4,
       base_delay: float = 1.5,
       label: str = "api call",
   ) -> T:
       """Retry an async callable with exponential backoff + jitter.
       Raises the LAST exception if all attempts fail."""
       last: Exception | None = None
       for attempt in range(1, max_attempts + 1):
           try:
               return await fn()
           except httpx.HTTPStatusError as e:
               status = e.response.status_code
               if status not in _RETRYABLE_HTTP_STATUS or attempt == max_attempts:
                   raise
               last = e
               wait = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
               logger.warning(
                   f"{label}: {status} on attempt {attempt}/{max_attempts}, "
                   f"retrying in {wait:.1f}s"
               )
               await asyncio.sleep(wait)
           except _RETRYABLE_EXC as e:
               if attempt == max_attempts:
                   raise
               last = e
               wait = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
               logger.warning(
                   f"{label}: {type(e).__name__} on attempt {attempt}/{max_attempts}, "
                   f"retrying in {wait:.1f}s"
               )
               await asyncio.sleep(wait)
       if last:
           raise last
       raise RuntimeError(f"{label}: retry loop exited without success")
   ```

2. Wrap each external API call. For ElevenLabs `services/elevenlabs.py`:
   - `list_voices()` — wrap the `client.get(...)` block in `with_retry`
   - `get_user_info()` — same
   - `synthesize()` — same (CRITICAL — this is the one that wastes most
     pipeline time when it fails)

   Example pattern for `synthesize`:
   ```python
   from services.retry import with_retry

   async def synthesize(text: str, voice_id: str, output_path: str, ...) -> str:
       ...
       async def _call():
           async with httpx.AsyncClient(timeout=180.0) as client:
               r = await client.post(
                   f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}",
                   headers={...},
                   json=payload,
               )
               r.raise_for_status()  # let with_retry see the 5xx
               return r.content

       try:
           content = await with_retry(_call, label=f"ElevenLabs TTS voice={voice_id}")
       except httpx.HTTPStatusError as e:
           ...  # existing error mapping
   ```

3. For `transcript_cleaner.py`: find the openai/anthropic HTTP calls
   (search `httpx.AsyncClient` or `client.post` inside the openai/
   anthropic functions). Wrap them the same way.

4. For Ollama (`http://localhost:11434/...`): also retry, but with
   `max_attempts=2` since it's local (a 503 from Ollama probably means
   the model crashed; retrying once is enough).

**Acceptance:**
- A simulated 503 in `synthesize` (temporarily raise it on first call)
  triggers a retry and the second call succeeds. Undo the simulation
  before committing.
- Backend log shows "retrying in X.Xs" lines on the simulated failure.

**Estimated effort:** 1.5 hours.

**Commit message:** `fix(api): retry transient 5xx errors with exponential backoff`

---

### [ ] T3. Cancel cleans up intermediate files (P0)

**Goal:** When a user cancels a job mid-pipeline, the intermediate
files (downloaded source, erased video, per-variant working dirs) are
deleted instead of staying on disk forever.

**Why:** Right now cancel = silent disk leak.

**Files:**
- `server/job_queue.py` — `cancel_job` method
- `server/workers/remix_pipeline.py` — handle CancelledError finally block
- `server/workers/parallel_pipeline.py` — same

**Steps:**

1. In `JobQueue.cancel_job`, after marking the job cancelled, fetch the
   project_id and call a new helper `cleanup_job_workspace(project_id)`.

2. New helper in `server/services/cleanup.py` (new file):
   ```python
   """Disk cleanup for cancelled / failed jobs."""
   import logging
   import shutil
   from pathlib import Path
   from config import settings

   logger = logging.getLogger("clipforge.cleanup")


   def cleanup_job_workspace(project_id: str) -> dict:
       """Remove the project's media/ subtree but keep finalized exports.
       Returns a small stats dict."""
       freed = 0
       removed = []
       media = Path(settings.media_dir) / project_id
       if media.exists():
           try:
               size = _dir_size(media)
               shutil.rmtree(media, ignore_errors=True)
               freed += size
               removed.append(str(media))
           except Exception as e:
               logger.exception(f"could not remove {media}: {e}")
       return {"freed_bytes": freed, "removed": removed}


   def _dir_size(p: Path) -> int:
       total = 0
       for sub in p.rglob("*"):
           try:
               total += sub.stat().st_size
           except OSError:
               pass
       return total
   ```

3. Wire it into `cancel_job` after the DB update:
   ```python
   # Cleanup AFTER the status flip so the UI sees "cancelled" right away,
   # even before the disk work finishes.
   if job and job.project_id:
       try:
           from services.cleanup import cleanup_job_workspace
           import asyncio
           loop = asyncio.get_event_loop()
           stats = await loop.run_in_executor(
               None, lambda: cleanup_job_workspace(job.project_id)
           )
           logger.info(f"Cancelled job {job_id}: freed {stats['freed_bytes']} bytes")
       except Exception:
           logger.exception(f"cleanup after cancel of {job_id} failed")
   ```

4. Same for `fail_job` — failed jobs should also clean their workspace.
   Pass a flag if you want different policy; for now: same behavior.

5. DO NOT clean for jobs that completed successfully (status=done) — those
   have outputs the user wants to download.

**Acceptance:**
- Start a Remix run, hit Cancel mid-erase.
- Verify `data/media/{project_id}/` no longer exists.
- Verify `data/exports/{job_id}/` (if any) still exists (no exports yet
  on cancel-mid-erase — that's fine).
- A successful run does NOT trigger cleanup.

**Estimated effort:** 1 hour.

**Commit message:** `fix(jobs): clean up project media on cancel + fail`

---

### [ ] T4. Cancel propagates through erase + caption-burn (P0)

**Goal:** When a user clicks Cancel, the pipeline stops within ~3 seconds,
not at the end of the current ffmpeg invocation (which can take 30+).

**Why:** Today cancel works for download + transcribe, but inpaint /
caption-burn / commentator stages keep running.

**Files:**
- `server/services/inpaint.py` — already takes `is_cancelled` callback? Check.
- `server/workers/remix_pipeline.py` — `_stage_erase`, `_stage_match_and_caption`,
  `_stage_commentator`
- `server/workers/parallel_pipeline.py` — same stages

**Steps:**

1. Check current state:
   ```bash
   grep -rn "is_cancelled" server/services/ server/workers/
   ```
   Currently propagated to: downloader, transcriber. Confirm what else needs it.

2. For `inpaint.py`: inside the batch processing loop, check
   `is_cancelled()` between batches. If True, kill the ffmpeg subprocesses
   (the Popen pair) and raise `JobCancelledError` from
   `server/job_queue.py`.

3. For `_stage_match_and_caption` (fused encode pass): currently a single
   ffmpeg invocation that runs to completion. To make it cancellable:
   - Switch from `subprocess.run` to `subprocess.Popen` with stdout/stderr
     piped to a thread
   - Poll `is_cancelled()` every 1s in a loop checking `proc.poll()`
   - On cancel, `proc.terminate()` then `proc.wait(timeout=5)` then
     `proc.kill()`

4. For `_stage_commentator`: same Popen pattern.

5. Update the stage signatures to accept `is_cancelled: Callable[[], bool]`
   parameter, and pass `lambda: queue.is_cancelled(job_id)` from the
   pipeline orchestrator.

**Acceptance:**
- Start a Remix run, wait for "Erasing" stage to be active.
- Click Cancel. Within 3 seconds the job status flips to `cancelled`
  in the UI.
- The ffmpeg/python processes terminate (verify with `ps -ef | grep ffmpeg`
  showing nothing related to the job).

**Estimated effort:** 1.5–2 hours.

**Commit message:** `fix(pipeline): propagate cancel through erase + caption-burn + commentator`

---

### [ ] T5. Better stuck-job recovery (P1)

**Goal:** On backend startup, ALL jobs in `running` state are checked
and either requeued or marked failed — not just ones older than 30 min.

**Why:** With `--reload` in dev mode the backend restarts frequently;
the 30-min threshold leaves running jobs stuck in "running" status until
half an hour passes, during which the UI shows them as still running.

**Files:**
- `server/job_queue.py` — `recover_stuck_jobs`

**Steps:**

1. Replace the time-threshold logic with: requeue any `status=running`
   job. Set progress to `min(current, 0.05)` and clear error. Log how
   many.

2. Keep the project-terminal check (jobs whose project is already
   cancelled/failed → mark the job failed, don't requeue).

3. Add a note in the recovered job's `progress_message`: "Recovered
   from backend restart at {timestamp} — requeued."

4. Optionally: cap how many recoveries we do per startup (sanity check
   in case the table has thousands of orphans). Refuse if > 50; log a
   warning.

**Acceptance:**
- Start a Remix run, wait until it's in "Erasing" stage.
- `kill -9` the uvicorn process. Restart backend.
- The job should be re-queued and start over (or fail cleanly if
  retries hit a limit).
- The UI no longer shows "running" forever.

**Estimated effort:** 30 min.

**Commit message:** `fix(jobs): requeue ALL running jobs on startup, not just >30min stuck`

---

### [ ] T6. Untracked background tasks → use job queue (P1)

**Goal:** TTS and Transcript jobs use the same JobQueue as Remix/Parallel
instead of fire-and-forget `asyncio.create_task`.

**Why:** Today `routers/tts.py:_run_tts_job` and `routers/transcript.py:_run_clean_job`
are started with `asyncio.create_task` and not tracked. Backend crash
mid-job = silent orphan; no cancel possible; not recovered on restart.

**Files:**
- `server/routers/tts.py`
- `server/routers/transcript.py`
- `server/models.py` — add new JobTypes
- `server/database.py` — migration for any new columns (probably none)
- `server/workers/utility_jobs.py` (or new `workers/standalone_jobs.py`)

**Steps:**

1. Add JobTypes in `models.py`:
   ```python
   tts_synthesize = "tts_synthesize"
   transcript_clean = "transcript_clean"
   ```

2. Move the body of `_run_tts_job` into a new handler in
   `server/workers/standalone_jobs.py` (or extend `utility_jobs.py`).
   Same for `_run_clean_job`.

3. Register the handlers in `main.py` lifespan.

4. In the routers, replace `asyncio.create_task(_run_x_job(...))` with:
   ```python
   from job_queue import job_queue
   job_id = await job_queue.enqueue(
       project_id="standalone-tts",  # or a real project id
       job_type=JobType.tts_synthesize.value,
       metadata={...request payload...},
   )
   return {"job_id": job_id}
   ```

5. The existing `_jobs` in-memory dicts in tts.py / transcript.py
   become redundant — the JobQueue + DB does the same. Delete them
   and update `GET /jobs/{id}` to use the standard `/api/jobs/{id}`
   endpoint instead.

**Acceptance:**
- POST `/api/tts/synthesize` returns a job_id that's pollable at
  `/api/jobs/{id}`.
- Mid-job backend restart → on restart, the job is requeued (per T5).
- No more `_jobs` dicts in tts.py / transcript.py.

**Estimated effort:** 2 hours.

**Commit message:** `refactor(jobs): unify TTS + Transcript jobs under the JobQueue`

---

### [ ] T7. Graceful SIGTERM handling (P1)

**Goal:** When uvicorn gets SIGTERM (Ctrl-C, systemd stop, etc.),
running jobs are marked `interrupted` cleanly before exit, not left
half-done in `running` state.

**Files:**
- `server/job_queue.py` — `stop()` method
- `server/main.py` — lifespan

**Steps:**

1. In `JobQueue.stop()`:
   ```python
   async def stop(self):
       self._stop_event.set()
       # Mark in-flight jobs as interrupted so the UI shows clearly what happened
       # and the next startup's stuck-recovery picks them up cleanly.
       if self._running_jobs:
           async with async_session() as session:
               for job_id in list(self._running_jobs.keys()):
                   await session.execute(
                       update(JobModel)
                       .where(JobModel.id == job_id)
                       .values(
                           progress_message="Interrupted by backend shutdown.",
                           updated_at=datetime.utcnow(),
                       )
                   )
               await session.commit()
       # Then cancel the tasks (with a short wait so they can at least flush DB)
       for task in self._running_jobs.values():
           task.cancel()
       # Give them up to 5s to finish
       try:
           await asyncio.wait_for(
               asyncio.gather(*self._running_jobs.values(), return_exceptions=True),
               timeout=5,
           )
       except asyncio.TimeoutError:
           pass
       self._running_jobs.clear()
       logger.info("Job queue processor stopped")
   ```

2. The lifespan in `main.py` already calls `job_queue.stop()` on shutdown.
   Confirm it's awaited (it is).

**Acceptance:**
- Start a job. Ctrl-C uvicorn (SIGINT).
- The job's `progress_message` reads "Interrupted by backend shutdown".
- On next startup, T5's recovery requeues it.

**Estimated effort:** 30 min.

**Commit message:** `feat(jobs): mark in-flight jobs as interrupted on backend shutdown`

---

### [ ] T8. Running-jobs indicator in sidebar (P1)

**Goal:** No matter which page the user is on, a small badge in the
sidebar shows how many jobs are running. Click → go to the job's page.

**Why:** User opens a new tab during a 10-min pipeline → has to navigate
to find progress. Annoying.

**Files:**
- `src/components/layout/sidebar.tsx`
- New: `src/components/layout/running-jobs-badge.tsx`
- Possibly new backend endpoint: `GET /api/jobs/active`

**Steps:**

1. Backend: ensure there's an endpoint that returns active jobs.
   Check `routers/jobs.py` — there's a `list` endpoint with `?status=running`
   filter. Should already work.

2. New component `running-jobs-badge.tsx`:
   ```tsx
   "use client";
   import { useEffect, useState } from "react";
   import Link from "next/link";
   import { Loader2 } from "lucide-react";

   interface ActiveJob {
     id: string;
     type: string;
     progress: number;
     progress_message: string;
   }

   export function RunningJobsBadge() {
     const [jobs, setJobs] = useState<ActiveJob[]>([]);

     useEffect(() => {
       let cancelled = false;
       const tick = async () => {
         try {
           const r = await fetch("/worker-api/jobs?status=running");
           if (!r.ok) return;
           const j = await r.json();
           if (!cancelled) setJobs(Array.isArray(j) ? j : (j.jobs || []));
         } catch {}
       };
       tick();
       const id = setInterval(tick, 3000);
       return () => { cancelled = true; clearInterval(id); };
     }, []);

     if (jobs.length === 0) return null;

     // Pick the route to link to based on the job type
     const j = jobs[0];
     const href =
       j.type === "parallel_pipeline" ? "/parallel-sheets" :
       j.type === "remix_pipeline" ? "/remix" :
       "/utilities";

     return (
       <Link
         href={href}
         className="mx-3 mb-2 flex items-center gap-2 rounded-md bg-primary/10 border border-primary/30 px-3 py-2 text-xs text-primary hover:bg-primary/15"
       >
         <Loader2 className="h-3 w-3 animate-spin" />
         <span className="flex-1 truncate">
           {jobs.length === 1
             ? `${j.progress_message || "Running"} (${Math.round(j.progress * 100)}%)`
             : `${jobs.length} jobs running`}
         </span>
       </Link>
     );
   }
   ```

3. Mount in `sidebar.tsx` — place it BETWEEN the nav list and the footer.
   Below the nav items, above the "Ready" footer.

**Acceptance:**
- Open `/settings`. Start a Remix run in another tab.
- The badge appears in the sidebar of `/settings` with progress %.
- Click → navigates to the job's page.

**Estimated effort:** 45 min.

**Commit message:** `feat(ui): running-jobs badge in sidebar for cross-page visibility`

---

### [ ] T9. Past-runs panel: pagination + delete (P1)

**Goal:** User can browse all past runs (not just last 10) and delete
ones they no longer need.

**Files:**
- `src/components/remix/past-runs.tsx` (already extracted)
- `server/routers/remix.py` — extend `/recent` endpoint with `?offset=` +
  add `DELETE /api/remix/{job_id}`

**Steps:**

1. Backend `routers/remix.py`:
   - `/recent` should accept `limit` AND `offset`. Default limit=10.
     Return `{runs, total}` so the UI can paginate.
   - New endpoint `DELETE /api/remix/{job_id}` that:
     - Deletes the file at `meta.results[0].final_path` if it exists
     - Deletes the project's media dir (`data/media/{project_id}`)
     - Deletes the JobModel row
     - Returns `{ok: true, freed_bytes: ...}`
   - Same for `parallel.py` if you have time.

2. Frontend `past-runs.tsx`:
   - Add `[offset, setOffset]` state
   - Show "Showing X-Y of Z" + Prev/Next buttons
   - Each run gets a small `×` button that calls DELETE + reloads
   - Confirm with `window.confirm("Delete this run + its files? (~XXX MB)")`

**Acceptance:**
- 15+ completed runs in DB. Past-runs panel paginates.
- Delete a run, confirm, files disappear from disk, list refreshes.

**Estimated effort:** 1 hour.

**Commit message:** `feat(remix): paginate past runs + add delete-with-files`

---

### [ ] T10. Drive Connect polling refactor (P2)

**Goal:** Replace the dual-interval poll hack in `drive-setup-card.tsx`
with a single clean polling loop.

**Why:** Current code has TWO `setInterval` running simultaneously
that race to update state. Works most of the time but fragile.

**Files:**
- `src/components/settings/drive-setup-card.tsx`

**Steps:**

1. Find the `connect` function in drive-setup-card.tsx (~line 60).
   It has the dual-interval pattern.

2. Replace with single polling loop using a ref to avoid stale closures:
   ```tsx
   const connect = async () => {
     setConnecting(true);
     try {
       const r = await fetch("/worker-api/drive-auth/connect", { method: "POST" });
       const j = await r.json();
       if (!r.ok || !j.auth_url) throw new Error(j.detail || "Could not start");
       window.open(j.auth_url, "_blank", "noopener");
       toast.info("Complete the Google login in the new tab…");

       const deadline = Date.now() + 180_000;
       const poll = setInterval(async () => {
         try {
           const sr = await fetch("/worker-api/drive-auth/status");
           const s: Status = await sr.json();
           setStatus(s);
           if (s.connected) {
             clearInterval(poll);
             setConnecting(false);
             toast.success(`Drive connected${s.email ? ` as ${s.email}` : ""}`);
           } else if (Date.now() > deadline) {
             clearInterval(poll);
             setConnecting(false);
           }
         } catch {}
       }, 2000);
     } catch (e: any) {
       setConnecting(false);
       toast.error("Drive connect failed", { description: e.message });
     }
   };
   ```

3. Same pattern for the dual poll in `connect` of any other place that
   uses it (probably none, but search).

**Acceptance:**
- Drive Connect works as before.
- React strict mode / re-renders don't cause double-poll.

**Estimated effort:** 20 min.

**Commit message:** `refactor(drive): single polling loop on Connect flow`

---

### [ ] T11. Toast terminology unified (P2)

**Goal:** All toast messages use a consistent pattern.

**Files:**
- New: `src/lib/toast-helpers.ts`
- All frontend files that call `toast.error` / `toast.success` /
  `toast.warning` / `toast.info` — find with:
  ```bash
  grep -rn "toast\." src/ | wc -l
  ```

**Steps:**

1. Create `src/lib/toast-helpers.ts`:
   ```typescript
   import { toast } from "sonner";

   /** Standard pattern: "Couldn't <operation>" + reason as description. */
   export const errorToast = {
     /** Operation phrased in present tense: "save preset", "load voices". */
     api(operation: string, error: unknown, opts?: { duration?: number }) {
       const reason = error instanceof Error ? error.message :
         typeof error === "string" ? error : "Unknown error";
       toast.error(`Couldn't ${operation}`, {
         description: reason,
         duration: opts?.duration,
       });
     },
   };

   /** Standard success: "<Thing> saved" / "<Thing> deleted" / etc. */
   export const okToast = {
     saved: (thing: string) => toast.success(`${thing} saved`),
     deleted: (thing: string) => toast.success(`${thing} deleted`),
     copied: (thing: string) => toast.success(`${thing} copied to clipboard`),
   };
   ```

2. Gradually migrate call sites. Start with the highest-traffic pages
   (`/settings`, `/parallel-sheets`, `/parallel`, `/remix`). Don't
   touch every file in one commit — go file by file, smaller commits.

   Example migration:
   ```tsx
   // BEFORE
   toast.error("Save failed", { description: e.message });
   // AFTER
   errorToast.api("save preset", e);
   ```

**Acceptance:** N/A — this is a style migration. Lint check: zero
TypeScript errors. Visually, error toasts all start with "Couldn't ".

**Estimated effort:** 1.5 hours (touch many files).

**Commit message:** `refactor(ui): unify toast terminology across the app`

---

### [ ] T12. VRAM management between stages (P2)

**Goal:** Free GPU memory between the erase stage and the caption-burn
stage so 8GB cards can run larger Whisper models (large-v3) or bump
LaMa batch size.

**Why:** Whisper + LaMa + EasyOCR can all be resident in VRAM
simultaneously. On 8GB Turing this is tight; explicit `empty_cache()`
between major stages reclaims significant headroom.

**Files:**
- `server/workers/remix_pipeline.py`
- `server/workers/parallel_pipeline.py`
- New: `server/services/gpu_utils.py`

**Steps:**

1. Create `server/services/gpu_utils.py`:
   ```python
   """Helpers to manage GPU memory between pipeline stages."""
   import logging
   logger = logging.getLogger("clipforge.gpu")


   def free_gpu_memory(label: str = "") -> None:
       """Aggressively release GPU memory: drop model references,
       call torch.cuda.empty_cache(), gc.collect(). Cheap on CPU."""
       import gc
       gc.collect()
       try:
           import torch
           if torch.cuda.is_available():
               torch.cuda.empty_cache()
               torch.cuda.synchronize()
               allocated = torch.cuda.memory_allocated() / 1024**3
               reserved = torch.cuda.memory_reserved() / 1024**3
               logger.info(
                   f"GPU memory after {label}: allocated={allocated:.2f}GB, "
                   f"reserved={reserved:.2f}GB"
               )
       except ImportError:
           pass


   def unload_inpaint_model() -> None:
       """Move the LaMa model to CPU and drop the GPU side. Next inpaint
       call will reload on demand."""
       try:
           from services import inpaint
           if getattr(inpaint, "_LAMA_MODEL", None) is not None:
               inpaint._LAMA_MODEL = inpaint._LAMA_MODEL.cpu()
               inpaint._LAMA_MODEL = None
               logger.info("LaMa model unloaded from GPU")
       except Exception:
           logger.exception("could not unload LaMa")
   ```

2. In `remix_pipeline.py`, call `free_gpu_memory("erase")` after
   `_stage_erase()` finishes. Same after `_stage_transcribe()`.

3. Optional: call `unload_inpaint_model()` after `_stage_erase()` so
   LaMa fully releases ~2GB. Cost: 5s reload next time. Worth it on
   single-run flows. For parallel runs with multiple variants, the
   LaMa stage runs only once at the start so unload-after-erase is
   correct.

**Acceptance:**
- Backend log shows "GPU memory after erase: allocated=X.XGB" lines.
- VRAM drops by ~2GB between erase end and caption-burn start (verify
  with `nvidia-smi` watching).

**Estimated effort:** 45 min.

**Commit message:** `perf(gpu): free VRAM between pipeline stages`

---

### [ ] T13. SSE for job progress (P2)

**Goal:** Replace 1.5s polling with Server-Sent Events. Less network,
real-time updates.

**Files:**
- `server/routers/jobs.py` — new `/api/jobs/{id}/stream` endpoint
- `src/components/parallel/parallel-processor.tsx` — replace polling effect
- `src/app/remix/page.tsx` — same

**Steps:**

1. Backend SSE endpoint:
   ```python
   from fastapi.responses import StreamingResponse
   import asyncio, json

   @router.get("/{job_id}/stream")
   async def stream_job(job_id: str):
       """SSE stream of job progress. Closes when job hits a terminal state."""
       async def events():
           last_progress = None
           last_status = None
           while True:
               async with async_session() as session:
                   job = await session.get(JobModel, job_id)
               if not job:
                   yield "event: error\ndata: {\"detail\":\"not found\"}\n\n"
                   return
               cur = (job.progress, job.status, job.progress_message)
               if cur != last_progress:
                   last_progress = cur
                   payload = {
                       "id": job.id,
                       "status": job.status,
                       "progress": job.progress,
                       "progress_message": job.progress_message,
                       "error": job.error,
                   }
                   yield f"data: {json.dumps(payload)}\n\n"
               if job.status in ("done", "failed", "cancelled"):
                   return
               await asyncio.sleep(1.0)
       return StreamingResponse(events(), media_type="text/event-stream")
   ```

2. Frontend: replace the `setInterval(tick, 1500)` with EventSource:
   ```tsx
   useEffect(() => {
     if (!jobId) return;
     const es = new EventSource(`/worker-api/jobs/${jobId}/stream`);
     es.onmessage = (ev) => {
       const j = JSON.parse(ev.data);
       setProgress(Math.round((j.progress || 0) * 100));
       setProgressMsg(j.progress_message || "");
       setJobStatus(j.status);
       if (j.status === "done") {
         // fetch full /result + onJobDone
         ...
         es.close();
       } else if (j.status === "failed" || j.status === "cancelled") {
         es.close();
       }
     };
     es.onerror = () => { es.close(); /* fall back to polling? */ };
     return () => es.close();
   }, [jobId]);
   ```

**Acceptance:**
- DevTools Network tab shows ONE `/stream` request that stays open
  vs multiple `/jobs/{id}` polls.
- Job progress UI updates exactly as fast as before.

**Estimated effort:** 1.5 hours.

**Commit message:** `perf(jobs): SSE for job progress instead of polling`

---

### [ ] T14. Split remaining oversized files (P2)

**Goal:** Bring every project file under 500 lines.

**Files (line counts as of 2026-06-07):**
- `src/app/remix/page.tsx` — 1477 (target: <500)
- `server/workers/remix_pipeline.py` — 886
- `server/services/captioner.py` — 877
- `src/app/tts/page.tsx` — 905
- `src/app/captions/page.tsx` — 907
- `src/app/transcript/page.tsx` — 574
- `src/app/utilities/caption-eraser/page.tsx` — 549
- `src/app/silence/page.tsx` — 449 (close to limit; defer)
- `server/services/transcriber.py` — 534
- `server/services/downloader.py` — 518
- `server/services/inpaint.py` — 545

**Strategy for each:**

**`src/app/remix/page.tsx` (1477 → target <500):**
- Already extracted `RemixPastRuns` + `CommentatorPicker`. Remaining:
  - Extract `<RemixVoiceCard>` (engine + voice + custom ID + language + speed) — ~180 lines
  - Extract `<RemixCaptionCard>` (template + style overrides) — ~200 lines
  - Extract `<RemixZonePicker>` (canvas + erase/caption rects) — ~250 lines
  - Extract `<RemixEngineSettings>` (erase method, transcript engine, target lang) — ~120 lines
  - Extract `<RemixResultsPanel>` (progress + download links) — ~150 lines
- Pass shared state via props; the page becomes a thin shell.

**`server/workers/remix_pipeline.py` (886 → target <500):**
- Move each `_stage_*` function into `server/workers/stages/`:
  - `stages/download.py`
  - `stages/transcribe.py`
  - `stages/erase.py`
  - `stages/match_and_caption.py`
  - `stages/commentator.py`
  - `stages/descriptions.py`
- `remix_pipeline.py` keeps only the orchestrator (`handle_remix_pipeline`),
  the `_Sliced` progress helper, and the shared ffmpeg helpers
  (`_ffmpeg_bin`, `_creationflags`, `_probe_audio_dur`,
  `_has_audio_stream`).
- `parallel_pipeline.py` imports from `stages/` directly.

**`server/services/captioner.py` (877 → target <500):**
- Split into:
  - `captioner/colors.py` — ASS color hex/RGB conversion helpers
  - `captioner/presets.py` — `DEFAULT_PRESETS` and helpers
  - `captioner/generator.py` — `generate_captions` main function
  - `captioner/__init__.py` re-exports

**`src/app/tts/page.tsx` / `src/app/captions/page.tsx`:**
- Extract engine-config / voice-library / settings panels into
  `src/components/tts/...` and `src/components/captions/...` respectively.

**Acceptance:**
- For each file: starting line count → final line count documented
  in the commit message.
- Zero TypeScript errors after each extract.
- Zero Python import errors after each backend extract.

**Estimated effort:** ~1.5 hours PER file. Do as separate commits.
**~12 hours total.** Don't try to do all in one go.

**Commit messages (one per file):**
- `refactor(remix): extract VoiceCard + CaptionCard + ZonePicker from page.tsx`
- `refactor(workers): split remix_pipeline into per-stage modules`
- `refactor(captioner): split into colors/presets/generator modules`
- etc.

---

### [ ] T15. Replace /remix Drive UI with shared <DriveCard> (P2)

**Goal:** `/remix` page still has its own Drive UI inline; replace with
the shared `<DriveCard>` from `src/components/parallel/drive-card.tsx`.

**Files:**
- `src/app/remix/page.tsx`
- (Possibly move `drive-card.tsx` to `src/components/shared/` since
  it's used by parallel + remix.)

**Steps:**

1. Search `/remix/page.tsx` for Drive-related JSX. Should find a card
   with Connect / Disconnect / status email similar to the shared one.

2. Move `src/components/parallel/drive-card.tsx` →
   `src/components/shared/drive-card.tsx`. Update all imports
   (`parallel-processor.tsx`, `settings/page.tsx`).

3. Replace the inline JSX in `/remix/page.tsx` with `<DriveCard />`.

**Acceptance:**
- `/remix` Drive section visually identical to `/parallel`.
- TypeScript clean.

**Estimated effort:** 30 min.

**Commit message:** `refactor(drive): single DriveCard used by /remix + /parallel + /settings`

---

### [ ] T16. Encrypt API keys at rest (P2)

**Goal:** API keys in `data/*_config.json` are no longer plaintext.

**Files:**
- New: `server/services/secret_storage.py`
- `server/services/elevenlabs.py`
- `server/services/transcript_cleaner.py`

**Steps:**

1. `services/secret_storage.py`:
   ```python
   """Light obfuscation for API keys at rest. Not real cryptography —
   prevents casual disclosure if the user accidentally syncs data/
   somewhere visible. The key is derived from a machine-specific
   identifier so the encrypted blobs don't survive a full move."""
   import base64
   import hashlib
   import os
   import platform
   from typing import Optional

   def _machine_secret() -> bytes:
       # Stable per-machine: hostname + a fixed app salt.
       parts = (platform.node() or "anon") + "::clipforge::v1"
       return hashlib.sha256(parts.encode()).digest()

   def encrypt(s: str) -> str:
       if not s:
           return s
       data = s.encode()
       key = _machine_secret()
       out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
       return "enc:" + base64.urlsafe_b64encode(out).decode()

   def decrypt(s: str) -> Optional[str]:
       if not s or not s.startswith("enc:"):
           return s  # plaintext / unset — return as-is for backward compat
       try:
           data = base64.urlsafe_b64decode(s[4:].encode())
           key = _machine_secret()
           return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode()
       except Exception:
           return None
   ```

2. Update the existing `get_api_key` / `set_api_key` functions in
   `elevenlabs.py` and `transcript_cleaner.py` to encrypt on save,
   decrypt on read.

3. Backward compat: existing plaintext keys keep working (decrypt
   returns them as-is). On next set, they're encrypted.

4. On startup, do a one-time migration: read each config file, if any
   key is plaintext, re-save it encrypted. Log how many were upgraded.

**Acceptance:**
- After running once, `cat data/tts_config.json` shows `"elevenlabs_api_key": "enc:..."`.
- API calls still work.
- This is light obfuscation — note in code comments it's NOT real cryptography.

**Estimated effort:** 1 hour.

**Commit message:** `feat(security): obfuscate API keys at rest with machine-tied XOR`

---

### [ ] T17. Smoke tests (P2)

**Goal:** Minimum coverage so big regressions are caught before push.

**Files:**
- New: `server/tests/` directory
- New: `server/tests/conftest.py`
- New: `server/tests/test_api_smoke.py`
- New: `server/requirements-dev.txt` (pytest + httpx + pytest-asyncio)

**Steps:**

1. `server/requirements-dev.txt`:
   ```
   pytest>=8
   pytest-asyncio>=0.23
   httpx>=0.25
   ```

2. `server/tests/conftest.py`:
   ```python
   import pytest
   from httpx import AsyncClient, ASGITransport
   from main import app

   @pytest.fixture
   async def client():
       transport = ASGITransport(app=app)
       async with AsyncClient(transport=transport, base_url="http://test") as c:
           yield c
   ```

3. `server/tests/test_api_smoke.py`:
   ```python
   import pytest

   @pytest.mark.asyncio
   async def test_health(client):
       r = await client.get("/api/health")
       assert r.status_code == 200
       assert r.json()["status"] == "ok"

   @pytest.mark.asyncio
   async def test_system(client):
       r = await client.get("/api/system")
       assert r.status_code == 200
       j = r.json()
       assert "gpu_available" in j
       assert "whisper_model" in j

   @pytest.mark.asyncio
   async def test_tts_engines(client):
       r = await client.get("/api/tts/engines")
       assert r.status_code == 200
       assert len(r.json()["engines"]) >= 3

   @pytest.mark.asyncio
   async def test_drive_status_no_setup(client):
       r = await client.get("/api/drive-auth/status")
       assert r.status_code == 200
       # Should not error even with no OAuth client configured

   @pytest.mark.asyncio
   async def test_sheets_config_unconfigured(client):
       r = await client.get("/api/sheets/config")
       assert r.status_code == 200
       # Returns {"configured": false} when no config saved

   @pytest.mark.asyncio
   async def test_parallel_recent(client):
       r = await client.get("/api/parallel/recent")
       assert r.status_code == 200
       assert "runs" in r.json()
   ```

4. README addition (`server/tests/README.md`):
   ```
   ## Running tests
   cd server
   .venv/bin/pip install -r requirements-dev.txt
   .venv/bin/pytest tests/ -v
   ```

5. Add a top-level script `scripts/run-tests.sh`:
   ```bash
   #!/usr/bin/env bash
   set -e
   cd "$(dirname "$0")/.."
   server/.venv/bin/pytest server/tests/ -v
   ```

**Acceptance:**
- `pytest server/tests/ -v` runs and all tests pass.
- 7+ tests covering the main endpoints listed above.

**Estimated effort:** 1.5 hours.

**Commit message:** `test: smoke tests for the public API surface`

---

### [ ] T18. Pre-commit hooks (P2)

**Goal:** TypeScript errors can't slip into commits.

**Files:**
- `package.json` — add husky + lint-staged dev deps
- New: `.husky/pre-commit`
- New: `lint-staged.config.js`

**Steps:**

1. `npm install -D husky lint-staged`

2. `package.json` add:
   ```json
   "scripts": {
     ...
     "prepare": "husky install",
     "typecheck": "tsc --noEmit"
   },
   "lint-staged": {
     "*.{ts,tsx}": ["eslint --max-warnings=0"]
   }
   ```

3. `npx husky install && npx husky add .husky/pre-commit "npm run typecheck && npx lint-staged"`

4. Test by making a TypeScript error in a file and trying to commit;
   the hook should reject it.

**Acceptance:**
- Hook fires on `git commit`.
- TS error blocks the commit.

**Estimated effort:** 30 min.

**Commit message:** `chore(dev): pre-commit hook runs tsc + eslint on changed files`

---

### [ ] T19. API documentation (P2)

**Goal:** External integrators can use `/api/auto`, `/api/sheets/*`,
`/api/drive-auth/*` without reading code.

**Files:**
- New: `docs/api.md`
- Update: `README.md` (link to it)

**Steps:**

1. `docs/api.md` with sections:
   - Overview (base URL `http://localhost:8420`, all endpoints prefixed
     with `/api/`)
   - Auth (none required — local-only by default)
   - `/api/auto` — full request/response schema + 3 curl examples
   - `/api/sheets/*` — config + pull-next + commit + skip-row
   - `/api/drive-auth/*` — status + connect + disconnect + client + DELETE
   - `/api/transcript/device` — diagnostic
   - `/api/variant-presets` — CRUD
   - `/api/parallel/start` — full schema
   - `/api/jobs/{id}` — polling vs SSE (after T13)
   - Errors: 400 / 401 / 404 / 409 / 413 / 422 / 502 / 503 meanings

2. Each section: copy-paste-runnable curl example.

3. Link from `README.md`:
   ```markdown
   ## API
   See [docs/api.md](docs/api.md) for the full API reference.
   FastAPI's auto-generated docs also work: visit
   `http://localhost:8420/docs` when the backend is running.
   ```

**Acceptance:**
- A person who has never seen ClipForge can curl `/api/auto` after
  reading the doc.

**Estimated effort:** 1.5 hours.

**Commit message:** `docs: API reference for /auto, /sheets, /drive-auth, /transcript/device`

---

## 6. Disk cleanup (separate concern, P1)

The user has 45GB in `data/media/`. With 1.33TB free their disk isn't
under pressure RIGHT NOW, but this grows ~540MB per project. Bundle this
under T9 (delete past runs) which already nukes media dirs on delete —
once T9 ships the user has a manual cleanup path.

Optionally add `/api/admin/cleanup-old-projects?older_than_days=N` for a
bulk sweep — but defer until the user actually asks.

---

## 7. Things explicitly OUT OF SCOPE

Do NOT do any of these unless the user asks:

- Migrate to Postgres
- Add user accounts / auth
- Multi-tenant support
- Docker / containerization
- TikTok / Instagram posting integration (mentioned in handover but
  needs business verification + app audit; not a 1-session task)
- Switch from Next.js to anything else
- Rewrite the captioner / inpaint / TTS

---

## 8. Verification checklist before each commit

Run ALL of these before committing:

```bash
# TypeScript
node node_modules/typescript/bin/tsc --noEmit

# Python (the files you touched)
cd server && python -m py_compile path/to/changed.py

# Bash (if you touched dev.sh)
bash -n dev.sh

# Smoke tests (after T17 is done)
server/.venv/bin/pytest server/tests/ -v
```

If any of these fail, fix before commit.

---

## 9. Workflow summary for the executor

For each `[ ]` task above, in order of priority:

1. Read the task fully. Note "Files to touch" and "Depends on" if any.
2. Read those files (Grep first, Read only what you need).
3. Make the changes per "Steps".
4. Run the verification checks in §8.
5. Run the task-specific "Acceptance" check.
6. `git add` the specific files (never `git add -A` without inspecting
   first).
7. Commit with the suggested message.
8. Move on. Push every 3-5 commits or end-of-session.
9. Mark the task `[x]` in this file.

**Do not spawn sub-agents.** All work is direct edits + grep + run.

**Do not invent new features** beyond what's specified.

**Do not refactor things outside the listed files** unless required to
make the task work; if you find unrelated cleanup, add a new `[ ]` task
to this file at the bottom for the user to prioritize.

---

## 10. Status board (update this in your final commit)

| Task | Status | Commit |
|------|--------|--------|
| T1   |   `[x]`     | 817e0f5 |
| T2   |   `[x]`     | c2f567f |
| T3   |   `[x]`     | 77697cf |
| T4   |   `[x]`     | 26cd37e |
| T5   |   `[ ]`     |        |
| T6   |   `[ ]`     |        |
| T7   |   `[ ]`     |        |
| T8   |   `[ ]`     |        |
| T9   |   `[ ]`     |        |
| T10  |   `[ ]`     |        |
| T11  |   `[ ]`     |        |
| T12  |   `[ ]`     |        |
| T13  |   `[ ]`     |        |
| T14  |   `[ ]`     |        |
| T15  |   `[ ]`     |        |
| T16  |   `[ ]`     |        |
| T17  |   `[ ]`     |        |
| T18  |   `[ ]`     |        |
| T19  |   `[ ]`     |        |

When you complete a task, change `[ ]` to `[x]` and add the commit
short SHA.

---

End of plan.
