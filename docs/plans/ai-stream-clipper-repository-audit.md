# AI Stream Clipper — Repository Audit

Audit date: **2026-07-30**. Branch at audit time: `claude/portable-setup` @ `1a774c1`
(8 commits ahead of `main`, 0 behind — a strict superset). Working tree clean.
Feature branch created: **`claude/ai-stream-clipper`** (repo convention is `claude/<topic>`,
not `feature/<topic>` — every branch on the remote follows it and PRs merge to `main`).

Remote: `origin` → `https://github.com/brinza69/ClipForge.git` (fetch + push).

> `CLAUDE.md`'s key file map is dated 2026-07-28 and warns "don't trust a path here without
> checking it". Everything below was verified against the tree on 2026-07-30. Two of its claims
> are now stale and are corrected in §3.

---

## 1. Architecture summary

**Frontend** — Next.js **16.2.1** with Turbopack (`CLAUDE.md` says 15; it is 16), React 19.2.4,
TypeScript 5, TailwindCSS 4, shadcn/ui on top of `@base-ui/react`, `lucide-react` icons,
`framer-motion`, `sonner` toasts, `zustand`, `@tanstack/react-query` 5.
`npm run dev | build | lint | typecheck`. A `prepare` script points `core.hooksPath` at
`.githooks/`, whose `pre-commit` runs `tsc --noEmit` whenever staged `.ts/.tsx` files exist.

Routing is the App Router under `src/app/`. Existing routes: `remix`, `parallel`,
`parallel-sheets`, `doodle` (+ `doodle/[id]`), `captions`, `transcript`, `tts`, `silence`,
`utilities` (+ `caption-eraser`, `upscale`), `settings`. The sidebar
(`src/components/layout/sidebar.tsx`) also links **`/tiktok`, which does not exist** — a known
dead link.

The frontend never calls the backend directly. `next.config.ts` rewrites
`/worker-api/:path*` → `${WORKER_URL_INTERNAL||http://127.0.0.1:8420}/api/:path*`, plus
`/worker-thumbnails/*` and `/worker-doodle/*` for static media. Proxy timeout is raised to 5
minutes and body limit to 200 MB because some backend calls are genuinely slow/large.

**Backend** — FastAPI + uvicorn on port **8420**, SQLAlchemy 2.0 async + `aiosqlite`,
`pydantic-settings` with env prefix **`CLIPFORGE_`**. `server/main.py` wires 13 routers,
mounts four `StaticFiles` dirs (`/media`, `/exports`, `/thumbnails`, `/doodle-files`),
registers job handlers in the lifespan, and installs a catch-all exception handler that returns
structured `{detail:{error,message,details}}` JSON.

**Data** — one SQLite file at `data/db/clipforge.db`. Four tables:
`projects`, `jobs`, `transcripts`, `clips`. Schema evolution is *not* Alembic: `database.init_db()`
runs `CREATE TABLE IF NOT EXISTS` via `Base.metadata.create_all`, then a list of
`ALTER TABLE … ADD COLUMN` statements each wrapped in `try/except: pass`.

**Job queue** — `server/job_queue.py`. SQLite-persisted, polled once per second. **Two lanes**:
a heavy lane bounded by `settings.max_concurrent_jobs` (default 2) and a light "doodle" lane
(`DOODLE_LANE_TYPES`, limit 2) so API-bound wizard steps can never be starved by the GPU-bound
video factory. Supports enqueue / progress / complete / fail / cancel, best-effort workspace
cleanup on fail+cancel, and a startup `recover_stuck_jobs()` pass that requeues anything left
`running` by a crash or restart (capped at 50).

**Progress transport** — `GET /api/jobs/{id}/stream` is a real Server-Sent Events endpoint that
emits on change and closes on a terminal state, with a 1-hour safety deadline. Frontend
wizards additionally use plain `setInterval` polling of the project JSON.

**Pipelines** — `workers/remix_pipeline.py` (933 lines, the reference implementation),
`workers/parallel_pipeline.py`, `workers/doodle_pipeline.py`, `workers/utility_jobs.py`.

**Auth / authorization** — **none, anywhere.** No user table, no sessions, no tokens, no
ownership column. CORS is limited to `localhost:3000`. This is a single-user local studio.

**Logging / monitoring** — stdlib `logging` with a per-module logger namespace
(`clipforge.*`). No metrics exporter, no tracing, no Sentry.

**Testing** — pytest with `asyncio_mode = auto`, driving the real ASGI app in-process via
`httpx.ASGITransport` (`server/tests/conftest.py`). 24 tests in 2 files.

**Deployment** — `vercel.json` for the frontend; `DEPLOY.md`; `scripts/` holds
`start_all.ps1`-style local orchestration. **No `.github/workflows` at all** — there is no CI.

**GPU code** — `services/gpu_utils.py`, `services/upscaler.py` (Real-ESRGAN ncnn-Vulkan, with
the documented *reversed* Vulkan device index), `services/inpaint.py` (torch/LaMa with an
OpenCV CPU fallback), and faster-whisper's CUDA path with a silent-CPU-fallback detector.

---

## 2. Reusable modules (this is why the feature integrates instead of duplicating)

| Module | What it already gives us | How the clipper uses it |
| --- | --- | --- |
| `services/downloader.py` | `validate_url`, `detect_source_type`, `fetch_metadata` (no download), `download_video` with progress + cancellation, a **10-pattern error-classification table** producing `(error_code, suggestion)`, and a TikTok silent-HEVC audio-remux workaround | The entire ingestion layer. We add SSRF/limit checks around it rather than replacing it. |
| `services/transcriber.py` | faster-whisper in a **separate spawned process** (hard-killable), word-level timestamps + per-word probability, VAD, ≥900 s inputs auto-chunked to 600 s PCM chunks with global timestamp re-offsetting, watchdog, progress heartbeat | The transcription stage verbatim — with one additive change (§5). |
| `services/caption_overlays.py` | `build_overlays_ass()` (libass-compatible ASS writer, per-style cache, `\pos` placement), `render_preview_frame()` (single frame + burned overlays → PNG), `probe_video_dims()`, disk frame cache | Caption rendering **and** the clip preview frame endpoint. |
| `services/captioner_presets.py` | 7 tuned presets + `hex_to_ass_color()` + **TikTok safe-zone constants** (`SAFE_TOP=200`, `SAFE_CAPTION_BOTTOM=480`, `SAFE_HOOK_MID_Y=700`) measured against TikTok's real UI overlays | Caption presets and the safe-area collision checks. |
| `services/captioner_events.py` | `_group_words()` — punctuation-aware, pause-aware (>0.5 s), orphan-avoiding word grouping; word/phrase/line animations; hook, title, creator-tag and part-label event builders | Caption phrase grouping and the headline/watermark overlays. |
| `services/caption_aligner.py` | `align_words()` + `group_into_caption_chunks()` force-alignment | Re-timing captions after a user edits transcript text. |
| `services/font_manager.py` | `fonts_dir()` for libass `fontsdir=` | Every ASS render. |
| `job_queue.py` | Persistence, 2 lanes, cancel, progress, stuck-job recovery | All 6 clipper job types. |
| `routers/jobs.py` | SSE progress stream + cancel endpoint, already consumed by the sidebar badge | Progress UI, no new transport needed. |
| `services/transcript_cleaner.py` | Multi-engine LLM abstraction (ollama / openai / anthropic) + encrypted API-key storage | Headline generation, with a deterministic fallback when no engine is configured. |
| `services/cleanup.py` | `cleanup_job_workspace(project_id)` | Temp-file reclamation. |
| `services/secret_storage.py` | Encrypted key storage | No new secret handling invented. |
| `src/lib/api-error.ts` | `readApiError` / `errorDescription`, incl. a specific message for the dead-proxy case | Every frontend fetch. |
| `src/components/doodle/*` | The wizard UI vocabulary: `progress-steps`, `action-buttons`, `project-card`, `new-project-form` | Pattern reference for the clipper UI (not imported — copied idiom). |

## 3. Conflicting, duplicated or stale modules

1. **`ClipModel` is orphaned — and it is exactly our data model.** `models.py` defines a
   `clips` table with `start_time`, `end_time`, `duration`, `momentum_score`, `hook_strength`,
   `narrative_completeness`, `curiosity_score`, `emotional_intensity`, `caption_readability`,
   `confidence`, `transcript_text`, `transcript_segments`, `hook_text`, `explanation`, a
   `ClipStatus` enum (`candidate/approved/rejected/exporting/exported/failed`), `reframe_mode`,
   `reframe_data`, export paths and ~40 style columns. `ProjectStatus` has `scoring` and
   `ready`. `JobType` has `score`, `export`, `full_pipeline`. **Nothing creates, reads or
   renders any of it** — there is no `routers/clips.py`, no `services/scorer.py`, no
   `workers/pipeline.py`, and `main.py` registers only utility/remix/parallel/doodle handlers.
   `database.py` even carries a data-fix UPDATE for "projects stuck at 'downloaded' that already
   have scored clips", proving this path once ran.
   **Decision: revive and extend it. Do not create a parallel model.** (Brief §3: "Do not
   rewrite working systems merely because another architecture would be cleaner.")
2. **`services/captioner.py::generate_captions()` is dead code with zero callers** (confirmed —
   `CLAUDE.md` rule 6 is correct). But the module is *not* deletable: it re-exports
   `DEFAULT_PRESETS` and `hex_to_ass_color`, and `caption_overlays.py` imports them **from
   `services.captioner`**, not from `captioner_presets`. Leave it alone; import from
   `captioner_presets` in new code.
3. **Duplicate columns on `ClipModel`.** `caption_font_size`, `caption_text_color`,
   `hook_font_size`, `hook_text_color`, `hook_bg_color` are each declared **twice** in the class
   body (once as `Float`/`String(9)` around lines 213–217, then again as `Integer`/`String(20)`
   at 220–227). Python keeps the last definition, so the effective types are the second set.
   Harmless today but confusing. **Not fixed in this change** — touching it risks the existing
   `_style_migrations` list; logged as tech debt.
4. **`CLAUDE.md` is stale in two places** (it invites this correction itself):
   Next.js is **16.2.1**, not 15; and the "Frontend state pattern" note says React Query is
   "wired up but only `src/app/settings` uses it" — accurate, and we keep it that way.
5. **The sidebar links to `/tiktok`, which does not exist** — pre-existing dead nav item.
   Out of scope here; not touched.
6. **`transcriber._clean_text()` strips *all* punctuation and lowercases everything.** For the
   existing caption path that is deliberate. For a clipper it is destructive: sentence
   boundaries, question detection and quotable-phrase detection all depend on `.?!,`. This is
   the single most important integration conflict — see §5.

## 4. Missing dependencies

| Need | Status | Plan |
| --- | --- | --- |
| `numpy` | Already present transitively (opencv-python, faster-whisper, torch) but **not declared** | Declare it explicitly in `requirements.txt` — we now depend on it directly. |
| `opencv-python` | Already declared | Reuse for face/webcam/motion detection. Haar cascades ship inside the wheel (`cv2.data.haarcascades`) — **no model download, no new dependency**. |
| ffmpeg / ffprobe | Assumed on PATH; `settings.ffmpeg_location` overrides | Reuse existing resolution helpers. |
| scikit-learn / xgboost (for learn-to-rank) | **Not present** | **Do not add.** The baseline ranker is implemented as a ~120-line pure-Python/numpy logistic regression. Adding sklearn to a torch+CUDA venv for one model is not worth the dependency risk. |
| sentence-transformers (embeddings) | **Not present** | **Do not add.** Duplicate detection uses lexical similarity (token Jaccard + trigram cosine) which is adequate for near-duplicate transcript windows. Real embeddings are a documented future upgrade. |
| A VLM for Pass D | Not present locally | Pass D is wired to the **existing** `transcript_cleaner` LLM abstraction (ollama/openai/anthropic) using transcript text only, and is **skipped entirely** if no engine is configured. No new provider. |

## 5. Data-model changes

**Reuse, don't replace.** Additive only, following `CLAUDE.md` rule 3 (add to `models.py` **and**
`database.py`'s migration lists).

`projects` — new columns:

| Column | Type | Purpose |
| --- | --- | --- |
| `clipper_settings` | `TEXT` (JSON) | The whole source-setup form: clip count, min/max duration, platform, fps, language, editing prefs, layout prefs. |
| `content_type` | `VARCHAR(30)` | Detected: `gaming`/`podcast`/`interview`/`irl`/`commentary`/`talking_head`/`tutorial`/`unknown`. |
| `content_type_confidence` | `REAL` | 0–1. |
| `content_type_override` | `VARCHAR(30)` | User's manual override — always wins (brief §16). |
| `analysis_version` | `VARCHAR(20)` | Pipeline version that produced the artifacts, for cache invalidation. |
| `rights_confirmed` | `BOOLEAN` | The user's ownership/permission affirmation (brief §5). |
| `source_kind` | `VARCHAR(20)` | `url` / `upload` / `library`. |

`clips` — new columns (on top of the ~55 that already exist):

| Column | Type | Purpose |
| --- | --- | --- |
| `overall_score` | `REAL` | 0–100. |
| `sub_scores` | `TEXT` (JSON) | The 16 sub-scores from brief §15. |
| `score_reason` | `TEXT` | Human-readable explanation. |
| `layout_plan` | `TEXT` (JSON) | Crop rectangles, layout mode, keyframes, warnings. |
| `caption_plan` | `TEXT` (JSON) | Word-grouped caption chunks + style. |
| `headline_text` | `TEXT` | Separate from `hook_text` (legacy). |
| `content_type` | `VARCHAR(30)` | Per-clip, may differ from the project. |
| `warnings` | `TEXT` (JSON) | Technical warnings, e.g. low-res facecam. |
| `dedupe_group` | `VARCHAR(12)` | Groups near-duplicates; the winner is surfaced, the rest are "alternatives". |
| `is_alternative` | `BOOLEAN` | True for suppressed duplicates (brief §14 "reveal discarded alternatives"). |
| `rank_position` | `INTEGER` | Final ordering after dedupe + diversity. |
| `feature_vector` | `TEXT` (JSON) | Frozen features for the ranker. |
| `ranker_version` | `VARCHAR(20)` | Which model version scored it. |
| `preview_path` | `TEXT` | Low-res proxy preview render. |

**New table `clip_feedback`** — append-only event log for the learning loop (brief §29):
`id, clip_id, project_id, event_type, payload (JSON), created_at`, indexed on `clip_id` and
`event_type`.

**Heavy artifacts do NOT go in SQLite.** Scene lists, audio-energy timelines, motion timelines,
face tracks and the full word list live on disk as JSON under
`data/clipper/{project_id}/analysis/`. This mirrors the doodle/tiktok disk-JSON pattern
(`CLAUDE.md` rule 4) and keeps rows small.

**Migration risk is low**: all changes are `ADD COLUMN` on existing tables plus one new table.
No column is dropped, renamed or retyped. Existing rows get `NULL` and every read path
tolerates `NULL`.

## 6. API changes

New router `server/routers/clipper.py`, prefix **`/api/clipper`**, following existing
conventions (raw dicts or small Pydantic models, structured errors, trailing-slash-canonical
list routes):

```
POST   /api/clipper/projects                 create (url | upload | library) + settings
GET    /api/clipper/projects                 list (summaries)
GET    /api/clipper/projects/{id}            full project + clips
DELETE /api/clipper/projects/{id}            delete + purge artifacts
POST   /api/clipper/projects/{id}/preview    metadata-only probe (no download)
POST   /api/clipper/projects/{id}/analyze    enqueue the analysis pipeline
POST   /api/clipper/projects/{id}/cancel     cancel running jobs
POST   /api/clipper/projects/{id}/retry      retry from the last failed stage
PATCH  /api/clipper/projects/{id}/settings   update settings / content-type override
GET    /api/clipper/projects/{id}/artifacts/{name}   read a cached analysis artifact
GET    /api/clipper/clips/{clip_id}          one candidate
PATCH  /api/clipper/clips/{clip_id}          boundaries, transcript, captions, headline, layout, crop
POST   /api/clipper/clips/{clip_id}/approve
POST   /api/clipper/clips/{clip_id}/reject
POST   /api/clipper/clips/{clip_id}/regenerate
GET    /api/clipper/clips/{clip_id}/preview-frame   PNG via render_preview_frame
POST   /api/clipper/clips/{clip_id}/export   enqueue the render
POST   /api/clipper/clips/{clip_id}/feedback record an event
POST   /api/clipper/clips/{clip_id}/performance  attach post-publication metrics
GET    /api/clipper/presets                  caption presets
GET    /api/clipper/ranker                   ranker status + version
POST   /api/clipper/ranker/train             train the baseline ranker from feedback
```

Progress reuses the **existing** `GET /api/jobs/{id}/stream` SSE endpoint and
`POST /api/jobs/{id}/cancel`. No new transport.

*Not added*: authentication, per-user rate limits, pagination cursors. There is no user concept
in this app, and inventing one would be a fake control. See §9.

## 7. UI changes

- New nav item **"AI Stream Clipper"** in `src/components/layout/sidebar.tsx`
  (route `/ai-stream-clipper`, `Scissors` icon), inserted after the pipeline group.
- `src/app/ai-stream-clipper/page.tsx` — project list + create form.
- `src/app/ai-stream-clipper/[id]/page.tsx` — progress screen → review dashboard → editor.
- `src/components/clipper/` — `source-form`, `analysis-progress`, `candidate-card`,
  `candidate-grid` (sort + filter + batch actions), `clip-editor`, `crop-editor`,
  `caption-controls`, `score-breakdown`.
- `src/types/clipper.ts` — shared types.

State follows `CLAUDE.md` rule 5: plain `useState` + `fetch` + `setInterval`/SSE, **not** React
Query; step/progress state is re-derived from the server, never stored client-side; all calls go
through `/worker-api/...`.

## 8. Worker changes

New `server/workers/clipper_pipeline.py` registering six job types:

| Job type | Lane | Why |
| --- | --- | --- |
| `clipper_ingest` | heavy | yt-dlp download + ffmpeg proxy — disk/CPU heavy. |
| `clipper_transcribe` | heavy | faster-whisper, GPU. |
| `clipper_analyze` | light | ffmpeg stats + OpenCV on a small proxy; must not be starved. |
| `clipper_score` | light | Pure CPU + optional LLM call. |
| `clipper_export` | heavy | Full-res 1080×1920 encode. |
| `clipper_preview` | light | Single short proxy render. |

The light ones join `DOODLE_LANE_TYPES` for exactly the reason documented there. Also registered
in `main.py`'s lifespan next to the other `register_*_handlers` calls.

## 9. Security risks

| Risk | Existing state | Action |
| --- | --- | --- |
| **SSRF** — user-supplied URL fetched server-side | `validate_url()` only checks scheme/length. Nothing blocks `http://169.254.169.254/`, `http://localhost:8420/`, or a DNS name resolving to RFC1918. | **New `services/clipper/urlguard.py`**: scheme allowlist, host resolution, rejection of private/loopback/link-local/CGNAT/multicast/reserved ranges for *every* resolved A/AAAA record, port allowlist, and a redirect cap. Applied before any fetch. |
| **Command injection** | Existing code already uses argument arrays, never `shell=True` — good. | Keep the same discipline; every new ffmpeg call is a list. Filter-graph strings that embed paths reuse the existing libass escaping helper. |
| **Path traversal** | Project ids are server-generated 12-hex; artifact names would be user-reachable via the artifacts endpoint. | Artifact names validated against a fixed allowlist of known filenames; paths resolved and asserted to stay under the project dir. |
| **Zip/archive & upload abuse** | `python-multipart` present; uploads exist elsewhere. | Uploads: extension + ffprobe content validation, size cap, sanitized filename, written under the project dir only. |
| **Resource exhaustion** | `max_concurrent_jobs=2` exists. | Add configurable max source duration and max upload size; check free disk before download; bound analysis frame sampling. |
| **Secret leakage in logs** | `secret_storage` encrypts at rest; logging is verbose. | Never log full transcripts, never log the source URL at INFO for non-public sources, never log API keys. Error messages truncate to 300 chars as elsewhere. |
| **No authn/authz** | None exists app-wide. | **Explicitly not solved.** Documented as an accepted property of a single-user local app. The brief's "never expose one user's media to another user" is vacuously satisfied — there is exactly one user, bound to `localhost`. We do **not** ship a fake auth layer that implies protection it cannot provide. |

## 10. Performance risks

| Risk | Mitigation |
| --- | --- |
| Multi-hour VOD transcription | Already chunked (600 s) with bounded RAM; runs in a killable subprocess. |
| Analysing full-resolution 60 fps footage | Never done. A 480p-wide, low-fps proxy is generated once and every analysis pass reads only that. |
| OpenCV face detection per frame | Sampled, not per-frame: a bounded number of frames per candidate window, at a stride derived from the source duration. |
| Loading the whole video into memory | No stage ever reads the media into Python memory — everything is an ffmpeg subprocess writing to disk. |
| Re-analysis on every edit | Artifacts are cached on disk and keyed by `analysis_version`; edits re-render only the one clip. |
| Two encodes per export | Avoided: crop + scale + pad + caption burn are fused into **one** ffmpeg pass, matching `remix_pipeline._stage_match_and_caption`'s rationale. |
| Chatty subprocess deadlock | `CLAUDE.md` gotcha: never `stderr=PIPE` on a chatty child without draining. Our long ffmpeg runs use `capture_output` with a timeout (as the existing code does) or redirect to a file. |

## 11. Migration risks

- **Low.** Every schema change is `ADD COLUMN` (idempotent via try/except) or a new table.
- The `_clip_migrations` / `_style_migrations` lists are order-independent and additive; a new
  `_clipper_migrations` list is appended without touching the existing ones.
- **Rollback**: dropping the feature branch leaves the extra columns in place on any DB that ran
  the new `init_db()`. Unused nullable columns are inert — no existing query selects `*` into a
  strict schema. Verified: every existing read path names its columns via the ORM.
- The new `clip_feedback` table is created by `create_all`; dropping it is a one-line SQL.
- No data is rewritten, no column is dropped or retyped.

## 12. Expected files to add / modify

**Add — backend**
```
server/services/clipper/__init__.py
server/services/clipper/urlguard.py        SSRF + URL policy
server/services/clipper/storage.py         on-disk artifact store
server/services/clipper/ingest.py          validate → metadata → download → proxy → audio → thumbs
server/services/clipper/signals.py         Pass A: audio energy, silence, scene, motion timelines
server/services/clipper/segmentation.py    Pass B: semantic windows
server/services/clipper/candidates.py      Pass C: candidate generation + boundary refinement
server/services/clipper/scoring.py         sub-scores + per-content-type profiles + reason
server/services/clipper/dedupe.py          overlap/similarity/diversity
server/services/clipper/content_type.py    gaming + content-type detection
server/services/clipper/layout.py          webcam/gameplay regions → 9:16 layout plan
server/services/clipper/captions.py        word-sync caption plan
server/services/clipper/headline.py        headline generation + fallback
server/services/clipper/render.py          fused ffmpeg render spec
server/services/clipper/feedback.py        event store + feature extraction
server/services/clipper/ranker.py          baseline learn-to-rank + versioning
server/routers/clipper.py
server/workers/clipper_pipeline.py
server/tests/test_clipper_*.py
```

**Add — frontend**
```
src/app/ai-stream-clipper/page.tsx
src/app/ai-stream-clipper/[id]/page.tsx
src/components/clipper/*.tsx
src/types/clipper.ts
```

**Add — root**
```
.env.example                               (referenced by .gitignore but absent)
docs/research/ai-stream-clipper-competitive-analysis.md
docs/plans/ai-stream-clipper-{repository-audit,architecture,decisions,task-board}.md
docs/ai-stream-clipper-runbook.md
```

**Modify**
```
server/models.py          + columns, + ClipFeedbackModel, + 6 JobType members
server/database.py        + _clipper_project_migrations / _clipper_clip_migrations
server/config.py          + clipper settings block
server/main.py            + include_router(clipper), + register_clipper_handlers
server/job_queue.py       + light clipper job types in DOODLE_LANE_TYPES
server/requirements.txt   + numpy (explicit)
server/services/transcriber.py   + keep_punctuation flag (backwards-compatible)
src/components/layout/sidebar.tsx  + nav item
CLAUDE.md                 + clipper section, + corrections
```

## 13. Phased implementation plan

Matches brief §40, collapsed to what one delivery can actually validate:

| Phase | Content | Gate |
| --- | --- | --- |
| 1 | Research + audit + architecture + branch | Docs exist, branch created |
| 2 | Schema, config, job types, queue lanes, router skeleton | `init_db()` runs, smoke tests pass |
| 3 | URL guard, ingestion, proxy, audio, transcription flag | Unit tests on guard + a real ingest |
| 4 | Signals, segmentation, candidates, scoring, dedupe | Unit tests on pure functions |
| 5 | Content-type + gaming detection, layout engine, captions, headline | Unit tests incl. fallbacks |
| 6 | Router + worker + progress + cancel/retry | Integration tests |
| 7 | Frontend: source form, progress, dashboard, editor | `tsc` + `next build` |
| 8 | Feedback events + baseline ranker + evaluation | Unit tests on ranker maths |
| 9 | Hardening, docs, `.env.example`, full regression | Full gate (§38) |
| 10 | Commit, push, draft PR | Git output quoted verbatim |

## 14. Rollback strategy

1. **Code**: the entire feature is additive and confined to new files plus small, clearly
   delimited edits. `git revert` of the feature commits, or simply not merging the branch,
   removes it. The branch is `claude/ai-stream-clipper`; `main` is untouched.
2. **Nav**: removing the one `navItems` entry hides the feature instantly without a rebuild of
   anything else.
3. **Schema**: added columns are nullable and unread by existing code; they can be left in place.
   `DROP TABLE clip_feedback;` is the only optional cleanup.
4. **Runtime**: the six new job types are only enqueued from clipper endpoints. If
   `register_clipper_handlers` is not called, any stray job fails cleanly with
   "No handler registered for job type" — the queue already handles that path.
5. **Artifacts**: everything lives under `data/clipper/`, which is git-ignored (`data/*`).
   Deleting that directory reclaims all disk with no code impact.
6. **Feature flag**: `CLIPFORGE_CLIPPER_ENABLED` (default true) gates router registration, so
   the backend can be run without the feature while keeping the code in place.

---

## Appendix A — verified test baseline (pre-change)

```
D:\clipforge\server> .venv\Scripts\python.exe -m pytest tests/ -v
...
FAILED tests/test_tiktok_transform.py::test_list_endpoint_ok - assert 404 == 200
FAILED tests/test_tiktok_transform.py::test_create_rejects_invalid_url - assert 404 == 400
======================== 2 failed, 22 passed in 20.32s ========================
```

Both failures are **pre-existing and unrelated to this work**: `routers/tiktok.py` was never
written, so `/api/tiktok/*` 404s. This is already documented in `CLAUDE.md`. The regression bar
for this change is therefore **22 passed / 2 pre-existing failures**, plus the new clipper tests.
