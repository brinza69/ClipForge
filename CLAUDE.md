# ClipForge — CLAUDE.md

## Project Context

ClipForge is a local AI video clipping studio:
- **Frontend**: Next.js 15 (Turbopack), TailwindCSS, shadcn/ui, React Query — `src/`
- **Backend**: FastAPI + SQLite (aiosqlite) + SQLAlchemy async — `server/`
- **Pipelines** (the real render paths — there is no `services/exporter.py`):
  - `workers/remix_pipeline.py` — download → transcribe → erase → TTS → speed-match + caption-burn (one fused encode) → descriptions. Forces 1080×1920 @ 60 fps.
  - `workers/parallel_pipeline.py` — reuses every remix stage to fan one source out into 1–4 variants.
  - `workers/doodle_pipeline.py` — Auto Story Doodle (script → TTS → images → render).
  - `workers/tiktok_pipeline.py` — **NOT BUILT YET.** Video Transformare TikTok wizard (see
    `docs/clipforge-transformation-decisions.md`). Only the service layer
    (`services/tiktok_transform/`), the `JobType` entries, the queue lane and the sidebar link
    exist. **Missing: `routers/tiktok.py`, `workers/tiktok_pipeline.py`, `src/app/tiktok/`,
    `src/components/tiktok/`, `src/types/tiktok.ts`.** Two tests in
    `server/tests/test_tiktok_transform.py` fail with 404 because the router is not mounted, and
    the sidebar's "Video Transformare TikTok" item links to a page that does not exist.
  - `workers/utility_jobs.py` — standalone tools (erase, silence, upscale, caption burn).
- **Dev server**: `npm run dev` on port 3000; backend: `cd server && uvicorn main:app --port 8420 --reload`
- **DB migrations**: add new columns in `server/database.py` `init_db()` via `ALTER TABLE ... ADD COLUMN` (safe/idempotent)

---

## Rules for Every Agent

### 1. Always read the relevant PRP before writing code
PRPs live in `PRPs/`. Each PRP contains goal, codebase context, gotchas, and the exact task list. Read it first; don't re-explore the whole codebase.

### 2. File size limit
Never let a file exceed 500 lines. If a file approaches this, split it.

### 3. DB schema changes
Always add new SQLite columns BOTH in `models.py` (SQLAlchemy model) AND in `database.py` `_clip_migrations` list (for existing DBs). Format: `("column_name", "SQLITE_TYPE")`.

### 4. Two persistence patterns — pick the right one
- **Feature projects** (doodle, TikTok wizard): a per-project JSON file on disk is the source of
  truth (`services/doodle/storage.py`, `services/tiktok_transform/storage.py`). No SQL table, no
  Pydantic response models — routers return raw dicts. `JobModel.project_id` has **no FK**, which is
  what lets these disk-only projects enqueue jobs.
- **SQL entities**: add the column in `models.py` AND the matching migration list in `database.py`.

### 5. Frontend state pattern
Wizard pages (`src/app/doodle/[id]`; `src/app/tiktok/[id]` is planned, not built) use plain `useState` + `fetch` +
`setInterval` polling — **not** React Query (it is wired up but only `src/app/settings` uses it).
Never store step progress on the client: re-derive it from the server's project JSON. Call the
backend through the Next proxy (`/worker-api/...`), not `src/lib/api.ts` (legacy, near-empty).

### 6. Captioner integration
The live caption path is `services/caption_overlays.py` (`build_overlays_ass`, `render_preview_frame`),
using the presets in `services/captioner_presets.py`. **`services/captioner.py::generate_captions()`
is dead code with zero callers** — don't build on it.

### 7. Pipeline pass-through
Caption/render params flow through the pipeline's own stage functions — e.g.
`remix_pipeline._stage_match_and_caption()`, which fuses speed-match + scale + caption burn into a
single ffmpeg encode on purpose (a second encode would add a generation of compression loss).

### 8. No speculative abstractions
Don't add helpers for one-off operations. Don't add error handling for impossible scenarios. Keep PRPs focused on what's actually being built.

### 9. Commit discipline
One commit per batch. Message format: `feat(scope): description` or `fix(scope): description`. Never skip tests or hooks.

### 10. Token efficiency
- Sub-agents: give them the relevant PRP + file paths only. Don't dump the whole repo.
- Don't re-read files you already read in this session.
- Use Grep/Glob for targeted lookups; only use full reads for files you'll edit.

---

## Key File Map

Verified 2026-07-28. The old map listed `editor/[id]`, `routers/clips.py`, `scorer.py`,
`exporter.py` and `workers/pipeline.py` — **none of those exist**; the clip-editor architecture was
superseded by the remix/parallel pipelines. Don't trust a path here without checking it.

```
clipforge/
├── src/
│   ├── app/                         ← routes: remix, parallel, parallel-sheets, doodle,
│   │   │                              tiktok, captions, transcript, tts, silence,
│   │   │                              utilities, settings
│   │   ├── doodle/[id]/page.tsx     ← wizard reference implementation
│   │   └── tiktok/[id]/page.tsx     ← PLANNED, does not exist yet
│   ├── components/
│   │   ├── layout/sidebar.tsx       ← Nav items
│   │   ├── doodle/                  ← wizard component patterns (progress-steps, actions)
│   │   └── tiktok/                  ← PLANNED, does not exist yet
│   ├── lib/api-error.ts             ← readApiError/errorDescription (use this everywhere)
│   └── types/                       ← doodle.ts (tiktok.ts PLANNED; index.ts nearly empty)
├── server/
│   ├── models.py                    ← SQLAlchemy ORM + JobType enum
│   ├── database.py                  ← DB setup + init_db() column migrations
│   ├── job_queue.py                 ← JobQueue: register_handler/update_progress, 2 lanes
│   ├── routers/                     ← jobs, utilities, doodle, remix, parallel, … (tiktok PLANNED)
│   ├── services/
│   │   ├── downloader.py            ← yt-dlp: validate_url/fetch_metadata/download_video
│   │   ├── caption_overlays.py      ← LIVE ASS builder + preview frame + probe dims
│   │   ├── captioner_presets.py     ← DEFAULT_PRESETS + 9:16 safe-zone constants
│   │   ├── speed_match.py           ← compute_speed_plan (sync video to voice, 60 fps)
│   │   ├── elevenlabs.py            ← synthesize() (speed clamped 0.7–1.2, returns MP3)
│   │   ├── transcript_cleaner.py    ← multi-engine LLM + API-key storage
│   │   ├── upscaler.py              ← Real-ESRGAN AI video upscale
│   │   ├── doodle/                  ← storage.py (storyboard.json) + renderer
│   │   └── tiktok_transform/        ← storage.py (project.json) + frames/vision/script/
│   │                                  voice/subtitles/montage/thumbnails/description
│   └── workers/                     ← remix_, parallel_, doodle_, utility_jobs (tiktok_ PLANNED)
├── docs/clipforge-transformation-decisions.md  ← TikTok wizard contracts + decisions
└── PRPs/                            ← Implementation blueprints for each feature batch
```

---

## Known Gotchas

```python
# CRITICAL: SQLite ALTER TABLE silently fails if column exists — always wrap in try/except in init_db()
# CRITICAL: SQLAlchemy async sessions — never use .refresh() after .execute(update()); use session.get() instead
# CRITICAL: pysubs2 ASS colors are &HAABBGGRR (reversed from hex). Use hex_to_ass_color() in captioner_presets.py
# CRITICAL: never launch a chatty subprocess (realesrgan, ffmpeg) with stderr=PIPE unless you drain
#           it — the 64KB pipe buffer fills and the child blocks forever. Redirect to a file.
# CRITICAL: ncnn/Vulkan GPU index is REVERSED vs nvidia-smi on this rig (-g 0 = RTX 3060)
# CRITICAL: FFmpeg on Windows needs even dimensions for H.264 (force width/height to nearest even)
# CRITICAL: faster-whisper on CPU is ~1x realtime — transcription is slow by design, not a bug
# CRITICAL: Next.js rewrites /api/* → backend. Don't call the backend directly from frontend; use api.ts helpers
# CRITICAL: React Query keys: ["clip", clipId] and ["project", project_id] — invalidate both after mutations
# CRITICAL: Turbopack active — no webpack config in next.config.ts
```
