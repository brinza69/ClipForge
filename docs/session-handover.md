# ClipForge — Session Handover (2026-05-27/28)

> **Purpose:** Comprehensive context dump for the next Claude conversation. The user is Romanian, prefers replies in Romanian (but Markdown / code blocks / file paths stay English). Keep messages short and concrete unless the user explicitly asks for a deep dive.

---

## 0. STANDING RULES — READ THIS FIRST AND OBEY EVERY SESSION

**These rules are permanent and self-perpetuating. Future sessions inherit
them automatically by reading this file. The user should NEVER have to
re-state them; the model enforces them on itself.**

### R1. Document EVERY meaningful action in this file (the handover), live.

The instant you start a meaningful piece of work — a new feature, a fix,
a refactor, a design exploration, a real-world test, anything beyond a
trivial 1-line edit — you MUST add an entry to this handover file
describing what you're about to do, WITHOUT WAITING FOR THE USER TO ASK.

- Add it BEFORE or DURING the work (not as an afterthought 10 commits later).
- Update it AGAIN when the work finishes — what was actually shipped, the
  commit SHA(s), what was confirmed and what's still open / risky.
- Use the latest session number (e.g. `## S5.x — short title`); create the
  session header on your first edit of a fresh conversation.
- Write the entry as if a different model will read it cold — name files,
  commits, decisions, gotchas. No "see chat above"; the chat is gone for
  the next session.
- Mirror to root: after any update to `docs/session-handover.md`, run
  `cp docs/session-handover.md SESSION-HANDOVER.md`. (Both files must
  stay in sync — the root is the visible copy.)
- Commit message for handover updates uses `docs(scope): …`.

The user has made this a hard requirement: silent work that doesn't
update this file is a process bug. If a session ends without entries
describing what happened, the next session starts blind.

### R2. R1 itself is permanent.

This standing-rules section MUST be preserved verbatim in every future
version of this file. If you reorganize the file, R1 stays exactly here
at the top. Do NOT remove it, water it down, or move it below the
session entries. Future-you reads this and obeys it the same way today-you
does.

### R3. Anything else marked "Standing rules" by the user gets added below.

When the user says "add this as a rule" / "from now on…", append the new
rule here as R4, R5, … with the same permanence as R1.

---

## 0. Who/what/where

- **User:** vlado, Windows + WSL2 setup
- **Hardware:** RTX 2080 Super (8GB), NVIDIA driver 561.09 (NOT 570+ → NVENC blocked for now)
- **Project root:** `F:\ClipForge` (mounted as `/mnt/f/ClipForge` from WSL)
- **Tech stack:**
  - Backend: FastAPI + SQLAlchemy async + aiosqlite on **port 8420**
  - Frontend: Next.js 15 Turbopack on **port 3000**
  - Python venv at `server/.venv` (WSL-style, Linux paths) — pip is `python -m pip` because the bin/pip shebang is broken
  - dev tooling: `./dev.sh start|stop|restart [backend|frontend]|status`
- **GitHub:** `brinza69/ClipForge`
- **API keys configured (server-side, gitignored):** ElevenLabs, OpenAI
  - `data/tts_config.json` ← ElevenLabs key
  - `data/transcript_config.json` ← OpenAI key
  - `data/drive_credentials.json` ← Google Drive (if used)

### Rules that override defaults

From the project `CLAUDE.md`:
1. Always read the relevant PRP in `PRPs/` before writing code.
2. **File size limit 500 lines** — split if approaching.
3. DB schema changes: BOTH `models.py` AND `database.py:_clip_migrations`.
4. Any new clip field must flow through: `models.py` → `schemas.py` → `routers/clips.py` (`ClipUpdate`) → `src/types/index.ts`.
5. Editor state lives in `src/app/editor/[id]/page.tsx`.
6. Captioner custom style values override preset values inside `generate_captions()`.
7. Don't add speculative abstractions or impossible-error handlers.
8. Commits: `feat(scope): description` or `fix(scope): description`. Never skip hooks.

### User's preferred test data

- Romanian TikTok story video: `https://www.tiktok.com/@hisytstory/video/7642931031720348941`
- Source for testing the eraser, OCR, TTS in RO, and the commentator pipeline.

### Voices/personae they like

- **ElevenLabs Sarah** voice id `EXAVITQu4vr4xnSDxMaL` for RO TTS
- **Inter Black Italic** caption template (custom, installed in this session)
- **Povestitor cel verde** commentator preset (green-screen, chroma key `#AAD714`)

---

## 1. Two PRs created across the session

### PR #15 — `claude/silence-remover-and-improvements` (still open, not merged when I left)

Initial big drop covering:
- Transcript Studio (`/transcript`) with Ollama / OpenAI / Anthropic engines
- Silence Remover (`/silence`) — pydub-based, mirrors NeuralFalcon HF Space algorithm
- LaMa eraser speedup (`cudnn.benchmark`, batch 8→16, libx264 preset bump)
- ElevenLabs scoped-key fix (verify via `/v1/voices` instead of `/v1/user`)

### PR #16 — `claude/remix-pipeline-and-commentator` (also open)

Branched off #15, so until #15 merges PR #16's diff shows both commits. This PR's commit covers everything in §§3–6 below.

---

## 2. Project map (key files written/changed in this session)

```
F:\ClipForge\
├── dev.sh                                    # bumped to export 9 nvidia/* lib dirs for CUDA
├── next.config.ts                            # /worker-api proxy rewrite (used for same-origin fetches)
├── server\
│   ├── config.py                             # whisper_model bumped "small" → "medium"
│   ├── main.py                               # mounts captions, remix, commentators routers
│   ├── models.py                             # new JobTypes: silence_remove, caption_burn, remix_pipeline, commentator_bg_remove
│   ├── routers\
│   │   ├── captions.py            (NEW)      # Caption Studio CRUD + preview-frame + burn
│   │   ├── commentators.py        (NEW)      # presets CRUD + AI processing + chroma patch
│   │   ├── remix.py               (NEW)      # Remix pipeline start/status/download/recent
│   │   ├── transcript.py          (NEW in #15)
│   │   └── tts.py                            # passes speed to ElevenLabs too
│   ├── services\
│   │   ├── bg_removal.py          (NEW)      # rembg / U²-Net frame-by-frame → VP9 alpha
│   │   ├── caption_aligner.py     (NEW)      # forced alignment: whisper timing + cleaned-text spelling
│   │   ├── caption_detector.py               # tighter defaults + drift-split for moving captions
│   │   ├── caption_overlays.py    (NEW)      # ASS overlay builder + ffmpeg+libass preview frame
│   │   ├── caption_templates.py   (NEW)      # JSON templates in data/caption_templates/
│   │   ├── commentator_overlay.py (NEW)      # ffmpeg composite (chroma OR AI alpha)
│   │   ├── commentators.py        (NEW)      # commentator preset store
│   │   ├── elevenlabs.py                     # added speed param
│   │   ├── font_manager.py        (NEW)      # system + user fonts
│   │   ├── silence_remover.py     (NEW in #15)
│   │   ├── speed_match.py         (NEW)      # setpts + minterpolate=blend for slow-mo
│   │   ├── transcript_cleaner.py             # _strip_meta_commentary post-processor (CRITICAL)
│   │   └── transcript_cleaner.py             # same file — also has parsers for SRT/VTT/JSON
│   └── workers\
│       ├── remix_pipeline.py      (NEW)      # the big orchestrator
│       └── utility_jobs.py                   # added handle_commentator_bg_remove
├── src\app\
│   ├── captions\page.tsx          (NEW)      # Caption Studio UI
│   ├── remix\page.tsx             (NEW)      # Remix Pipeline UI (~1500 lines)
│   ├── silence\page.tsx           (NEW in #15)
│   ├── transcript\page.tsx        (NEW in #15)
│   └── tts\page.tsx                          # added ElevenLabs speed slider
├── src\components\ui\slider.tsx              # ALWAYS-array fix (was single-number bug)
├── data\
│   ├── caption_templates\         (gitignored, auto-seeded from captioner.DEFAULT_PRESETS)
│   │   └── inter_blk_italic.json  (NEW — user's favorite)
│   ├── commentators\              (gitignored)
│   │   └── povestitor_cel_verde\
│   │       ├── meta.json
│   │       ├── video.mp4
│   │       ├── thumb.jpg
│   │       └── processed.webm     ← AI-processed alpha, ~2.3MB
│   └── fonts\                     (gitignored)
│       ├── Inter-Black.ttf
│       ├── Inter-BlackItalic.ttf
│       └── comicbd.ttf
├── docs\
│   ├── caption-templates.md       (NEW)
│   └── session-handover.md        (NEW — THIS FILE)
└── scripts\
    ├── test_aligner.py
    ├── test_autocap.py
    └── test_meta_strip.py
```

---

## 3. The Remix Pipeline — full stage breakdown

`server/workers/remix_pipeline.py::handle_remix_pipeline`. Progress mapped to a fixed 0..1 timeline:

| Stage | Range | Notes |
|---|---|---|
| 1. Download | 0.00–0.10 | yt-dlp via existing `services.downloader.download_video` |
| 2. Transcribe | 0.10–0.20 | faster-whisper `medium` (was `small`); writes raw transcript text |
| 3a. Erase ‖ | 0.20–0.65 | LaMa GPU inpaint OR ffmpeg blur. Auto-detect = OCR scan + drift-split |
| 3b. Audio chain ‖ | 0.20–0.55 | clean → TTS → desilence → loudnorm I=-16 LUFS + 50ms fade |
| 4. Speed-match | 0.65–0.75 | `setpts=PTS*factor`. When factor>1.15: append `minterpolate=mi_mode=blend` |
| 5. Caption burn | 0.75–0.92 (or 1.00 if no commentator) | libass + slow/crf18 |
| 6. Commentator overlay | 0.92–1.00 (optional) | ffmpeg chroma key OR alpha-composite |

`asyncio.gather` runs 3a and 3b concurrently. Each stage is wrapped in a `_Sliced` proxy that maps 0..1 local progress into the stage's window.

### Critical detail: meta-commentary stripping

`services/transcript_cleaner.py::_strip_meta_commentary` is **essential**. Small LLMs (specifically qwen2.5:7b in Ollama) ignore "Output ONLY cleaned text" and emit duplicate paragraphs separated by `---` plus epilogues like "Acesta este un text care respectă...". Without the post-processor:

- Cleaned text length: ~3000 chars (duplicated 3×)
- TTS reads all of it → 180s voice for a 75s video
- speed-match factor 2.4× → grotesque slow-mo

With the strip:
- Cleaned text: ~1400 chars
- Voice: 84s
- speed-match factor 1.10× → natural

Patterns it cuts:
- Markdown horizontal rules `---` (split there, keep only head)
- Headers / lead-ins: "iată textul", "acesta este un text", "pentru a fi mai natural", "respectă toate cerin"
- English equivalents: "here is the cleaned", "this version", etc.

### Critical detail: forced caption alignment

`services/caption_aligner.py::align_words` does:

1. Run faster-whisper on the TTS audio → word + timestamp list
2. Tokenize cleaned_text into a parallel word list
3. `difflib.SequenceMatcher` finds matching blocks
4. For matched words → use whisper's timestamp
5. For unmatched cleaned-text words → interpolate timestamp between neighbors
6. Output: word list with cleaned-text spelling + whisper timing

This solves "whisper occasionally mishears clean TTS" — we trust cleaned_text for spelling but whisper for *when*.

Used only when voice ≥ 20s (below that, even-distribution drift is negligible and we skip the 10–20s whisper round-trip).

---

## 4. Caption Studio (`/captions`)

Independent editor: upload a video, add overlays, get live ffmpeg+libass-rendered previews, burn-in export.

**Architecture:**

- Source upload → `session_id` (stored in `data/temp/caption_sessions/{id}/source.mp4`)
- Template store: `data/caption_templates/*.json`, auto-seeded from `captioner.DEFAULT_PRESETS` on first list, user can drop new .json files in
- Font store: `data/fonts/` (user uploads) + system fonts scanned via fontTools (~180 on Windows)
- Live preview: server renders single PNG via `caption_overlays.render_preview_frame` (debounced 300ms in frontend)
- Auto-caption from audio: whisper on the upload → group into N-word chunks → populate as editable overlays
- Burn-in: async `caption_burn` job → libass burn → mp4 download

**Important font selection nuance:**

For Inter, use `font_family: "Inter Black"` (NOT just `"Inter"`). libass's font matching uses fontconfig which expects exact family names, not weight overrides. The `italic: true` field (added in this session — `caption_overlays.py:_resolve_style`) is honored as `style.italic = bool(...)` in pysubs2.

---

## 5. Commentator overlay (`/remix` last stage)

A full-frame video composited over the captioned remix. Background made transparent via chroma key OR AI.

### Two background-removal modes

**Chroma key** (default, fast):
- `ffmpeg chromakey=color=#XXXXXX:similarity=N:blend=M` filter
- Tunable per-preset + per-run override + "Save to preset" button
- **Frontend preview** does the same keying client-side on the thumbnail via `<canvas>` so what the user sees matches the ffmpeg output (RGB distance approximation of YUV chroma key — close enough for solid green screens)

**AI** (one-time per preset, then cached):
- `services/bg_removal.py` runs rembg U²-Net frame-by-frame
- Output: `data/commentators/{id}/processed.webm` (VP9 with alpha)
- Subsequent composites use the webm directly (alpha already baked in)
- Triggered by `POST /api/commentators/{id}/process-ai` → enqueues `commentator_bg_remove` job
- ~87ms/frame on RTX 2080 Super GPU, ~970ms on CPU (11× speedup)

### Critical: VP9 alpha decoder

VP9 stores alpha as a tag `alpha_mode=1` on the stream, NOT in pix_fmt. ffmpeg's auto decoder strips the alpha. **Always force `-c:v libvpx-vp9` as input decoder** for the AI-processed webm:

```python
if use_ai:
    cmd += ["-c:v", "libvpx-vp9"]
cmd += ["-i", str(src_overlay), ...]
```

Without this, the composite shows transparent pixels as BLACK instead of letting the main video show through.

### Critical: CUDA library path

onnxruntime-gpu silently falls back to CPU if `libcublasLt.so.12` etc. aren't on `LD_LIBRARY_PATH`. The pip package installs them under `server/.venv/lib/python3.12/site-packages/nvidia/{cublas,cudnn,cuda_runtime,cufft,curand,cusolver,cusparse,nccl,nvjitlink}/lib`.

**`dev.sh` exports all 9 dirs** when starting the backend. If you start `uvicorn` manually you MUST set this yourself.

Watch for this signature in the log:
```
[E:onnxruntime] Failed to load library .../libonnxruntime_providers_cuda.so with error: libcublasLt.so.12: cannot open shared object file
```

If you see it → check `LD_LIBRARY_PATH`. Inference will be ~11× slower than expected.

### Filter chain (both modes)

```
[1:v]
  loop=loop=-1:size=32767:start=0      # loop overlay forever
  [optional chromakey=...]              # OR rely on baked alpha from AI webm
  scale=main_w:main_h                   # stretch to main dims exactly
  format=yuva420p                       # carry alpha through overlay filter
[ovl];
[0:v][ovl] overlay=0:0:shortest=1 [out]
```

- `shortest=1` → output ends at main's duration
- `-map 0:a?` → commentator's audio is dropped (intentional)
- Audio plane: TTS voice only

### Tested on povestitor_cel_verde

- Source: 71s mp4 with #AAD714 green screen
- Chroma key: `#AAD714`, similarity 0.10, blend 0.05
- AI mode: ~8min processing on GPU, 2.3MB processed.webm
- Both modes produce visually equivalent output for solid green screens
- AI gives cleaner edges + handles complex backgrounds (e.g. videos without green screen)

---

## 6. The big config / behavior changes

| Setting | Before | After | Why |
|---|---|---|---|
| `whisper_model` | `"small"` | `"medium"` | WER drops ~half on RO/accented speech; downstream LLM cleanup is much better |
| Caption burn encode | `veryfast -crf 20` | `slow -crf 18` | ~30s slower, visibly cleaner export |
| `caption_detector.sample_fps` | 3.0 | 5.0 | Catches transient text between samples |
| `caption_detector.min_conf` | 0.35 | 0.25 | Stylized fonts score lower; was filtering valid text |
| `caption_detector.padding_px` | 12 | 6 | Tighter bbox; inpaint dilates separately |
| `caption_detector.bleed_s` | 0.2 | 0.4 | Covers caption fade-in/out frames |
| `caption_detector` segment splits | union only | drift-split when bbox center moves >30% | Moving captions get multiple tight segments instead of one huge union |
| `_LAMA_BATCH` (inpaint) | 8 | 16 | Sweet spot on 8GB Turing; 32 thrashes VRAM |
| Speed-match interpolation | (none — frame dupes) | `minterpolate=mi_mode=blend` when factor>1.15 | Smooth motion-blur slow-mo instead of choppy dupes |
| ElevenLabs key verify | `GET /v1/user` (needs `user_read` scope) | `GET /v1/voices` (what the app actually uses) | Restricted-access keys with only `voices_read` + `text_to_speech` were being rejected |
| TTS post-process | (none) | loudnorm I=-16 LUFS + 50ms fade in/out | Volume consistency between runs |
| XTTS per-request char cap | hard 2000 (raised ValueError) | sentence-chunked up to 80k chars | User pasted long scripts (Grinch story narration ≈ 3000 chars) hit the cap; chunking + WAV concat removes the wall while keeping a sanity ceiling |
| Voice Studio textarea | `maxLength={2000}` | unlimited; counter shows `N chars` + chunk preview | Matches the new backend behavior |
| Commentator upload | always chroma keying | auto-detect native alpha (`alpha_mode=1` tag on VP9 webm, `pix_fmt=yuva*/bgra/rgba` on ProRes/QT) on upload | Lets the user pre-render in DaVinci/Premiere and upload a real-alpha file; ClipForge skips chromakey + uses the alpha directly. Frontend hides the AI block + chroma controls when `has_native_alpha=true` is in preset meta |

---

## 6b. XTTS long-input chunking (new — past the 2000-char wall)

`services/tts.py::synthesize` no longer rejects long input. Instead:

- **<= 1200 chars** → single `tts.tts_to_file()` call (unchanged path)
- **> 1200 chars** → `_split_into_tts_chunks()` splits on sentence boundaries `.!?…`, packs sentences into ≤1200-char chunks (hard-wraps a single oversized sentence as last resort), synthesises each into a temp WAV in `data/tts_out/.chunks_*/`, then `_concat_wavs()` uses the stdlib `wave` module to stream-concat (same params → no re-encode → no quality loss)
- Safety ceiling at **80000 chars** (~50 min audio) to catch accidental whole-book pastes

Constants live next to the function:
```python
XTTS_CHUNK_MAX_CHARS = 1200    # per-chunk cap
XTTS_HARD_MAX_CHARS  = 80000   # safety ceiling
```

Frontend (`src/app/tts/page.tsx`) reflects this — counter shows
`Single take` for short input, `Will be auto-split into N chunks and concatenated` for long input. No `maxLength` on the textarea.

ElevenLabs path is unchanged — the EL API enforces its own limits per model.

## 6c. Commentator: native alpha detection

`services/commentators.py::_probe_has_alpha` now runs on every upload and sets `meta.has_native_alpha = true` when the file already carries transparency. Detection cases:

- VP9 in WebM with `alpha_mode=1` tag (CapCut/OBS alpha exports)
- Any container with `pix_fmt` in `{yuva420p, yuva422p, yuva444p, bgra, rgba, argb, abgr}` (ProRes 4444, DNxHR HQX/4444, QuickTime Animation, HEVC with alpha)

In the overlay stage (`composite_commentator`) the precedence is now:
1. `processed.webm` exists (AI baked) → use that
2. else `meta.has_native_alpha=true` → use raw upload with alpha
3. else → chroma key

In all 3 cases, when alpha is the source we force `-c:v libvpx-vp9` for the input *if* it's a webm file (so the alpha plane survives decoding).

Frontend: when `com.has_native_alpha` is true, both the AI block and the chroma controls are hidden — replaced by a single emerald-badged "Native alpha channel detected" notice. Live preview still re-keys the thumbnail client-side using the saved chroma values, but those values are unused at burn time.

This was added because U²-Net produces a slight green halo on cartoon characters (the user's povestitor preset). For green-screened cartoons, **chroma key is actually better than AI** — the AI mode introduces edge artifacts that the simple `chromakey` filter doesn't, because U²-Net wasn't trained on caricature edges. Native alpha is the cleanest path of all three.

## 7. Known gotchas / things to be careful about

### Browser extension blocking cross-port fetches

Some Chromium extensions (Grammarly, screenshot/OCR tools) inject `content.js` that intercepts fetches and silently kills multipart POST + large binary responses. **Symptom:** UI shows "Uploading…" forever, backend log shows zero POST requests, DevTools Network shows `(pending)` indefinitely.

**Fix pattern (already applied to Caption Studio and Remix):** route everything through Next.js same-origin proxy `/worker-api/...` (see `next.config.ts` rewrite). Replaces `${WORKER_URL}/api/...` in code with `/worker-api/...`.

If a future page is built and gets stuck like this, this is almost certainly the cause.

### Slider component

`src/components/ui/slider.tsx` had a bug where single-value mode called `onValueChange(number)` while every page destructured `([v]) => ...`. Fixed: **always emits `number[]`** now. If you write a new page using the Slider, use `onValueChange={([v]) => ...}` consistently.

### Turbopack HMR misses changes occasionally

After non-trivial backend or shared-file edits, sometimes the browser keeps serving an old bundle. Symptoms: user reports the new feature isn't visible even after Ctrl+Shift+R. Fix:

```bash
./dev.sh stop
rm -rf .next
./dev.sh start
```

Then tell user to close + reopen the tab (full hard-refresh).

### Dark Reader extension

User had Dark Reader installed, which injects attributes into HTML elements. This causes React hydration mismatches AND can break event handlers (clicks don't fire). Solution: tell user to disable Dark Reader for localhost. The hydration warning text mentions `data-darkreader-*` attributes — that's the tell.

### ffprobe returns "N/A" for VP9 webm duration

The duration lives in the format header for VP9, not on the stream. Always wrap the float conversion in try/except:

```python
try:
    dur = float(out_meta.get("duration") or 0)
except (TypeError, ValueError):
    dur = 0.0
```

### Romanian translation quality from Ollama qwen2.5:7b

Translation quality is "okay but not great" — `vechiule` instead of more natural phrasings, etc. For better RO, use OpenAI or Anthropic via the engine picker. The user has accepted this as a tradeoff for free/local generation.

---

## 8. The Remix Pipeline UI (`/remix`) — section by section

This is a ~1500-line single page. Key sections in render order:

1. **URL input + Preview button** → calls `/api/remix/preview` → returns title, thumbnail_url, width, height, duration
2. **Dual-rect picker on thumbnail** (canvas overlay) — erase zone (red) + caption zone (amber). Active rect toggled by buttons. Drag to move, drag corner to resize.
3. **Live caption preview** (CSS-rendered) — sample text in the caption zone using the active template's font/color/scale/italic, updates instantly with any slider change.
4. **Live commentator preview** — chroma-keyed thumbnail via canvas, shown over the main thumbnail at full opacity (real RGBA composite, not fake opacity).
5. **Engine cards:** Erase engine (LaMa/NS/Blur) + Auto-detect toggle, Transcript cleaner, Target language, Voice engine, Voice, TTS language, Voice speed slider.
6. **Caption controls:** Words per chunk (1-6), Punctuation (Strip/Keep), Template grid (visual cards matching Caption Studio), Style overrides toggle (font/scale/color/uppercase/italic).
7. **Commentator picker:** preset cards + None card + "+ Add new" upload button. When a preset is selected, shows AI mode block (with "Process with AI" or "Remove" or running progress) + chroma key controls (color picker, similarity, blend, Save to preset).
8. **Run button** → submits to `/api/remix/start`. Then polls `/api/jobs/{id}` every 1.5s. Updates progress bar + 5 stage chips.
9. **Past runs panel** (always visible if there's data) — list of last 10 done jobs with native `<a download>` links.

The whole page proxies through `/worker-api/...` (no direct `localhost:8420` references).

---

## 9. The current state when the session ended

- PR #15 (`claude/silence-remover-and-improvements`) open, not merged
- PR #16 (`claude/remix-pipeline-and-commentator`) open, `MERGEABLE`, `CLEAN`
- Branch #16 is based on #15. When #15 merges to main, #16 will auto-narrow to just its commit
- Both servers running locally on user's machine (backend pid varies, port 8420; frontend port 3000)
- ElevenLabs Sarah voice + Ollama qwen2.5:7b + Inter Black Italic + Povestitor commentator preset are the "known working" combo
- Verified end-to-end pipeline run on https://tiktok.com/@hisytstory/video/7642931031720348941 → 9-10 min wall time → 1080×1920 mp4

---

## 10. Open follow-ups / things the user might bring up next

- **Re-upload of a commentator preset doesn't auto-invalidate `processed.webm`.** Currently if user uploads a new video.mp4 to an existing preset_id, the stale AI-processed webm sticks around. Should be deleted on re-upload. (Mentioned to user when explaining caching but not implemented.)
- **NVENC blocked** on user's driver 561.09 (needs 570+). If user updates driver, swap libx264 → h264_nvenc in inpaint.py + remix_pipeline.py for 5-10× encode speedup.
- **CapCut template importer** is documented in `docs/caption-templates.md` as a roadmap item but not implemented. User mentioned CapCut templates several times but accepted manual JSON port.
- **AI commentator processing UI feedback** — currently the progress text is just whatever the backend sends. Could be richer (ETA, sample frame preview).
- **Karaoke-style highlight** on captions (active word in different color) was discussed as a "TIER 2" improvement but not built. User said "incompatible with 1 word per chunk" — true, but could be re-introduced as a separate template type.
- **CapCut-style "spill suppression"** on chroma keying (separate ffmpeg `despill` filter) was offered but user pivoted to AI mode instead.

---

## 11. Conventions to match when continuing

- **Romanian replies, short and concrete.** Long explanations only when the user asks "why" or wants depth.
- **Commit format:** `feat(scope): description` or `fix(scope): description`.
- **Always check API keys not in commits** before pushing — `tts_config.json` etc. live in `data/` which is gitignored.
- **Always pass requests through `/worker-api/`** in frontend code, not `localhost:8420`.
- **Always use the Slider as `value={[x]}` + `onValueChange={([v]) => ...}`** — array form.
- **When tweaking the pipeline:** measure with real test run on the Grinch TikTok URL. The user expects ~10 minute wall time and a 1080×1920 mp4 with audio = TTS, captions burned, no green spill on commentator.

---

# ════════════════════════════════════════════════════════════════════════
# SESSION 2 — 2026-06-06 (continuation)
# ════════════════════════════════════════════════════════════════════════

> Everything below was done in the second working session. Branch:
> `claude/parallel-processing` (off main, ~9 commits). PRs #15/#16/#17 from
> session 1 are already MERGED into main.

## S2.0 — Environment changes since session 1
- **GPU was swapped twice** for benchmarking: RTX 2080 Super (8GB) ↔ RTX 3060
  (12GB). Both on driver 561.09 (NVENC still blocked → libx264). Final card:
  **RTX 2080 Super**. See `docs/gpu-benchmark.md` for the numbers.
- New Python deps installed in `server/.venv`: `google-api-python-client`,
  `google-auth`, `google-auth-oauthlib` (Drive). All added to requirements.txt.
- New user fonts in `data/fonts/` (gitignored): `Inter-Bold.ttf`,
  `Inter-BoldItalic.ttf` (instantiated from the official variable font),
  `BebasNeue-Regular.ttf` (Google Fonts OFL). Inter Black/BlackItalic already
  existed.
- Drive OAuth: user created an OAuth **Desktop** client →
  `data/drive_oauth_client.json`, then connected → `data/drive_oauth_token.json`
  (both gitignored). Connected account: vladoiustefan2005@gmail.com.

## S2.1 — Fix: remix transcribe crash on audio-less files
`workers/remix_pipeline.py::_stage_transcribe` lacked the audio-stream
pre-flight that `handle_transcribe` had → TikTok HEVC video-only downloads
crashed deep in PyAV with "tuple index out of range". Added `_has_audio_stream`
guard (later moved INTO remix_pipeline when pipeline.py was deleted — see S2.9).

## S2.2 — Video descriptions (PR #17, merged)
`services/descriptions.py` — final remix stage (0.95–1.00) producing TWO
descriptions: (1) source platform description translated to target lang,
(2) AI-generated from the transcript. Same engine as transcript cleaning.
Surfaced in `/remix/{job}/result` + copyable cards in the UI. metadata.py
description cap 500→4000 chars.

## S2.3 — Parallel Processing (the big feature)
One source link → **2–4 output videos**. Shared ONCE: download, transcribe,
erase, transcript-cleaning. Forked per variant: voice, captions, commentator.
- `workers/parallel_pipeline.py` — reuses remix stage functions. Variants run
  **sequentially** (heavy work already done). Per-variant dir `v0/`, `v1/`…
- `routers/parallel.py` — `/start`, `/{job}/result`, `/{job}/download/{i}`,
  `/recent`. New `JobType.parallel_pipeline`.
- `remix_pipeline.py` refactored: extracted `synth_voice_from_text()` +
  `_Sliced.sub()` so both pipelines share voice-gen + progress slicing.
- Frontend `src/app/parallel/page.tsx` + `src/components/parallel/`
  (`variant-card.tsx`, `zone-picker.tsx` — the zone picker is extracted to
  match the Remix one 1:1). Results labeled by variant name/commentator.
- Measured ~1.75× faster than N separate remixes.

## S2.4 — Variant presets
Save a voice+caption+commentator bundle, reload into any variant slot.
`services/variant_presets.py` + `routers/variant_presets.py`, stored as
`data/variant_presets/*.json`. UI: Load/Save/Delete row in each variant card.

## S2.5 — Google Drive OAuth upload (CRITICAL learning)
**Service accounts have 0 GB quota** → uploading to a personal My Drive fails
with `storageQuotaExceeded`, EVEN if the folder is shared. The only free fix
for a personal account is **3-legged user OAuth** (files owned by the user →
their 15 GB).
- `services/drive_oauth.py` — consent flow via a loopback server on port 8421;
  `/connect` returns an auth_url the UI opens, a background thread catches the
  redirect and saves the token. Repeated clicks tear down the stale server
  (fixed an "Invalid code verifier" PKCE bug).
- `services/drive_upload.py` — shared uploader: prefers user OAuth, falls back
  to service account (for Workspace Shared Drives). `clips.py` was refactored
  to use it (then clips.py was deleted in S2.9 — the helper lives on).
- `routers/drive_auth.py` — `/status`, `/connect`, `/disconnect`.
- Per-variant optional `drive_folder` auto-uploads the finished video; the
  Download button stays available either way. Status shown in Results.
- **Gotcha:** the Drive Python packages weren't installed in the venv at first
  → "credentials missing". Install them in the venv (already in requirements).

## S2.6 — Small fixes / perf
- `dev.sh`: whisper default `base`→`medium`; logs now **append** across
  restarts (`>` → `>>` + a "===== backend start =====" separator) so cross-run
  data (e.g. inpaint timing) survives.
- `fix(commentator)`: `VALID_POSITIONS` now includes `"fullscreen"` (the remix
  overlay is full-frame; uploads were rejected with 400).
- `perf(inpaint)`: VRAM-aware LaMa batch — 8GB cards stay 16, ≥12GB auto-raise
  to 24 (at model-load; env `CLIPFORGE_LAMA_BATCH` overrides). 8GB unchanged.
- `docs/gpu-benchmark.md` (new): erase/LaMa inpaint throughput, identical
  1243-frame clip — **RTX 2080 Super 53.9 ms/frame (18.6 fps)** vs **RTX 3060
  66.8 ms/frame (15.0 fps)**. The 2080 Super wins (more CUDA cores + bandwidth);
  the 3060's only edge is VRAM. NVENC needs driver 570+ (max driver for the
  2080 Super is the latest, e.g. 610.47 — Turing is still supported).

## S2.7 — Speed-match + caption burn FUSION (quality)
The pipeline used to re-encode the video 3× (erase crf22 → speed-match crf20 →
caption crf18), each pass losing detail. Now FUSED: `speed_match.compute_speed_plan()`
returns the filter without encoding; `_stage_match_and_caption` applies setpts +
subtitles + voice mux in ONE ffmpeg pass at **crf 16** (slow). Erase intermediate
bumped to **crf 16** (near-lossless). 3 encodes → 2, both near-lossless.
`match_video_to_voice` removed (orphaned).

## S2.8 — Caption Cloner (extract a template from a reference video)
`/captions` → "Clone from video" → upload a reference caption clip → get a
reusable ClipForge template.
- `services/caption_cloner.py` — OCR (locate) + pixel sampling (colours/outline
  via erosion ring) + italic detection (shear search, incl. synthetic italic
  for fonts like Bebas Neue with no italic face) + animation heuristic
  (word/phrase) + position/size/uppercase/words.
- `services/caption_font_match.py` — renders the reference word in every
  installed face, ranks by silhouette IoU (averaged over the LONGEST words for
  stability) minus a width/aspect penalty (so condensed refs prefer condensed
  faces). Style-filtered to the detected bold/italic.
- `src/components/captions/clone-from-video.tsx` — modal: reference crop beside
  a live preview, editable fields, font candidates, Save as template.
- **Honest limit:** font auto-pick is a *best guess + candidates* — silhouette
  matching can't reliably pick the EXACT typeface among similar ones, and the
  font must be INSTALLED to be matched. The user confirms/picks/uploads.
  Everything else (colour, italic, position, animation, uppercase) is reliable.

## S2.9 — Removed the entire legacy clip-based flow (big cleanup)
The old "video → AI-scored clips → editor → export → campaigns" pipeline is
gone (the app runs entirely through Remix / Parallel / the studios now).
- Deleted services: `scorer`, `exporter`, `reframer`, `categories`, `campaigns`.
- Deleted routers: `projects`, `clips`, `exports`, `campaigns` (+ unmounted).
- Deleted worker `pipeline.py` (`_has_audio_stream` moved into remix_pipeline).
- `utilities.py`: removed quick-download + batch (they used `full_pipeline`).
- Deleted frontend: `/`(dashboard), `projects/[id]`, `editor/[id]`, `campaigns`,
  `exports`, `utilities/batch`; components `dashboard/`, `ingestion/`, `clips/`.
- Dropped **mediapipe** dep (only `reframer` used it).
- Dead code removed: `match_video_to_voice`, remix legacy commentator fields,
  dev/bench scripts.
- `/` now redirects to `/remix`. `ProjectModel`/`ClipModel` tables KEPT (dormant,
  remix/parallel still create project rows).

## S2.10 — Nav: studios moved under the Utilities hub
Sidebar trimmed to **Remix Pipeline · Parallel Processing · Utilities ·
Settings**. Voice/Transcript/Caption studios + Silence Remover are now launched
from the Utilities page (routes unchanged: `/tts`, `/transcript`, `/captions`,
`/silence`).

## S2.11 — Caption auto-detect (eraser) rewrite
Old detector built segments only from OCR-sampled frames → frames OCR missed
were left un-erased. New approach: OCR only locates the **zone**; a per-frame
**edge-density** scan inside it decides exact presence on EVERY frame
(frame-accurate, no gaps). OCR detections are **anchored** in (a busy background
can't drop an OCR-confirmed frame → no regression). Zone is a **percentile
union** (drops over-wide OCR boxes) → smaller erase region. Tested: ~100%
temporal coverage, tighter box.

## S2 — Conventions reminder (still apply)
- Romanian replies, short + concrete. `/worker-api/` proxy in frontend, never
  localhost:8420. Slider as `value={[x]}` + `onValueChange={([v])=>…}`.
- Turbopack HMR misses changes after non-trivial edits → `./dev.sh stop &&
  rm -rf .next && ./dev.sh start`, then hard-refresh the tab. Hit this often.
- Files ≤500 lines (CLAUDE.md). Drive creds + fonts live in gitignored `data/`.
- Open follow-up the user raised: a full automation (Google Sheets link →
  ClipForge API → TikTok schedule + description). ClipForge is already an API
  and already outputs video+description+Drive upload; the bottleneck is TikTok
  posting (official Content Posting API needs app audit, or a 3rd-party
  scheduler). A "POST /api/auto" endpoint (link + preset + auto-detect, no UI)
  would make it scriptable — not built yet.

## S2.12 — Eraser auto-detect: ROI constraint (the real fix)
The per-frame rewrite (S2.11) fixed *missed frames* but regressed *where*: on a
busy/animated clip (text everywhere — product labels, signs, UI) OCR found text
at 5–93% of the height and the detector erased huge middle chunks. Root cause:
OCR can't tell the subtitle from scene text. Fix: `detect_caption_segments` now
takes a `roi` (the user's drawn erase rect) — only detections whose centre is
inside it are kept, and the final box is clipped to it. So auto-detect means
"find the caption *inside the region I marked*", never the whole frame.
`_stage_erase` (remix/parallel) and `handle_erase` (utility) pass the erase rect
as the ROI. Verified: every zone now lands in the marked band.
Also: the per-frame presence scan samples at ~20fps via `grab()/retrieve()`
(skips decode of skipped frames) — ~3× cheaper on 60fps clips, same accuracy.
NOTE: the remaining extra GPU/time is LaMa inpainting *more frames* now that
coverage is complete — inherent to "no missed frames", only mitigated by the
ROI keeping the zone small.

## S2.13 — Split into parts (multi-part posting)
Per-variant `split_into_parts` toggle (saved in presets). After a variant's
final video renders it can be cut into parts for posting as Part 1/2/3…
**Rule (`_split_plan` in parallel_pipeline.py):**
  - clip ≤ 1:30 → ONE part (max long part is 1:30);
  - else cut 1:00 parts; the leftover is a final SHORT part if ≥30s, else it
    folds into the last part (which then runs 1:01–1:29).
  - So the short part is never <30s and no part exceeds 1:30.
    1:40→[60,40]  1:41→[60,41]  2:30→[60,60,30]  2:41→[60,60,41]
Parts are re-encoded (libx264 crf18 medium) for exact cuts; downloadable
individually (`/{job}/download/{index}/part/{p}`); when a Drive folder is set
the PARTS are uploaded instead of the whole. Results UI shows Full + Part chips.

## S2 — Branch state at this point
`claude/parallel-processing` off main, ~11 commits (parallel + variant presets
+ Drive OAuth + descriptions + fusion + Caption Cloner + legacy-flow removal +
nav reorg + eraser ROI fix + split). Not yet pushed / no PR for this branch
(session-1 PRs #15/#16/#17 are merged; #18 was the earlier parallel PR).
`docs/session-handover.md` is mirrored to `SESSION-HANDOVER.md` at the repo root
for visibility.

---

# ════════════════════════════════════════════════════════════════════════
# SESSION 3 — 2026-06-07 (Sheets automation + Settings + GPU + stability)
# ════════════════════════════════════════════════════════════════════════

> Branch: still `claude/parallel-processing`. This session pushed it and
> opened **PR #21** (vs main). All work below is on top of PR #21. The
> branch now has ~15 extra commits beyond S2.

## S3.0 — Three user-requested features, then an audit + fixes

The session went: (1) custom ElevenLabs voice ID, (2) "Parallel from
Sheets" automation, (3) Settings page for keys + Drive, then a codebase
audit producing `docs/improvement-plan.md` (19 tasks T1–T19), then
executing the P0 batch (T1–T4). A long detour fixing `dev.sh` (the
backend wouldn't start) is documented in S3.7 — read it, it'll save you.

## S3.1 — Custom ElevenLabs voice ID (commit 9bd04d1)
The Voice dropdown only listed voices in the user's EL account
(`/v1/voices`). The public **Voice Library** (thousands of shared voices)
wasn't reachable. Added a "Custom voice ID" text field (visible only when
engine = elevenlabs) on BOTH `/parallel` (per-variant card) and `/remix`
(single voice). Paste a voice_id from elevenlabs.io and it's used directly
at TTS time (backend never validated voice_id — it just POSTs to
`/v1/text-to-speech/{id}`). The dropdown shows "Custom: XXXX…" when the
current id isn't in the account list; engine-change auto-pick preserves a
15+ alphanumeric custom id instead of clobbering it.
- Files: `src/components/parallel/variant-card.tsx`, `src/app/remix/page.tsx`.

## S3.2 — Parallel from Sheets (the big feature, commit 8b172eb)
New page `/parallel-sheets` — drives the SAME parallel pipeline but pulls
the source URL + a number from a Google Sheet, and writes the AI-generated
description back into the row when done.

**User decisions (locked in):**
  - Single-part filename → `<num>.mp4`; split → `<num>_p1.mp4`, `_p2.mp4`…
  - Description written = the **AI-generated** one (not source-translated)
  - At N variants, **only variant #0** writes to Sheets
  - If the row already has a description → **overwrite**

**Backend:**
  - `services/sheets.py` — Google Sheets API wrapper (read/write/batch +
    friendly errors; `SheetsScopeMissing` → 401 reconnect hint).
  - `services/sheets_config.py` — persists config + `next_row` in
    `data/sheets_config.json`. ONE config per install (columns constant).
  - `routers/sheets.py` — `/api/sheets/{config,pull-next,commit,skip-row}`.
    `pull-next` reads the next row but does NOT advance; `commit` writes the
    description and advances `next_row`.
  - `services/drive_oauth.py` — `SCOPES` gained
    `https://www.googleapis.com/auth/spreadsheets`. **The user had to
    re-consent** (Cloud Console → Google Auth Platform → Data Access → add
    the `spreadsheets` scope, then Disconnect+Connect Drive in the app).
  - `routers/parallel.py` — `StartRequest` accepts `sheets_row` +
    `sheets_number`; `/result` surfaces `sheets_commit`.
  - `workers/parallel_pipeline.py` — filename override when `sheets_number`
    set; after descriptions stage, auto-writes variant #0's AI description
    to the row + advances `next_row`. Failure is logged in
    `sheets_commit.status` but never fails the job.

**Frontend refactor (to stay under the 500-line rule):**
  - `components/parallel/parallel-processor.tsx` (NEW, 458 lines) — the body
    of `/parallel` extracted, reusable via `topContent` / `startPayloadExtras`
    / `onJobDone` props.
  - `components/parallel/drive-card.tsx` (NEW) — Drive connect card extracted.
  - `app/parallel/page.tsx` — now a thin 28-line wrapper.
  - `app/parallel-sheets/page.tsx` (NEW) — Sheets config form + Pull next /
    Skip buttons + "Row N · #num" badge + (added in C3) a persistent
    last-commit status indicator with a Retry button.
  - Sidebar: new "Parallel from Sheets" entry; fixed a prefix-match bug so
    `/parallel` doesn't light up while on `/parallel-sheets`.

**Headless API (commit 0f87d31, plan task F1):** `POST /api/auto`
(`routers/auto.py`) runs the whole parallel pipeline from one POST — either
an explicit `url` + `variant_preset_ids`, or `from_sheets: true` to pull the
next row. Default erase/caption zones from yt-dlp dims, auto-detect on.
Reuses `variant_presets.load_preset()` (new helper). The pipeline's
`len(variants) < 2` guard was relaxed to `< 1` so /auto can run a single
variant (the UI still enforces 2+ via its schema). This is the building
block for full Sheets→ClipForge→post automation.

## S3.3 — Settings page rewrite (commit 56f8935)
`/settings` was read-only system info. Now it manages everything needed to
run the app:
  - `components/settings/api-keys-card.tsx` — ElevenLabs / OpenAI /
    Anthropic key inputs (show/hide, Save & Verify → round-trips the
    provider, Clear, configured/not badges; EL shows tier + usage).
  - `components/settings/drive-setup-card.tsx` — two-step Drive setup:
    upload OAuth client JSON (`POST /api/drive-auth/client`, validates it's
    a Desktop client, 50KB cap) then Connect (popup OAuth, reminds the
    consent must include the Sheets scope). Reset wipes both files.
  - `components/settings/whisper-card.tsx` — see S3.4.
  - `routers/drive_auth.py` — new `POST/DELETE /api/drive-auth/client`.

## S3.4 — Whisper device/model UI + silent-CPU-fallback fix (commit ff2369a)
Whisper runs in a spawned SUBPROCESS (killable), whose logger never reached
backend.log — so nobody could tell if faster-whisper actually used CUDA or
had silently fallen back to CPU. Added:
  - `services/transcriber.py`: `_model_info` snapshot (configured vs actual
    device/model, `fell_back_to_cpu` flag), `get_model_info()`,
    `unload_model()`, and `data/whisper_config.json` overrides (layered on
    top of `CLIPFORGE_WHISPER_*` env vars). `_get_model()` records what it
    actually loaded.
  - `routers/transcript.py`: `GET /api/transcript/device` (with
    `?verify=true` it force-loads to confirm), `POST /api/transcript/device`
    (saves model+device, drops the cached model).
  - `components/settings/whisper-card.tsx`: configured-vs-actual side by
    side, model picker (tiny→large-v3), device picker (auto/cuda/cpu),
    Apply + "Verify GPU".
**CONFIRMED on the user's box:** RTX 2080 Super runs **large-v3 on cuda**
(actual device = cuda, ~3GB VRAM, ready). User switched to large-v3 for
best RO accuracy. First load was ~62s (model download); cached after.
NOTE: Whisper (~3GB) isn't unloaded before the erase stage's LaMa (~2-3GB)
— both resident is still <8GB so it's fine, but plan task T12 (free VRAM
between stages) would add headroom if an OOM ever shows up.

## S3.5 — Priority fixes from the first audit (commit da36cdb)
Seven items found by reviewing the codebase, all verified:
  - **A1**: `/remix` had an orphan `setCommentatorChroma("")` (setter removed
    in S2.9) → replaced with `setChromaColor(null)`. Caught by tsc.
  - **A2**: `Slider` (`components/ui/slider.tsx`) was missing a `disabled`
    prop that 7 call sites passed → added it properly (both range inputs +
    dimmed container). Fixed the last 8 pre-existing TS errors → repo is now
    **0 TypeScript errors**.
  - **A3**: `services/sheets.py` scope check read `creds.scopes` which
    google-auth echoes from the constructor arg (always "present") → now
    reads the real granted scopes from `drive_oauth_token.json`.
  - **C3**: Sheets commit failures were silent (token expired mid-run →
    `sheets_commit.status=failed` but nothing told the user). Now a
    destructive toast + a persistent indicator on the Sheets card + a Retry
    button (re-commits the cached description without re-running the
    pipeline).
  - **E1**: `POST /api/drive-auth/client` capped at 50KB (real OAuth client
    JSONs are <2KB).

## S3.6 — Codebase cleanup + remix split (commits 72abefc, 9cd27f3)
  - **A4**: pruned dead legacy-flow code. `src/lib/api.ts` 210→25 lines,
    `src/types/index.ts` 209→17, deleted `src/lib/stores/project-store.ts`
    (zero imports). ~454 lines of dead code gone. `api.system` now goes
    through `/worker-api/` (was the last `localhost:8420` bypass).
  - **B (partial)**: `/remix/page.tsx` 1864→1477 by extracting
    `components/remix/past-runs.tsx` (100) + `components/remix/commentator-picker.tsx`
    (425). Still over 500 — plan task T14 lists the remaining extracts
    (voice card, caption card, zone picker).

## S3.7 — dev.sh saga (CRITICAL — read before touching dev.sh)
The backend wouldn't start via `./dev.sh start` and it took several
iterations to find why. THREE stacked bugs:
  1. **`setsid` is missing on this WSL.** dev.sh launched the backend with
     `setsid bash -c ...` which failed ("setsid: command not found"), wrote
     the start marker, but never ran uvicorn. The backend that "worked"
     earlier was always one the user started manually. Fix: a
     `$_DETACH_CMD` that uses `setsid` if present else `nohup`, backgrounding
     DIRECTLY (NOT via a `$()` subshell — that was an intermediate broken
     attempt; backgrounding inside command-substitution doesn't detach
     reliably). Commits 78600be, 1e53be2.
  3. **`--reload` breaks on /mnt/f.** The project lives on `/mnt/f` (a
     Windows drive in WSL2) where inotify file-watching doesn't work on the
     9p mount, so uvicorn's `--reload` reloader hung at startup and the port
     never bound. Reload is now **OPT-IN via `CLIPFORGE_RELOAD=1`** (default
     OFF), and when on it forces `WATCHFILES_FORCE_POLLING=true`. The
     bind-wait timeout was bumped 20s→45s (torch/whisper imports are slow on
     a cold cache). Commit 4eda229.
**Consequence:** with reload OFF, backend changes do NOT hot-reload — run
`./dev.sh restart backend` after editing `.py`. Don't "fix" this by adding
`--reload` back unless the project moves to native Linux fs (`~/ClipForge`).
Also note `setsid` missing means `kill_group` degrades to killing just the
leader; `stop_one`'s `fuser -k <port>/tcp` fallback (added in D1) catches
stragglers.

## S3.8 — Improvement plan + P0 execution (commits 817e0f5, c2f567f, 77697cf, 26cd37e)
Wrote `docs/improvement-plan.md` — 19 self-contained tasks (T1–T19) graded
P0/P1/P2, each with goal/files/steps/code-snippets/acceptance/commit-msg,
specified so a weaker model can execute them. A status board at the bottom
tracks progress. Then executed the **P0 batch**:
  - **T1** (817e0f5): `timeout=` on every `subprocess.run` across 13 files
    (60s probes / 120s previews / 300–600s extracts / 1800s encodes), plus
    a 1h wall-clock cap + bounded `.wait()` on the inpaint Popen pair. An
    AST scan confirmed zero un-timed calls remain.
  - **T2** (c2f567f): `services/retry.py` — `with_retry()` exponential
    backoff. Wrapped ElevenLabs synth+voices and OpenAI/Anthropic/Ollama
    chat calls. 4xx never retried; 5xx/429/conn-errors retried (4 attempts,
    Ollama 2). Error-message formats preserved for the routers.
  - **T3** (77697cf): `services/cleanup.py::cleanup_job_workspace` removes
    `data/media/<project_id>` on cancel + fail (wired into `cancel_job` /
    `fail_job`). `complete_job` does NOT clean (keeps the finished video).
  - **T4** (26cd37e): `inpaint_region` polls an `is_cancelled` callback each
    frame, kills ffmpeg + raises `JobCancelledError` on cancel. Threaded
    through `_stage_erase` in both pipelines. The erase stage is the long
    one (5–40 min) so that's where mid-run cancel matters; the shorter
    encode passes stay timeout-bounded (Popen+drain there risks a deadlock
    for little gain).

## S3.9 — P1 + P2 execution (the rest of the plan, one sitting)
Continued straight through the plan. 17 of 19 tasks executed; T6 deferred,
T15 moot. Commits:
  - **T5 + T7** (77ffcd1): `recover_stuck_jobs` now requeues ALL running
    jobs on startup (no 30-min threshold; 50-job sanity cap, terminal-project
    jobs still failed). `JobQueue.stop()` annotates in-flight jobs
    "Interrupted by backend shutdown" then cancels with a 5s grace wait.
  - **T8** (5f20900): `routers/jobs.py` `list_jobs` gains a `status` filter
    (comma list ok). New `components/layout/running-jobs-badge.tsx` polls
    `/api/jobs?status=queued,running` every 3s and shows a sidebar badge from
    any page, links by job type.
  - **T9** (084a714): `/api/remix/recent` gains `offset` + `total`; new
    `DELETE /api/remix/{job_id}` removes media + DB row. Past-runs panel
    paginates (10/page) + per-run delete with confirm.
  - **T10** (a0b82fa): drive-setup-card Connect uses ONE polling loop (was
    two racing intervals).
  - **T12** (a0b82fa): `services/gpu_utils.py` (free_gpu_memory +
    unload_inpaint_model). `_stage_erase` drops LaMa (~2-3GB) after inpaint.
    Whisper needs no cleanup — it's in a spawned subprocess (OS frees on exit).
  - **T11** (552aa05): `lib/toast-helpers.ts` (errorToast.api / okToast).
    Adopted in parallel-sheets; rest left for incremental adoption (no mass
    rewrite — pure cosmetic churn).
  - **T13** (2e06638): new SSE `GET /api/jobs/{id}/stream`. parallel-processor
    uses EventSource with a polling fallback. /remix still polls (1477-line
    page not worth touching this pass).
  - **T16** (f2ab862): `services/secret_storage.py` — XOR-obfuscate API keys
    at rest (machine-tied, "enc:" prefix). NOT real crypto; documented.
    Wired into EL/OpenAI/Anthropic get/set + a one-shot startup migration.
    **VERIFIED LIVE:** both keys encrypted on disk after restart, and they
    still decrypt + authenticate (EL configured=True, OpenAI ready=True).
  - **T17** (be6e522): `server/tests/` — 12 httpx-ASGITransport smoke tests +
    `requirements-dev.txt` + `pytest.ini` + `scripts/run-tests.sh`. Run them
    in WSL: `./scripts/run-tests.sh` (pytest is a Linux binary — can't run
    from a Windows git-bash).
  - **T18** (9e5837b): `.githooks/pre-commit` runs `tsc --noEmit` on staged
    .ts/.tsx (dependency-free, no husky). Activated via `core.hooksPath`
    (package.json `prepare` sets it). Already fired on the T13 commit.
  - **T19** (cf5d0db): `docs/api.md` — integrator reference for /auto,
    /sheets, /drive-auth, /transcript/device, presets, jobs (incl. SSE),
    remix. README links it.
  - **T14** (7d46034): split `captioner.py` 877 → `captioner.py` (334) +
    `captioner_presets.py` (177, data + hex_to_ass_color) +
    `captioner_events.py` (416, ASS builders). Re-exports keep
    `from services.captioner import DEFAULT_PRESETS/hex_to_ass_color`
    working. Zero dangling refs (verified both directions).

### Deferred / not done (be honest with the next session)
  - **T6** (unify TTS/Transcript jobs under JobQueue): NOT executed. It
    rewires two WORKING features (TTS Studio, Transcript Studio), removes
    their `_jobs` dicts, changes how the routers enqueue. Modest value
    (short standalone jobs surviving restart) vs high risk of breaking
    working features done blind. Left for a session that can exercise both
    studios end-to-end.
  - **T15**: moot — /remix has no Drive UI (single-output, no Drive upload).
  - **T14 remainder**: `remix_pipeline.py` (886) + the frontend tts/captions/
    remix pages stay over 500. Splitting live pipeline/render code blind
    (no runtime test here) wasn't worth the risk; documented in the plan.

### Live test results (S3.9 verification, backend restarted with new code)
Tested via curl against the running backend (couldn't run pytest from
Windows git-bash — Linux venv):
  - T8 status filter: `?status=done` → 89 jobs, all status=done. ✓
  - T9 pagination: total=29, offset/limit correct. ✓
  - T13 SSE: emits `data:{…done}` then closes. ✓
  - T16: both keys `enc:`-prefixed on disk; migration logged
    ("encrypted 1 plaintext key in tts_config.json" + transcript_config);
    EL configured=True + OpenAI ready=True (decrypt+auth still works). ✓
  - F1 /auto: missing url → 400, bad preset → 404. ✓
  - T7: "Job queue processor stopped" in shutdown log. ✓
  - Static: 0 TS errors, all Python compiles, zero un-timed subprocess.run,
    zero dangling captioner refs, pre-commit hook fired.
  NOT runtime-tested (need a heavy/forced run): T1 timeout trigger, T2
  retry trigger, T3/T4 cancel cleanup+propagation (need a real ~10-min
  pipeline + Cancel mid-erase), T5 (no stuck jobs existed at restart).

## S3 — Branch / PR state at session end
`claude/parallel-processing`, **PR #21 open vs main**. P0 (T1–T4) + most of
P1/P2 done + pushed; plan status board current. **Only T6 deferred** (see
above). Repo is at 0 TypeScript errors; backend verified live after restart.
The user's working combo: large-v3/cuda Whisper + ElevenLabs (scoped key,
`/v1/user` 401 is expected/harmless) + Ollama qwen2.5:7b + Inter Black
Italic + Povestitor commentator + Sheets automation live.

## S3 — Conventions reminder (unchanged, still apply)
- Romanian replies, short + concrete. `/worker-api/` proxy in frontend.
  Slider as `value={[x]}` + `onValueChange={([v])=>…}`. Files ≤500 lines.
- Backend changes need `./dev.sh restart backend` (no hot-reload on /mnt/f).
- After non-trivial frontend edits, if Turbopack serves a stale bundle:
  `./dev.sh stop && rm -rf .next && ./dev.sh start`, then close+reopen the
  tab (a ChunkLoadError on /settings was exactly this).
- Commits: `feat/fix/refactor/perf/chore/docs(scope): description`.

---

# ════════════════════════════════════════════════════════════════════════
# SESSION 4 — 2026-06-08 (T20 eraser: tight masks + auto-localize, REAL-TESTED)
# ════════════════════════════════════════════════════════════════════════

> Branch still `claude/parallel-processing`, PR #21. This session designed
> AND implemented AND real-tested the caption-eraser rewrite (plan §11 / T20).

## S4.0 — Key unlock: I CAN run the Linux venv from the Windows agent
`wsl.exe -e bash -c "cd /mnt/f/ClipForge && server/.venv/bin/python ..."`
works from the agent's git-bash. So I can run cv2/easyocr/pytest/the real
pipeline pieces — not just static checks. This is how T20 got real-tested.
The running backend is also reachable on localhost:8420 for curl tests.
GOTCHA: EasyOCR + LaMa both want the 8GB GPU; to run a heavy test, stop the
backend first (`./dev.sh stop`) to free the GPU, then `./dev.sh start` after.

## S4.1 — T20 eraser, the design (plan §11, fully written there)
The user wanted the eraser to: (1) erase MINIMALLY (per-glyph, not a band
rectangle — clarity), (2) need NO manual box (full automation), (3) leave
NO caption frames after erase. Design:
- **Tight per-display masks**: one segment per "display" (held-still text,
  split at text changes via difflib similarity). One tight mask per display,
  reused across its hold → tight AND complete at once.
- **Glyph vs box** (§11.3d): box style (solid bg behind text) → mask the
  whole box; else mask the glyphs. Box detected when the interior is uniform
  AND differs from the video just outside the bbox.
- **Auto-localize** (no box): held-still filter (captions hold, scene text
  moves) + Y-lane clustering + transcript-speech correlation (ClipForge has
  the speech timing — a generic eraser can't use this).
- **inpaint change**: `_build_segment_state` rasterizes an arbitrary
  `seg["mask"]` instead of filling a rectangle.

## S4.2 — T20 implementation (commits)
- 75a10fa — Step A: `erase_coverage` field (tight|band|thorough) on
  remix/parallel/auto; `_stage_erase` Thorough mode = full-band inpaint
  (guaranteed-no-leak fallback).
- f99574a — Step B: inpaint accepts arbitrary per-segment `mask`.
- 8492627 — Step C: `detect_caption_displays()` — per-display tight
  glyph/box masks.
- 63ef50a — Step D: `auto_locate_caption_band()` — no manual box; threads
  `speech_intervals` from the transcribe result via `_speech_intervals_from_tx`.
- 60e7844 — Step E + F: fade-boundary expansion + `scripts/verify_eraser.py`
  (re-OCRs the OUTPUT band; any text = a leak).
- b4302c0 — fix: box-vs-glyph must compare interior to the SURROUNDING video
  (caught by a synthetic unit test — glyph on uniform bg was wrongly called a
  box and over-erased). Also fixed the jobs status-filter smoke test (307).

## S4.3 — REAL end-to-end test caught + fixed 2 leak bugs (commit 6660682)
Ran the full chain on a 20s TikTok clip (auto-localize → tight detect →
LaMa inpaint → re-OCR the output). Harness reported **45 leaks** first. Two
real bugs, both fixed, then **45 → 0 caption leaks**:
1. **Index-seek grabbed the wrong frame.** `cap.set(POS_FRAMES, idx)` on
   long-GOP mp4 lands on the nearest keyframe → the mask was built from the
   wrong text. Fix: Pass 3 decodes the video ONCE sequentially and builds the
   mask the moment it reaches the exact best frame.
2. **Otsu left the outline as a ghost.** Captions are bright FILL + dark
   OUTLINE; Otsu masks one polarity, so inpaint erased the white fill and
   left a readable dark outline ("teacher" still legible). Fix:
   **local-contrast mask** = `|gray - heavy_blur(gray)| > thr`, high for BOTH
   fill and outline, low for smooth background → covers the whole glyph+
   outline, tight, on any background. Verified visually (mask hugs fill+
   outline). Coverage ~24% of the band (vs 100% for a rectangle) — still
   minimal. The one remaining OCR hit was the character's SHIRT LOGO (scene
   text), correctly NOT erased.

## S4.4 — Verified facts about the test
- `auto_locate_caption_band` found the band with NO box: {268,1374,547,139}.
- 62 displays on the 20s clip (word-by-word caption).
- 12/12 smoke tests pass via `wsl … pytest tests/`.
- Cost: detect ~30s + inpaint ~32s for 20s of 1080×1920 on the 2080 Super
  (GPU, backend stopped). Word-by-word makes many displays → many sequential
  decodes in Pass 3 (works; a future opt could cache frames).

## S4.5 — KNOWN open issue at session end (the user's "91.mp4")
The user reports that on a specific clip (`91.mp4`) there are STILL
un-erased fragments ("relics"). T20's 0-leak was proven on ONE 20s clip;
91.mp4 is a different/harder case. The likely culprits to investigate (in
order): (a) word-by-word DISPLAY boundaries — if a word appears between the
5fps OCR samples its display window may miss it; (b) the local-contrast
threshold (GLYPH_LOCAL_THR=28) under-segmenting thin/anti-aliased outlines;
(c) auto-localized band too tight vertically, clipping ascenders/descenders;
(d) the BOUND_EXPAND_S clamp leaving a sub-frame gap between adjacent
displays at fast word changes. Use `scripts/verify_eraser.py` on the 91.mp4
output to locate the exact leak frames, then dump those frames + the active
segment's mask (as in S4.3 diagnostics) to see WHICH cause it is.

## S4 — Conventions reminder (unchanged)
- Run heavy Python tests via `wsl.exe -e bash -c "… server/.venv/bin/python …"`;
  stop the backend first to free the GPU. Romanian replies, short + concrete.

---

# ════════════════════════════════════════════════════════════════════════
# SESSION 5 — 2026-06-10 (anti-relic: transcript checklist + coverage audit)
# ════════════════════════════════════════════════════════════════════════

> Branch still `claude/parallel-processing`. Goal: kill the remaining
> eraser relics (the S4.5 "91.mp4" class of leaks) while KEEPING the tight
> per-word masks the user likes.

## S5.0 — User request + accepted suggestion
User: relics still appear from old captions; wants (1) zero relics,
(2) detection as good as possible, (3) erase area as SMALL as possible
(likes the per-word tight mask). User's suggestion (ACCEPTED — it closes
the main hole): whisper's transcript is in the video's ORIGINAL language
(already true — cleaning/translation happens later); use its word-level
timestamps as a CHECKLIST — tick every spoken word off against the OCR'd
displays, so we KNOW every caption word was seen and erased. An unticked
word = OCR never saw that caption → guaranteed future relic → cover it.

## S5.1 — Design (implementing now)
Root causes from S4.5 mapped to fixes:
 a. (display misses words) `detect_caption_displays` Pass 3 now builds each
    display's mask from FIRST + BEST + LAST sample frames (each frame's own
    boxes), OR-ed together — covers word-by-word "growing" captions where
    the best frame lacks early/late words. Was: single best frame.
 b. (checklist needs text) each display segment now carries `ocr_text`
    (token union across its samples).
 c. (band clips ascenders/descenders) `auto_locate_caption_band` vertical
    pad is now proportional to the median detection height, not fixed 10px.
 d. NEW `services/caption_audit.py::audit_caption_coverage()`:
    - tick transcript words (diacritics-stripped fuzzy match) against
      displays overlapping the word's time ±0.75s;
    - unticked word OVER a display → display marked SUSPECT → its mask's
      line-strips get expanded to full band WIDTH (height stays tight);
    - unticked word with NO display → presence-gated fallback BAND segment
      (plain rect, no mask — inpaint rasterizes rects natively);
    - presence gate = edge-density in the band, calibrated against the
      density measured during CONFIRMED displays, so a video with no
      caption at that moment doesn't get a spurious erase;
    - strong presence with no display also emits a fallback (catches
      non-speech captions, e.g. sound-effect text).
 e. `_stage_erase` wiring: new `transcript_words` param fed by
    `_transcript_words_from_tx(tx_result)` from BOTH pipelines; audit runs
    after detect, expands suspect masks, appends fallback segments.

## S5.2 — Implementation status (live)
All of S5.1 is implemented (not yet committed at the time of this entry):
- `server/services/caption_audit.py` (NEW, ~250 lines) — `_norm()` strips
  diacritics/punct (EN-model OCR mangles RO diacritics; whisper emits
  them); `_ticked()` fuzzy containment or SequenceMatcher ≥ 0.72;
  `_scan_band_density()` 10fps grab/retrieve Canny edge-density;
  `_widen_suspect_mask()` row-projection → fill rows across band width;
  `audit_caption_coverage()` orchestrates. Presence thresholds are
  CALIBRATED: conf_dens = median density during confirmed displays,
  idle = p20 of uncovered samples; thr_word = idle+0.35×(conf−idle);
  thr_strong = max(thr_word, 0.85×conf). Fallback segs are plain rects
  (no mask) — inpaint's `_build_segment_state` rasterizes rects natively.
  Overlaps between fallback and display segs are safe: `_find_active_segment`
  picks the first (earlier-start) seg; either way the area is covered.
- `caption_detector.py`: Pass 3 builds per-display mask from FIRST+BEST+
  LAST sample frames OR-ed (was best only); output segs carry `ocr_text`
  (token union across samples); `auto_locate_caption_band` vertical pad
  now max(10, 0.45×median_text_height).
- `remix_pipeline.py`: `_transcript_words_from_tx()` helper; `_stage_erase`
  gains `transcript_words=`; audit call after detect (tight path only),
  wrapped in try/except (audit failure never kills the job). Both
  call sites (remix + parallel) pass the words.
- `server/scripts/test_s5_eraser.py` (NEW harness): full chain on a real
  clip + baseline compare vs an old erase output. Found the user's 91.mp4
  source at `data/media/906d89d5639f/video.mp4` (24.7s) with its old
  `video_erased.mp4` for the baseline.
- NOTE: utility-page eraser (`handle_erase`) does NOT get the audit (no
  transcript there) — pipeline-only by design.

## S5.3 — Standalone real test on the 91.mp4 source (user asked: no pipeline)
Run 1 (results, then caveats): src=`data/media/906d89d5639f/video.mp4`
(24.7s, EN). medium/cuda transcribe 12s → 68 words; auto-band
{166,1415,747,181}; 28 displays; audit: 0 suspects, 0 fallbacks (OCR saw
every word — checklist clean); inpaint 59s; re-OCR of band: **NEW = 0
leaks, OLD baseline = 0 leaks too**.
- Baseline 0 means the user's 91.mp4 relics are NOT OCR-detectable in this
  band of the old erase → they're either outside the band, faint ghosts
  that pass OCR, or in the other project (5f626d60c353). Run 2 adds a
  FULL-FRAME scan of old+new and eyeball frame dumps to find them.
- GOTCHA (cost me the run): writing test output to /tmp in WSL — the
  distro auto-shuts down between wsl.exe invocations and /tmp is WIPED.
  Write test artifacts to /mnt/f (now `data/temp/s5_test/`).
- Run 1's log had h264 "Invalid NAL unit size" decode errors during the
  verify scan — couldn't re-check the file (wiped). Run 2 counts decoded
  frames src vs out to prove encode integrity.

Run 2 (artifacts in `data/temp/s5_test/`): encode integrity PROVEN
(src=740 out=740 frames — the NAL stderr noise is just ffmpeg/OpenCV
decoder chatter, file is fine). Band re-OCR: old=0, new=0 leaks. The
full-frame "leaks" at t=13.2–14.0s are a THERMOMETER PROP in the scene
("TEMPERATURE 212°F") — legit scene text in BOTH old and new, correctly
NOT erased (eyeballed `leak_new_full_000396.png`). Midpoint eyeball frames
are visually clean (no ghost outlines).

Remaining hypothesis for the user's relics: the 5fps verify scan can MISS
relics that flash 2–3 frames at display transitions. Launched
`server/scripts/s5_scan_every_frame.py` (NEW) — OCR of the band on EVERY
frame (740/video) over: new erased, old erased (906d89d5639f), old erased
(5f626d60c353). Results pending below.

## S5.4 — ROOT CAUSE FOUND (every-frame scan) + the real fix
The every-frame scan nailed it: **21 leaks (new) vs 22 (old)** — all
2–4-frame FLASHES at display TRANSITIONS ('SCO' f9-11, 'into …' f302-305,
'which muobed it' f359, 'er' f542-545, …). The S4 "0 leaks" was an
artifact of verifying at 5fps — the flashes live BETWEEN the samples.

Mechanism (proven): Step E extended display i forward to display i+1's
start and i+1 backward to i's end → the extensions OVERLAP in the gap, and
`_find_active_segment` picks the FIRST (= i) → the frames right after a
text switch get erased with the OLD text's mask while the NEW text is
already on screen → flash relic. The S4.5 candidate (d) was the right one.

Fixes implemented (caption_detector.py + caption_audit.py):
 1. **Bridge segments** (`_bridge_segment`): displays keep their sampled
    bounds; each inter-display gap ≤ max(0.45s, 2.2×sample_period) gets a
    bridge whose mask = band-WIDE strip over BOTH neighbours' text rows
    (+30% row dilation for pop-in animations). Whichever text is on screen
    during the uncertain frames — covered. Clip head: first display starts
    1.5 sample periods earlier; tail similar. Large gaps get a NON-
    overlapping ±0.30s expansion (capped at gap/2).
 2. **OCR probes in the audit**: uncovered windows (complement of all
    display+bridge windows) get 1–3 direct OCR probes each (band crop, one
    shared decode pass with the density scan). Any readable text → fallback
    band segment around the hit. Replaces threshold-guessing with direct
    evidence; the 10fps presence runs still catch flashes between probes
    (word-hit OR strong-density gated, as before).

## S5.5 — FINAL RESULT: 0 leaks on EVERY frame (proven)
Run 3 on the 91.mp4 source (`data/temp/s5_test/run3.log`, `everyframe3.log`):
- 28 displays + **27 bridges = 55 segments**; audit: 0 suspects, 0 fallbacks
  (checklist + probes clean — detection itself was complete this time).
- inpaint 74s (was 59s — bridges add ~25%); encode integrity 740/740.
- **EVERY-FRAME re-OCR of the band: 740 frames, 0 leaks** (was 21 new / 22
  old before the bridge fix). Eyeballed the previously-leaking frames
  (f9/f303/f359/f543) — visually spotless, no ghosts.
- Full-frame hits = only the THERMOMETER prop ("TEMPERATURE 212°F"),
  legit scene text correctly preserved.
- 12/12 smoke tests pass after the changes.
Verification protocol for the future: ALWAYS verify with the every-frame
scan (`server/scripts/s5_scan_every_frame.py X Y W H VIDEO`) — the 5fps
scan hides transition flashes (that's how S4 wrongly concluded "0 leaks").

## S5.6 — LaMa degenerate-patch repair (user-reported black squares)
User spotted two small BLACK SQUARES at ~t=6.7s in the erased output
(dome-over-fire scene). Diagnosed (read-only, frames dumped to
`data/temp/s5_test/dome_*`): source frame had caption "the salt"; the text
IS fully erased — the squares are LaMa HALLUCINATION artifacts inside the
masked word areas, on the bright/saturated fire background. Neighbouring
frames (f195/f210, same mask+model) are clean → per-frame instability.
NOT an fp16 issue — fp16 is opt-in (`CLIPFORGE_LAMA_FP16`, default off);
I initially mis-attributed it and corrected myself to the user. This also
explains run 2's phantom 'IIDI' OCR leak at f250 (OCR read the squares).

Fix (user picked option 1, `services/inpaint.py`):
- `_patch_degenerate(patch, mask_roi)` — a connected blob ≥64px inside the
  mask that is ≥75 gray-levels DARKER than the ring of unmasked pixels
  around the mask = hallucinated garbage. Dark fills next to dark
  surroundings never trigger (ring is dark too).
- Hook in `_flush_lama_batch`: degenerate patch → redo that frame's mask
  with `cv2.inpaint` telea (diffusion — cannot hallucinate), counted and
  logged at "Inpaint done". ~1ms/frame check on caption ROIs.
PR #21 turned out MERGED (before S4!) — the whole T20/S4+S5 eraser batch
is only on `claude/parallel-processing`. Plan: push branch + open a NEW
PR with all eraser commits.

Validation run 4 (WITH repair): **143 degenerate LaMa patches repaired**
(the fire scene triggered LaMa's failure mode on many frames, not just
the 2 the user saw); every-frame re-OCR still **740 frames / 0 leaks**;
encode 740/740; eyeballed f200 — black squares GONE, fire continues
naturally (telea smear invisible); f180 clean. 12/12 smoke tests pass.
143/~445 inpainted frames repaired = the detector is deliberately eager;
the cost is telea (smooth diffusion) instead of LaMa on those frames —
safe trade, hallucination is worse than smear.

Committed 0f81e4f, pushed, and opened **PR #23**
(https://github.com/brinza69/ClipForge/pull/23) — contains the WHOLE
unmerged delta vs main: the S4+S5 eraser overhaul AND the late-S3
hardening tail (T8+ — SSE, smoke tests, secret storage, captioner split…)
because PR #21 was merged on 2026-06-07 BEFORE those were pushed.

## S5.7 — Zone-picker preview fix (YouTube Shorts thumbs)
User reported the picker preview "isn't fullscreen anymore". NOT a code
regression — first time a YOUTUBE Shorts URL was used: YouTube thumbs are
16:9 with the 9:16 video pillarboxed between blurred bars (TikTok thumbs
are full 9:16, which is all we'd used before). Fix in BOTH pickers
(`components/parallel/zone-picker.tsx` + the duplicated one in
`app/remix/page.tsx`): wrap the img in a box forced to the VIDEO's aspect
(`aspectRatio: width/height` from the preview meta) with
`object-fit: cover` — crops the blur bars, fills the picker; no-op for
TikTok. Rect mapping unchanged (getRenderedRect now sees a matching
aspect → full-area render). tsc clean. NOTE: frontend tsc must run in WSL
(`node_modules` is Linux-installed): `wsl … node_modules/.bin/tsc --noEmit`.

FOLLOW-UP BUG (caught live with the user via a console one-liner): after
the fix the picker rendered 0×0 — the wrapper div had only `mx-auto` +
max-width, which inside the Card's flex column shrinks to fit content;
the aspect-ratio box derives its width FROM the wrapper → circular → 0px.
The old <img> had an intrinsic width that masked this. Fix: `w-full` on
the wrapper (both pickers), commit 6d6479a. Debug protocol that worked:
have the user paste a JSON.stringify one-liner in the Console — got
naturalWidth (image loaded), clientWidth 0 (layout collapsed), computed
aspect-ratio (style applied) in one shot.

CRITICAL GENERALIZATION of the S3.7 lesson: **Turbopack dev (WSL) does
NOT see frontend file edits made from the Windows side** — inotify is
dead on /mnt/f, same as uvicorn --reload. HMR never fires and the dev
server keeps serving the previous compilation. EVERY frontend edit in
this setup needs `./dev.sh stop && rm -rf .next && ./dev.sh start` (or at
minimum a frontend restart) + a tab reload to become visible. This cost
two rounds of "tot asa apare" with the user before being spotted.
Also seen in the user's console: `fdprocessedid` attributes injected by a
browser extension (IDM-style form filler) → React hydration warnings on
localhost; harmless for this bug but worth disabling for localhost.

## S5.8 — TikTok auto-posting (DESIGN ONLY — user rules)
User wants the LAST automation mile: after parts are produced → schedule
to TikTok at user-preset daily hours. TWO HARD RULES from the user:
(1) implement NOTHING until they explicitly confirm; (2) the account must
NOT be flagged as a bot. Options laid out (in chat):
 1. Official Content Posting API + internal ClipForge scheduler — zero
    bot risk; needs TikTok developer app + audit (unaudited = posts go
    PRIVATE/Only-Me only); days-weeks of bureaucracy.
 2. Browser automation of the user's REAL Chrome session that only
    UPLOADS+SCHEDULES via TikTok's native web scheduler (≤10 days ahead),
    one human-paced session/day — small but NON-zero detection risk.
 3. Semi-auto "Posting queue" page (ClipForge preps everything, user
    drags + pastes ~2 min/day) — zero risk, one day of work.
Recommended 3 now + apply for 1 in parallel; 2 only as plan B. AWAITING
the user's choice — do not build any of it without their explicit OK.

---

## S5.9 — Merged main into the branch (60fps + eraser combined)
`main` had diverged 87 commits on a DIFFERENT line: feat "auto story doodle
video" (`e132894`), forced-60fps export, AND a whole TikTok-posting build
already done by a parallel session (`n8n/clipforge-tiktok-poster.json`,
`RESEARCH-antiban-posting-2026-06-25.md`, `scripts/dual_dispatch.py`,
`HANDOVER-2026-06-25.md` — DID NOT touch it). The S3 hardening + S4/S5
eraser work was only on `claude/parallel-processing`.
User: combine both, MUST keep 60fps.
- Backup first: tag `backup-parallel-20260702_2329` + zip
  `F:\ClipForge-backups\clipforge_parallel_20260702_2329.zip` + origin branch.
- `git merge main` → CLEAN, zero conflicts (merge `e604bba`). speed_match.py
  resolved to main's `target_fps = 60.0` (60fps kept ✓); eraser S5 survived
  (inpaint `_patch_degenerate`, `caption_audit.py`, transcript_words wiring
  x6 remix / x2 parallel) ✓; main's new features present ✓.
- Verified `py_compile` OK + `tsc --noEmit` OK; pushed; dev server restarted
  (`rm -rf .next`) to serve merged code.
- The export diff the user spotted = 60fps: main lifts 30fps sources to 60
  (fps= or minterpolate); old branch kept src fps. Now unified on 60fps.

## S5.10 — Level B scheduled poster BUILT (n8n → Postiz, per-country slots)
User chose Level B (self-hosted Postiz per country behind that country's
proxy, official TikTok API) and asked to build + test. Accounts confirmed
"really localized" (small scale, not a fleet). BOUNDARY held across many
turns: built the legitimate automation (official-API posting + per-country
proxy for REAL localized accounts = network consistency); REFUSED and did
NOT build antidetect/fingerprint-spoofing (Dolphin) or farm-concealment
infra. Also declined earlier: false monetization appeal, fraud research,
"escape the flag" tricks. The reclipped-content originality problem was
flagged repeatedly as the real risk (not the automation).
Built (all in `n8n/`, committed):
- `lib/schedule.js` — pure scheduling core: each variant → next free daily
  slot per account in the account's TZ (RO/FR etc.), no double-book, day
  roll-over, role mapping, per-account caption suffix.
- `lib/schedule.test.js` — **9/9 node tests pass** (tz math, assignment,
  roll, dedupe, role filter). This is the "does it work" proof — the
  scheduling engine is verified.
- `build-workflow.js` → `clipforge-postiz-poster.json` — n8n workflow
  GENERATED from the tested core (Code node == tested module). 6 nodes,
  embedded JS syntax-checked. Targets Postiz `POST /public/v1/posts` with
  `type:schedule, date:<slot>`.
- `postiz/docker-compose.yml` + `.env.example` — ONE Postiz stack per
  country, `HTTP_PROXY` scoped to the postiz app container only (verified
  not on postgres/redis). `.env` is per-country, not committed.
- `README-B.md` — Accounts sheet-tab schema, setup, honest caveats.
Key honest caveats documented for next session:
- B costs the TikTok app **audit** (~2-4 wks, SELF_ONLY until approved)
  that the existing Upload-Post poster (A, `n8n/README.md`) SKIPS. Posting
  IP is a minor signal per the user's own RESEARCH doc — B's per-country
  egress is belt-and-suspenders.
- LIVE posting NOT testable here: needs the user's Postiz instance + audit +
  account OAuth from the country IP + proxy creds. I don't enter their
  credentials. Logic tested; live is their step.
- docker NOT available in this env (node v24 is) — couldn't spin Postiz.
Env: still on branch `claude/parallel-processing` post-merge with main
(60fps + eraser + main's automation all present). Not pushed yet this turn.

## S5.11 — Sheets full automation (in progress, 4 parts)
User request: automate Parallel-from-Sheets — pick erase/caption zones ONCE
at start (same for every row), loop rows until Stop or sheet exhausted;
transcript language per COMMENTATOR PRESET (its `tts_language`), not the
shared default (for foreign-country accounts); and after transcribe, verify
all keys/quota BEFORE the expensive erase (no wasted resources — a row died
today at 48% on ElevenLabs `quota_exceeded` AFTER a full erase).

Part 4 DONE + tested (`services/preflight.py`, wired in
`parallel_pipeline.py` between transcribe and erase): checks transcript
engine key (openai/anthropic) or Ollama reachability, ElevenLabs key
presence, and ElevenLabs quota (`get_user_info` → limit−count) vs estimate
(raw transcript chars × #EL variants, raw len = safe upper bound since
cleaning shortens). Raises a clear RuntimeError before erase. Unit-tested:
140-remaining/694-needed (today's exact case) → BLOCKED; enough → PASS.
Remix pipeline preflight = TODO (single-variant, deferred; user uses Sheets).

Part 3 DONE + tested (`parallel_pipeline.py`): clean once per DISTINCT variant
language (cache), keyed on `tts_language` (fallback shared → keep-original).
Voice AND burned captions now in each variant's language. RO,FR,RO → 2 cleans.
Description/result use variant #0's language. Commit 53c823f.

Parts 1+2 DONE (frontend, tsc clean — NOT live-tested, see below):
- `ParallelProcessor` gains optional `autoControls` prop; when set (Sheets page
  only), an "Auto-run all rows" button appears. `start()` refactored to
  `startWith(urlOverride, extrasOverride)` so the loop reuses the SAME captured
  zones + variants for each pulled url. A terminal-status effect advances the
  loop: on done → count + pull next; on failed → skipRow (so a stuck row isn't
  re-pulled forever — failed rows don't advance next_row) + pull next; ends when
  pullNext returns null (sheet exhausted) or Stop pressed. Zones captured once on
  the first preview; backend re-scales to each video's real dims.
- `parallel-sheets/page.tsx`: `autoPullNext` (returns {url,extras} or null on
  empty row) + `autoSkip`, passed as `autoControls`.
- The manual single-run path is untouched (auto only via the new prop).

VERIFIED: tsc clean, py_compile clean, preflight + lang-dedupe unit tests pass.
NOT live-tested (needs real ~10-min jobs AND ElevenLabs quota is currently
exhausted — that's what surfaced this whole request): the full multi-row loop
end-to-end. Test path when quota/XTTS available: configure Sheets, Preview one
video → pick zones + set variants (with per-country tts_language) → "Auto-run
all rows" → it should process next_row onward, commit each description, advance,
and stop at the first empty row. Watch for: the terminal-effect double-firing
(guarded by autoHandledJobRef) and the skip-on-fail not looping.

## S5.11 — LIVE-tested the pipeline with XTTS + found/fixed the FR-bloat bug
Live end-to-end test of the per-variant-language path with XTTS (bypassing
Sheets, so the user's real production sheet at next_row=168 was untouched):
job `9109324011d0`, test Grinch URL, 2 variants EN + FR, erase_mode=blur.
- RESULT: done in 15 min, 0 errors. Log confirmed `Cleaning transcript (en)…`
  then separately `Cleaning transcript (fr)…` → **per-variant language works**
  (each variant's clean/translate is in ITS tts_language). Both videos +
  descriptions produced.
- BUG the test caught: FR cleaned text = **3376 chars (2.3× the EN 1458)** →
  FR raw voice 185s vs EN 85s → FR final video 150s vs source 75s. Isolated
  repro (`server/scripts/repro_fr_clean.py`, EN/FR/DE × ollama/openai on a
  synthetic sample) showed BOTH engines are normally 1.0–1.2× — so it's a
  STOCHASTIC bloat spike of qwen2.5:7b on a particular input, NOT systematic,
  and NOT caught by `_META_HEADERS` (EN/RO phrases only, no FR/DE/…).
- FIX (commit 64b47b0): `transcript_cleaner.clean_transcript` now caps every
  chunk at **MAX_LEN_RATIO = 1.2 × source** (`_is_bloated` / `_trim_to_ratio`
  / `_clean_one_chunk`): on bloat → retry once → else hard-trim at a sentence
  boundary so output NEVER exceeds 1.2×. Language- + engine-agnostic. Normal
  output passes untouched. Tested 9/9 (`server/scripts/test_bloat_guard.py`,
  incl. forced-bloat retry+trim + normal pass-through). Backend restarted to
  load it. This is the safety net that lets unattended multi-country auto-run
  never waste TTS/GPU on a bloated clip.
- GOTCHA re-confirmed: the Windows-side git-bash `curl` intermittently can't
  reach :8420 (WSL relay recycled → backend looked "down" mid-test but had
  finished cleanly). Run curls against the backend from `wsl.exe -e bash`
  instead; `/tmp` inside those one-shot WSL calls is ephemeral (write probe
  files under `data/` on /mnt/f).

End of handover.
