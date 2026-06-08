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

### [x] T1. Subprocess timeouts (P0)

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

### [x] T2. Retry external API calls (P0)

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

### [x] T3. Cancel cleans up intermediate files (P0)

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

### [x] T4. Cancel propagates through erase + caption-burn (P0)

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

### [x] T5. Better stuck-job recovery (P1)

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

### [~] T6. (DEFERRED — needs runtime test) Untracked background tasks → use job queue (P1)

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

### [x] T7. Graceful SIGTERM handling (P1)

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

### [x] T8. Running-jobs indicator in sidebar (P1)

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

### [x] T9. Past-runs panel: pagination + delete (P1)

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

### [x] T10. Drive Connect polling refactor (P2)

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

### [x] T11. Toast terminology unified (P2)

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

### [x] T12. VRAM management between stages (P2)

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

### [x] T13. SSE for job progress (P2)

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

### [x] T14. Split remaining oversized files (P2)

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

### [~] T15. (MOOT — /remix has no Drive UI) Replace /remix Drive UI with shared <DriveCard> (P2)

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

### [x] T16. Encrypt API keys at rest (P2)

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

### [x] T17. Smoke tests (P2)

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

### [x] T18. Pre-commit hooks (P2)

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

### [x] T19. API documentation (P2)

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
| T5   |   `[x]`     | 77ffcd1 |
| T6   | DEFERRED | needs runtime test — rewires working TTS/Transcript studios |
| T7   |   `[x]`     | 77ffcd1 |
| T8   |   `[x]`     | 5f20900 |
| T9   |   `[x]`     | 084a714 |
| T10  |   `[x]`     | a0b82fa |
| T11  |   `[x]`     | 552aa05 (helper + parallel-sheets; rest incremental) |
| T12  |   `[x]`     | a0b82fa |
| T13  |   `[x]`     | 2e06638 (parallel-processor; /remix still polls) |
| T14  |   `[x]`     | 7d46034 (captioner split; remix_pipeline + FE pages deferred) |
| T15  | MOOT | /remix has no Drive UI; nothing to unify |
| T16  |   `[x]`     | f2ab862 |
| T17  |   `[x]`     | be6e522 |
| T18  |   `[x]`     | 9e5837b |
| T19  |   `[x]`     | cf5d0db |

When you complete a task, change `[ ]` to `[x]` and add the commit
short SHA.

---

## 11. DEEP DIVE — Caption auto-detect: eliminate post-erase leak (T20)

> **Status:** `[x]` IMPLEMENTED + REAL-TESTED (commits 75a10fa, f99574a,
> 8492627, 63ef50a, 60e7844, b4302c0, 6660682). Steps A–F all done. Tested
> end-to-end on a 20s TikTok clip via the Linux venv (auto-localize → tight
> detect → LaMa inpaint → re-OCR the output): **caption leaks 45 → 0**, mask
> coverage ~24% of the band (vs 100% for a rectangle), scene text (a shirt
> logo) correctly preserved, band auto-located with NO manual box. Two bugs
> were caught by the harness and fixed: (1) index-seek grabbed the wrong
> frame for the mask → sequential decode; (2) Otsu masked only the bright
> fill and left the dark outline as a ghost → local-contrast mask covering
> fill+outline. Section kept below as the design record.

> This is a dedicated design + task for the eraser, written after the user
> reported: "after erasing, a few frames STILL show the caption." Read this
> whole section before touching the eraser.

### 11.1 What happens today (read the code first)

- `server/services/caption_detector.py::detect_caption_segments`
  1. OCR (EasyOCR) samples frames at ~5fps → finds text bboxes.
  2. Clusters bboxes into vertical "lanes", takes a percentile-union bbox
     per lane → caption **zones** (rectangles).
  3. `_presence_segments`: scans ~20fps, measures **edge density** (Canny
     `.mean()`) inside each zone; thresholds it (adaptive `lo + 0.30*(hi-lo)`)
     → a boolean `present[]` per sampled frame; bridges short gaps; emits
     time segments `{start_t, end_t, x, y, w, h}` (+ `bleed_s=0.4`).
- `server/services/inpaint.py`: builds one rectangular mask per segment.
  `_find_active_segment(t)` returns the segment whose `[start_t, end_t)`
  contains `t`. **If no segment contains `t`, the frame passes through
  UNTOUCHED** — this is the leak.

### 11.2 Why captions leak (root causes — all of these are real)

1. **Temporal boundary quantization.** Segment start/end come from the
   20fps presence samples. A caption that appears between two samples isn't
   covered until the next sample (+`bleed_s`). If bleed is too small, the
   first/last frames leak.
2. **Fade in/out.** During a fade the caption is semi-transparent → fewer
   edges → edge-density drops below threshold → marked "absent" → not
   erased, but the ghost is still visible. **This is the #1 cause of the
   user's complaint.**
3. **Sparse-text frames.** A single short word dilutes the edge-density
   mean over the whole (wide) zone → below threshold → missed.
4. **Horizontal under-coverage.** The zone bbox is a percentile UNION of
   sampled widths. A frame whose caption is wider than the union (long
   sentence) leaks at the left/right edges.
5. **Threshold fragility.** `lo + 0.30*(hi-lo)` is balanced. On clips where
   the background band is itself busy, the floor rises and real captions
   fall under threshold.
6. **OCR localization gaps.** If OCR never sampled the caption in a slightly
   shifted position (jitter), the union zone may not cover it.

### 11.2b UPDATED GOALS (the user refined the requirement — read carefully)

The user added two requirements that CHANGE the design away from the
"over-erase the whole band" idea below. The new target is:

1. **Minimal mask.** Erase as LITTLE as possible — ideally only the text
   glyphs + their outline/shadow, NOT a rectangle. Less background touched
   = clearer output. So a tight, per-pixel mask, not a band rectangle.
2. **No manual box.** Fully automatic. The user will NOT draw the erase
   rect (they want to script it). So the detector must (a) localize the
   caption on its own and (b) reject scene text (signs/labels/UI in the
   source) WITHOUT the box that used to do that job.

These two pull AGAINST simple "over-erase for zero leak". The resolution is
in §11.3b. (Keep the old §11.3/§11.4 below as the *fallback* design — a
guaranteed-safe band mode — but the PRIMARY goal is now tight + auto.)

### 11.3 (OLD) The cost-asymmetry principle — now the FALLBACK only

**The cost is asymmetric.** Inpainting a band frame with NO caption is
visually free (LaMa reconstructs background over background). Missing a
caption frame is a visible defect. So a *band-rectangle* mode can bias
toward over-erase for guaranteed zero leak. This is still the right idea
for the **safety fallback** (§11.4 Tier 0), but it erases too much for the
user's new clarity goal — see §11.3b.

### 11.3b THE NEW GUIDING DESIGN (tight + automatic, resolves the tension)

Two axes are in tension: **completeness** (no leak) vs **minimality**
(clarity). The insight that gives BOTH:

> **A caption is HELD IDENTICAL for its whole on-screen duration.**
> So compute ONE tight per-glyph mask per "caption display", from its
> clearest (fully-opaque) frame, and reuse that exact mask for EVERY frame
> of that display.

Why this wins on both axes:
- **Tight** — the mask follows the actual glyph shapes (per-pixel), not a
  rectangle → minimal background touched.
- **Complete** — every frame of the display gets the same, correct mask, so
  there's no per-frame jitter and no boundary leak. Fade-in/out frames get
  the full opaque mask, which slightly over-covers the faint glyphs — which
  is exactly right (covers the ghost) and still tight.
- **Fits the existing inpaint architecture** — a segment already carries a
  `mask_roi`. Today it's a filled rectangle; make it the tight glyph mask
  instead. The inpaint loop already applies one mask to all frames of a
  segment. So redefine a "segment" = **one caption display** (split at every
  TEXT CHANGE, not just at gaps). Inpaint barely changes; all the work is in
  the detector.

**Three sub-problems and how to solve each:**

**(A) Tight per-glyph mask (minimality).**
OCR already gives a tight quadrilateral per text line. Inside that quad:
crop → grayscale → **adaptive/Otsu threshold** to separate glyph pixels from
their immediate background → binary glyph mask → **dilate 2–3px** to cover
the outline/shadow (captions almost always have one). Union the line masks
of a display → that display's tight mask. Cheap (one threshold per display,
~20–40 displays per clip, not per frame).

**(B) Auto-localization + scene-text rejection (no box).**
The manual box used to mean "the caption is HERE, ignore text elsewhere."
Replace it with a VOTE over signals that distinguish a burned-in caption
from scene text:
  - **Held-still ≥ ~0.5s (STRONGEST).** A caption's text pixels stay fixed
    while the video moves; scene text moves with the camera/objects. Reject
    any text region that drifts more than a few px over its lifetime.
  - **Recurs in a consistent band.** Cluster all held-still detections by
    Y-center over the WHOLE clip; the band(s) with the most cumulative
    text-time = caption lane(s). Scene text is scattered → filtered out.
  - **Transcript-timing correlation (we already have it!).** The pipeline
    already produced the speech transcript with timestamps. A real caption
    appears in sync with speech; a static lower-third graphic does not.
    Correlate text-present intervals with speech intervals → strong caption
    confidence. This is a signal a generic eraser CAN'T use but ClipForge
    can, because transcription runs first.
  - **Style consistency (weak tiebreak).** Caption font height/colour is
    constant across the clip; scene text varies.
  Combine into a confidence; keep regions above threshold as caption. No box.

**(C) Catching faint / fade / missed frames (completeness, from §11.2).**
Because the mask is per-DISPLAY (reused across the hold), the only risk is
getting the DISPLAY's time bounds right. Use the dual presence signal from
the old plan (edge density OR temporal-stability×gradient) just to mark the
display's start/end frames, then apply the tight mask across that whole
range + a small temporal dilation. Fade frames are inside the range → they
get the full mask → no ghost.

**Honest hard cases (tell the user these up-front):**
- **Scene text in the SAME band that's also held still** (e.g., a static
  burned-in logo / lower-third in the caption lane). Held-still + band both
  fire → it'd be erased too. The transcript-correlation signal helps (a logo
  isn't in sync with speech) but it's not bulletproof. This is the one case
  where dropping the manual box genuinely loses information.
- **Very low-contrast captions with no outline** — adaptive threshold may
  under-segment. Fall back to dilating the mask or to band-rectangle mode.
- **Two captions overlapping a text change (cross-fade)** — OR the two
  adjacent displays' masks across the transition window.
For these, keep the §11.4 band-rectangle mode as a one-click fallback.

### 11.3d Two caption STYLES need two mask shapes (glyph vs box)

The tight-glyph mask is correct for the common style (text + thin
outline/shadow). But some captions sit on a **solid background box** (a
filled or semi-transparent rectangle behind the whole line, for legibility).
For those, a glyph mask is WRONG — you'd erase the letters but leave the
box's rectangle outline showing. So the detector must pick the mask shape
per display:

- **Multi-word handling (good news):** the per-display model already handles
  a full phrase/sentence shown at once — that's just one display whose OCR
  text doesn't change for its hold. Otsu over the line quad(s) produces a
  glyph mask covering ALL the words (and both lines if 2-line), with the
  spaces/line-gaps/margins left untouched → MORE background saved than for
  single words. A karaoke highlight that recolours the current word does NOT
  change the string, so it stays one display / one mask (don't split on it).

- **Detect "is there a solid box":** inside the line quad's bounding region
  (plus a few px margin), test whether the NON-glyph pixels are a single
  near-uniform colour with low variance (a box) vs varied (real video
  background showing through). Concretely: take the quad region, remove the
  glyph pixels (the Otsu foreground), and look at the remaining background
  pixels — if their colour std-dev is low (< ~25 per channel) AND they form
  a connected rectangular-ish region, it's a BOX.
    - **Box style → mask = the box rectangle** (the connected uniform region's
      bbox), dilated a couple px. Erase the whole box.
    - **Glyph style → mask = the dilated Otsu glyph mask** (§11.3b-A).
  Store `segments[i]["mask_kind"] = "glyph" | "box"` for logging/debug.

This branch is ~20 lines and removes the "box outline ghost" failure mode.

### 11.3c Architecture change in inpaint (small but required)

Today `_build_segment_state` fills a RECTANGLE into `mask_roi`. To erase
tightly, let a segment optionally carry a `mask` (a binary ndarray in
input-pixel coords, or a list of polygons). When present, `_build_segment_state`
rasterizes THAT into `mask_roi` (cropped to the segment's tight bbox + the
inpaint context margin) instead of filling the rectangle. Everything
downstream (LaMa tensor, batching, the per-frame apply loop) is unchanged —
it already applies an arbitrary `mask_roi`. So this is a ~30-line change
plus passing the mask through `detect_caption_segments → segments[i]["mask"]`.

### 11.4 (FALLBACK) Band-rectangle modes — three tiers

Expose an **"Erase coverage"** choice in the UI (Remix + Parallel shared
settings, next to "Auto-detect captions"): `Thorough` (Tier 0) | `Smart`
(Tier 1, default). Backend reads it from the erase config.

#### Tier 0 — "Thorough": full-band, full-duration (GUARANTEED zero leak)

The simplest bulletproof answer. When the user picks Thorough (or when
`auto_detect` is on but the clip is short), **inpaint the entire ROI band
for the entire clip** — no presence detection, no OCR. One static segment =
the user's rect, `start_t=0, end_t=∞`. Zero leak is mathematically
guaranteed because every frame's band is inpainted.

- Cost: more frames through LaMa (the whole clip, not just caption frames).
  On the user's RTX 2080 Super that's ~18fps inpaint, so a 60s/1800-frame
  clip ≈ 100s. Acceptable given ~10min total runs.
- Implementation: trivial — in `_stage_erase`, when coverage=="thorough",
  skip `detect_caption_segments` and call `inpaint_region(x,y,w,h=ROI)`
  with no `segments`. **Do this first; it's ~20 lines and gives the user a
  guaranteed-correct option immediately.**

#### Tier 1 — "Smart": robust presence + full-width mask + aggressive dilation

Keep OCR for **localization only** (finding the vertical sub-band), but make
PRESENCE and the MASK leak-proof. Four changes to `caption_detector.py`:

**(a) Erase the FULL ROI WIDTH (kills horizontal leak, cause #4).**
Stop fitting the bbox to text width. The user drew the band; captions live
across it. Set each segment's `x = roi.x`, `w = roi.w`. Keep the vertical
extent tight to the detected lane (so a tall ROI doesn't erase more height
than needed) — or full ROI height in Thorough. Horizontal leak becomes
impossible.

**(b) Dual/triple presence signal, OR-ed (kills fade + sparse leak, #2/#3).**
In `_presence_segments`, compute per sampled frame, inside the (now
full-width) band:
  - **Signal A — edge density** (current Canny mean). Good for crisp text.
  - **Signal B — temporal-stability × gradient (NEW, the key addition).**
    Keep a ring buffer of the last `K≈5` decoded band crops. A caption pixel
    is one that has **high spatial gradient** (text edge) AND **low temporal
    difference** vs the buffered neighbors (the caption holds still while the
    background video moves). Count such pixels; a caption frame has a large
    connected cluster, a no-caption frame (moving video) has few. **This
    fires even on faded/semi-transparent captions**, because a fading caption
    still holds its SHAPE over several frames while the background moves —
    exactly the case edge-density misses.
  - **Signal C — OCR anchors** (current): frames OCR confidently saw text.
  Mark `present = A_over_thr OR B_over_thr OR C_anchor`. ORing means any one
  signal catching it is enough — no single failure mode leaks.

**(c) Aggressive temporal morphology (kills boundary leak, #1).**
After `present[]`:
  - **Dilate** every True run by a generous margin (≈0.4s each side,
    configurable `expand_s`, default up from today's 0.4 bleed to a real
    morphological grow). Over-erasing idle band frames is free (§11.3).
  - **Bridge** gaps up to ≈0.6s (up from 0.3) so word-to-word and brief
    fades stay one segment.
  - **Lower the threshold** for Signal A to `lo + 0.15*(hi-lo)` (from 0.30)
    and add a hard rule: a frame adjacent (±expand) to any confirmed-present
    frame is present. Bias-to-present.

**(d) Spatial mask dilation in inpaint (kills 1-px edge leak).**
Bump `dilate_px` for auto-detect segments from 6 → ~10, so the inpaint mask
slightly overshoots the text outline (captions have outlines/shadows that
extend a few px beyond the glyph).

#### Tier 2 — OPTIONAL, no-OCR presence (if EasyOCR ever becomes a problem)

The user asked "if OCR can't be perfect, find another way." Tier 1's Signal
B is already OCR-independent for PRESENCE. If we also drop OCR for
LOCALIZATION: the user already draws the band, so we don't need OCR to find
WHERE. Run Signal B (temporal-stability × gradient) across the **full ROI**,
and the vertical sub-band = the rows where stable-gradient pixels cluster.
Result: a fully signal-based detector, no EasyOCR dependency, no model
warm-up. Keep this as a fallback/experiment; Tier 1 (OCR for localization +
Signal B for presence) is the recommended default because OCR localization
is reliable and cheap.

### 11.5 PRIMARY implementation path (tight glyph masks + auto-localize)

This is the path that satisfies the user's refined goals (§11.2b). Build it
in this order; each step is independently verifiable.

**Step 1 — Auto-localize the caption lane (no box).**
New `auto_locate_caption_band(video_path, transcript_segments) -> roi|None`
in `caption_detector.py`:
  - OCR-detect text on sampled frames (reuse the existing sampling loop).
  - For each detection, measure its **drift**: track the same text region
    across the next few samples; if its centre moves > ~1% of frame size, it
    is scene text → drop it. Keep only **held-still** detections.
  - Cluster the held-still detections by Y-centre over the whole clip
    (existing lane logic). Score each lane by cumulative text-time.
  - If `transcript_segments` are available, multiply each lane's score by how
    well its text-present intervals overlap the speech intervals (caption
    tracks speech). Pick the top lane(s).
  - Return that lane's bbox as the auto-ROI (or None → fall back to a default
    bottom band, or to Thorough mode). This REPLACES the user's manual box.

**Step 2 — Per-display segmentation (split at text changes).**
In the detection pass, group consecutive samples into **displays**: a new
display starts when the OCR text content changes materially (string
similarity drops) OR after a gap. Each display = {start_t, end_t, the text,
the line quads}. This replaces today's lane-time-range segments.

**Step 3 — Tight per-glyph mask per display (minimality, §11.3b-A).**
For each display, from its clearest sample (highest OCR confidence / mid-
display frame): for each line quad, crop → grayscale → Otsu/adaptive
threshold → glyph binary → dilate 2–3px → union → the display's tight
`mask` (full-frame-coord binary ndarray, or a list of polygons). Store it
on the segment: `segments[i]["mask"] = ...` alongside its tight bbox.

**Step 4 — inpaint accepts per-segment arbitrary masks (§11.3c).**
`_build_segment_state`: if `seg.get("mask")` is present, rasterize it into
`mask_roi` (cropped to the tight bbox + context margin) instead of filling
the rectangle. ~30 lines; nothing else in inpaint changes.

**Step 5 — completeness on the time bounds (§11.3b-C).**
Run the dual presence signal (edge-density OR temporal-stability) ONLY to
nudge each display's start/end outward to the real first/last frame the
glyphs are visible (including fades), then apply that display's tight mask
across the whole range + ~0.3s temporal dilation. The mask is reused, so
fade frames are covered with no per-frame work.

### 11.5b Files to touch (primary path)

- `server/services/caption_detector.py`
  - NEW `auto_locate_caption_band()` (Step 1).
  - Per-display grouping + tight-mask extraction (Steps 2–3); emit
    `segments[i]` with `mask`, tight `x/y/w/h`, `start_t/end_t`.
  - Keep `_presence_segments` for Step-5 boundary nudging.
- `server/services/inpaint.py`
  - `_build_segment_state`: rasterize `seg["mask"]` when present (Step 4).
- `server/workers/remix_pipeline.py` + `parallel_pipeline.py`
  - `_stage_erase`: when `auto_detect` and NO roi given → call
    `auto_locate_caption_band` first; if it returns None → Thorough fallback.
    Pass tight masks through.
- `server/routers/{remix,parallel,auto}.py`
  - Make `erase_zone` OPTIONAL when `erase_auto_detect=true` (auto-localize).
    Add `erase_coverage: "tight" (default) | "band" | "thorough"`.
- Frontend `parallel-processor.tsx` (+ /remix): when auto-detect is on, the
  zone picker becomes optional; add the coverage select.
- **`/api/auto` (the user's automation): with `erase_auto_detect=true` and no
  zones, it now works fully hands-off — this is the whole point.**

### 11.6 Params (tight path)

```
DRIFT_MAX_FRAC      = 0.01   # held-still threshold (Step 1 scene-text reject)
DISPLAY_SIM_MIN     = 0.6    # text-similarity below this = new display (Step 2)
GLYPH_DILATE_PX     = 3      # cover outline/shadow on the tight mask (Step 3)
BOUND_EXPAND_S      = 0.30   # nudge display bounds outward for fades (Step 5)
SPEECH_OVERLAP_MIN  = 0.3    # lane must overlap speech ≥ this to score as caption
```

### 11.7 How to verify (don't ship without this)

Throwaway harness (not committed). Two things to measure — leak AND tightness:
1. Clips: (a) RO TikTok story (fades), (b) long sentences, (c) moving
   caption, (d) a clip WITH scene text outside the caption band (to prove
   auto-localize doesn't erase it).
2. Run the eraser fully auto (no box).
3. **Leak:** re-OCR the OUTPUT inside the detected band at full fps → ZERO
   text with conf>0.3 = pass. Dump 10 frames/clip for eyeballing.
4. **Tightness:** report mean erased-pixels-per-frame. The tight path should
   be MUCH lower than band-rectangle (that's the clarity win). Dump a frame
   with the mask overlaid to confirm it hugs the glyphs.
5. **Scene-text safety (clip d):** confirm the scene text OUTSIDE the band is
   untouched in the output.
Acceptance: zero leak on a–c, scene text intact on d, erased-pixel count
well below band mode.

### 11.8 Effort + order

1. **Tier 0 Thorough fallback** (§11.4) — ~30 min. Ship first as the safety
   net + a baseline for the harness.
2. **Verification harness** — ~1h. Measures leak AND tightness AND scene-text
   safety. Build before tuning so you optimize against numbers, not vibes.
3. **Step 4 — inpaint per-segment mask** — ~45 min. Unblocks everything tight.
4. **Steps 2–3 — per-display + tight glyph masks** — ~2h. The minimality win.
5. **Step 1 — auto-localize (no box)** — ~2.5h. The automation win; hardest
   part is the held-still scene-text rejection + transcript correlation.
6. **Step 5 — boundary nudging for fades** — ~1h. Closes residual leak.
7. UI: optional zone picker + coverage select — ~45 min.

Total ~8h. If time-boxed: Steps 4+2+3 (~3.5h) give tight masks with the box
still drawn; Step 1 (~2.5h) removes the box. Both are needed for the user's
full goal (tight + no box).

### 11.9 Honest expectation (set this with the user)

"Perfect" tight + auto is achievable on **clean, conventional captions**
(consistent band, outlined text, in sync with speech — i.e. exactly the RO
TikTok story style the user works with). It will NOT be 100% on the hard
cases in §11.3b (static scene-text inside the caption band; ultra-low-
contrast captions with no outline). For those, the one-click **Thorough**
fallback guarantees no leak at the cost of erasing the whole band. So the
product answer is: **tight + auto by default; Thorough button when a clip
fights it.** Don't promise 100% auto on adversarial clips — promise it on the
clips the user actually makes, plus a guaranteed fallback.

### 11.10 Commit messages

- `feat(eraser): Thorough fallback mode — full-band inpaint, zero leak`
- `feat(inpaint): per-segment arbitrary masks (not just rectangles)`
- `feat(eraser): per-display tight glyph/box masks — minimal erase, clearer output`
- `feat(eraser): auto-localize caption band — no manual box (held-still + transcript)`
- `fix(eraser): nudge display time-bounds for fade frames`

### 11.11 COOKBOOK — do this exactly (written for a weaker model)

Follow these steps IN ORDER. After each step: run the listed check, and do
NOT continue until it passes. Commit after each step. Everything is in
`server/`. Read `services/caption_detector.py` and `services/inpaint.py`
fully before starting (§11.1 explains them).

Helper terms: a **display** = one held-still piece of caption text (same
string shown for ~1–3s). A **glyph mask** = a binary image where text pixels
are 255. ROI = a rect `{x,y,w,h}` in source-video pixels.

---

**STEP A — Thorough fallback (do first, ~30 min).**

Goal: a guaranteed-no-leak mode that erases the whole band for the whole
clip. This is your safety net + test baseline.

1. In `routers/parallel.py`, `routers/remix.py`, `routers/auto.py`: add a
   field to the start-request model:
   ```python
   erase_coverage: str = "tight"   # "tight" | "band" | "thorough"
   ```
   Put it into the job metadata dict next to `erase_auto_detect`.
2. In `workers/remix_pipeline.py` `_stage_erase` (and the same call in
   `parallel_pipeline.py`), near the top:
   ```python
   coverage = cfg.get("erase_coverage", "tight")
   if coverage == "thorough":
       # erase the whole drawn/auto band for the whole clip — one static rect
       await inpaint_region(str(video_path), str(output_path),
                            x=x, y=y, w=w, h=h, algorithm=algorithm,
                            on_progress=_progress_cb, is_cancelled=is_cancelled)
       await slc.update(1.0, "Erase complete (thorough)")
       return output_path
   ```
   (`x,y,w,h` are the ROI already computed in `_stage_erase`.)

Check: run a Remix with `erase_coverage="thorough"` on any clip; the whole
band is clean in the output. Commit.

---

**STEP B — inpaint accepts an arbitrary per-segment mask (~45 min).**

Goal: let a segment carry a tight mask instead of a rectangle. Read
`inpaint.py::_build_segment_state` first (it currently fills a rectangle into
`mask_roi`).

1. In `_build_segment_state`, after computing `rx,ry,roi_w,roi_h` and the
   filled-rectangle `mask_roi`, add:
   ```python
   seg_mask = seg.get("mask")  # full-frame uint8 {0,255}, or None
   if seg_mask is not None:
       # crop the provided full-frame mask to this segment's ROI
       sub = seg_mask[ry:ry+roi_h, rx:rx+roi_w]
       if sub.shape == mask_roi.shape:
           mask_roi = (sub > 0).astype(np.uint8) * 255
           if dilate_px > 0:
               k = np.ones((dilate_px, dilate_px), np.uint8)
               mask_roi = cv2.dilate(mask_roi, k, iterations=1)
   ```
   Everything else (LaMa tensor build, batching) stays the same — it already
   consumes `mask_roi`.

Check: temporarily pass a hand-made circular mask for one segment; confirm
the output erases the circle, not the rectangle. Remove the temp code. Commit.

---

**STEP C — per-display segmentation + tight glyph/box masks (~2h).**

Goal: replace the lane-time-range segments with one segment per display,
each carrying a tight mask. Work in `caption_detector.py`.

1. After OCR collects `detections` (each has t, x, y, w, h, text, conf),
   group consecutive in-band detections into **displays** by text change:
   ```python
   def _group_displays(dets, sim_min=0.6, gap_s=0.8):
       # dets sorted by t. Start a new display when the joined text of the
       # frame differs (difflib ratio < sim_min) from the current display's,
       # or after a time gap > gap_s.
       ...
       # return [{start_t, end_t, lines:[(quad,text),...], best_frame_t}]
   ```
   Use `difflib.SequenceMatcher(None, a, b).ratio()` for similarity. A
   karaoke recolour keeps the same string → same display (good).
2. For each display, at its `best_frame_t` (highest-confidence sample), build
   the tight mask:
   ```python
   def _display_mask(frame_bgr, line_quads, vw, vh):
       full = np.zeros((vh, vw), np.uint8)
       for quad in line_quads:
           x,y,w,h = _bbox_from_easyocr(quad)
           crop = frame_bgr[y:y+h, x:x+w]
           gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
           # glyph foreground via Otsu (text is the high-contrast part)
           _, th = cv2.threshold(gray, 0, 255,
                                 cv2.THRESH_BINARY+cv2.THRESH_OTSU)
           # Otsu may invert (dark text on light) — pick the polarity whose
           # foreground is the MINORITY (text is fewer pixels than background)
           if th.mean() > 127: th = 255 - th
           # §11.3d box-vs-glyph: if the NON-text pixels are near-uniform
           # colour (a solid box), erase the whole bbox instead.
           bg = crop[th == 0]
           if bg.size and bg.reshape(-1,3).std(axis=0).mean() < 25:
               full[y:y+h, x:x+w] = 255            # BOX style
           else:
               full[y:y+h, x:x+w] = np.maximum(full[y:y+h, x:x+w], th)  # GLYPH
       return full
   ```
   You must re-open the video and seek to `best_frame_t` to get that frame
   (or cache frames during the OCR pass).
3. Emit segments: `{start_t, end_t, x, y, w, h (tight bbox of the mask),
   "mask": full_frame_mask}`. Feed these into `inpaint_region(segments=...)`.

Check: run on the RO TikTok; in the output, between words / around letters
the original background shows through (NOT a big erased rectangle), and the
text itself is gone. Eyeball 5 frames. Commit.

---

**STEP D — auto-localize the band, drop the manual box (~2.5h).**

Goal: find the caption lane automatically so the user passes NO rect.

1. New function:
   ```python
   def auto_locate_caption_band(video_path, speech_intervals=None) -> dict | None:
       # speech_intervals: list of (start_s, end_s) from the transcript, optional
       # 1) OCR-sample the WHOLE frame at ~3 fps.
       # 2) Keep only HELD-STILL detections: track each text box to the next
       #    sample; if its centre moves > DRIFT_MAX_FRAC*frame_diag, drop it.
       # 3) Cluster survivors by y-centre into lanes (reuse _Lane logic).
       # 4) Score each lane = total seconds it shows text. If speech_intervals
       #    given, multiply by overlap fraction with speech.
       # 5) Return the top lane's bbox {x,y,w,h} (padded), or None if no lane
       #    scores above a floor (→ caller uses Thorough or a default band).
   ```
2. In `_stage_erase`: when `auto_detect` is on and the request gave NO usable
   rect, call `auto_locate_caption_band(video_path, speech_intervals)` first
   and use its bbox as the ROI. Get `speech_intervals` from the transcript
   result already computed earlier in the pipeline (pass it into `_stage_erase`).
3. In the routers: make `erase_zone` optional when `erase_auto_detect=true`.
   For `/api/auto`, default to auto-detect + no zone so it's hands-off.

Check: run with NO box on a clip that has scene text OUTSIDE the caption band
(e.g. a sign). Output: caption gone, the sign untouched. Commit.

---

**STEP E — fade/boundary completeness (~1h).**

Goal: no ghost on fade-in/out frames. In `caption_detector`, after a
display's start/end are known, nudge them outward to the real first/last
visible frame using the presence signal (edge-density OR temporal-stability
from §11.3b-C) within ±BOUND_EXPAND_S, then keep the tight mask across the
whole range. Re-use the existing `_presence_segments` machinery, restricted
to each display's window.

Check: on a clip with clear fades, step through the output frame-by-frame at
the caption appear/disappear moments — no faint ghost. Commit.

### 11.12 TEST HARNESS (give this to the weaker model verbatim)

Create `server/scripts/verify_eraser.py` (throwaway, don't ship). It proves
the three things that matter: NO leak, TIGHT mask, scene text SAFE.

```python
"""Run AFTER an erase. Usage:
   server/.venv/bin/python scripts/verify_eraser.py OUTPUT.mp4 X Y W H
where X Y W H is the caption band (the auto-located or drawn ROI).
"""
import sys, cv2, numpy as np
from services.caption_detector import _get_reader

out_path = sys.argv[1]
bx, by, bw, bh = map(int, sys.argv[2:6])
reader = _get_reader()
cap = cv2.VideoCapture(out_path)
fps = cap.get(cv2.CAP_PROP_FPS) or 30
leaks, frames, erased_px = 0, 0, []
i = 0
while True:
    ok, fr = cap.read()
    if not ok: break
    if i % max(1, int(fps/5)) == 0:          # 5 fps
        frames += 1
        band = fr[by:by+bh, bx:bx+bw]
        # LEAK CHECK: any text left in the band?
        for _b, text, conf in reader.readtext(band):
            if conf and conf > 0.3 and text.strip():
                leaks += 1
                cv2.imwrite(f"/tmp/leak_{i}.png", fr)
                print(f"  LEAK @frame {i} t={i/fps:.2f}s: {text!r} conf={conf:.2f}")
                break
    i += 1
cap.release()
print(f"\nframes checked: {frames} | LEAKS: {leaks}  (target 0)")
print("dumped any leak frames to /tmp/leak_*.png — eyeball them too")
```

**How the weaker model uses it:**
1. Run the eraser on the RO TikTok (`tiktok.com/@hisytstory/...`) with the
   new tight+auto path.
2. Find the auto-located band (log it from `auto_locate_caption_band`, or
   print it). Run:
   ```
   server/.venv/bin/python scripts/verify_eraser.py <output.mp4> X Y W H
   ```
3. **Pass = `LEAKS: 0`.** If > 0, open the dumped `/tmp/leak_*.png`, see what
   leaked (fade frame? wrong band? box outline?), and fix the matching step
   (E for fades, D for band, C box-branch for outlines).
4. Tightness: also dump a frame with the mask overlaid (red) during the run
   and confirm it hugs the glyphs (or the box), not the whole band.
5. Scene-text safety: on a clip with text outside the band, confirm
   `verify_eraser.py` finds the OUTSIDE text still present (it should — you
   only erased the band) and the band text gone.

Acceptance for the whole feature: `LEAKS: 0` on 3 of the user's real clips,
scene text intact, and the overlaid mask visibly tighter than the full band.

---

End of plan.
