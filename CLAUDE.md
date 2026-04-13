# ClipForge — CLAUDE.md

## Project Context

ClipForge is a local AI video clipping studio:
- **Frontend**: Next.js 15 (Turbopack), TailwindCSS, shadcn/ui, React Query — `src/`
- **Backend**: FastAPI + SQLite (aiosqlite) + SQLAlchemy async — `server/`
- **Pipeline**: yt-dlp download → faster-whisper transcription → LLM scoring → FFmpeg export with ASS captions
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

### 4. API field chain
Any new clip field must flow through all 4 layers:
`models.py` → `schemas.py (ClipResponse)` → `routers/clips.py (ClipUpdate)` → frontend `src/types/index.ts (Clip)`

### 5. Frontend state pattern
Editor state lives in `src/app/editor/[id]/page.tsx`. New fields: (a) add `useState`, (b) initialize in `useEffect` from `clip`, (c) include in `buildSaveData()`, (d) pass to `_CaptionOverlay`.

### 6. Captioner integration
Custom style values override preset values inside `generate_captions()` in `server/services/captioner.py`. Pass them as explicit optional params; don't mutate the preset dict.

### 7. Pipeline pass-through
When captioner gets new params, update `handle_export` in `server/workers/pipeline.py` to read the new clip columns and pass them.

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

```
clipforge/
├── src/
│   ├── app/
│   │   ├── editor/[id]/page.tsx     ← Main editor (700 lines) — all clip settings UI
│   │   ├── utilities/page.tsx       ← Utilities (downloader, eraser)
│   │   └── ...
│   ├── components/
│   │   ├── layout/sidebar.tsx       ← Nav items
│   │   └── clips/clip-card.tsx      ← Clip list card
│   ├── lib/api.ts                   ← API client (typed, uses WORKER_URL)
│   └── types/index.ts               ← All TypeScript types
├── server/
│   ├── models.py                    ← SQLAlchemy ORM (ClipModel, ProjectModel, etc.)
│   ├── schemas.py                   ← Pydantic schemas (ClipResponse, ProjectResponse)
│   ├── database.py                  ← DB setup + init_db() with column migrations
│   ├── routers/clips.py             ← Clip CRUD endpoints (ClipUpdate = patch schema)
│   ├── services/
│   │   ├── captioner.py             ← ASS subtitle generator (generate_captions)
│   │   ├── scorer.py                ← Clip scoring + hook text generation
│   │   ├── transcriber.py           ← faster-whisper wrapper
│   │   └── exporter.py              ← FFmpeg export
│   └── workers/pipeline.py          ← handle_export, handle_transcribe, handle_score
└── PRPs/                            ← Implementation blueprints for each feature batch
```

---

## Known Gotchas

```python
# CRITICAL: SQLite ALTER TABLE silently fails if column exists — always wrap in try/except in init_db()
# CRITICAL: SQLAlchemy async sessions — never use .refresh() after .execute(update()); use session.get() instead
# CRITICAL: pysubs2 ASS colors are &HAABBGGRR (reversed from hex). Use hex_to_ass_color() in captioner.py
# CRITICAL: FFmpeg on Windows needs even dimensions for H.264 (force width/height to nearest even)
# CRITICAL: faster-whisper on CPU is ~1x realtime — transcription is slow by design, not a bug
# CRITICAL: Next.js rewrites /api/* → backend. Don't call the backend directly from frontend; use api.ts helpers
# CRITICAL: React Query keys: ["clip", clipId] and ["project", project_id] — invalidate both after mutations
# CRITICAL: Turbopack active — no webpack config in next.config.ts
```
