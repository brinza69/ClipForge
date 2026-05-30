# ClipForge — Session Handover (2026-05-27/28)

> **Purpose:** Comprehensive context dump for the next Claude conversation. The user is Romanian, prefers replies in Romanian (but Markdown / code blocks / file paths stay English). Keep messages short and concrete unless the user explicitly asks for a deep dive.

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

End of handover.
