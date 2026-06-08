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

## S3 — Branch / PR state at session end
`claude/parallel-processing`, **PR #21 open vs main**. P0 tasks (T1–T4)
done + pushed; plan status board updated. **P1 (T5–T9) and P2 (T10–T19)
remain** — see `docs/improvement-plan.md`. Repo is at 0 TypeScript errors.
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

End of handover.
