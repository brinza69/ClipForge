# PRP — Auto Story Doodle Video

## Goal

New feature tab **"Auto Story Doodle Video"**: create faceless English doodle-explainer
videos from a topic. Flow: topic → OpenAI script + scene split + image prompts →
Kokoro local TTS voiceover (per scene, real durations) → user manually generates
images in Google Flow and drags them into scene slots → FFmpeg assembles final MP4
(images + voiceover + subtitles + subtle motion).

**NO automatic image generation API.** Manual Flow Mode only. Paid providers are
placeholder stubs, disabled.

## Codebase facts (verified — do not re-explore)

- Backend: FastAPI at `server/`, port 8420. Venv: `server/.venv` (Python 3.13.1,
  torch 2.6.0+cu124, CUDA available). Run: `server/.venv/Scripts/python.exe`.
- FFmpeg 8.1 + ffprobe are on PATH (winget install). `espeak-ng` NOT on PATH.
- Job queue: `server/job_queue.py` singleton `job_queue`. Handler signature:
  `async def handler(job_id, project_id, clip_id, metadata, queue)`. Register via
  `job_queue.register_handler("type", fn)`. Progress: `await queue.update_progress(job_id, 0.5, "msg")`.
  Cancellation check: `queue.is_cancelled(job_id)` → raise `job_queue.JobCancelledError`.
  `JobModel.project_id` is a plain indexed string (no FK) — safe to pass doodle project ids.
- Job polling (already exists, reuse): `GET /api/jobs/{job_id}` → JobResponse
  (id, status: queued|running|done|failed|cancelled, progress 0-1, progress_message, error),
  SSE at `GET /api/jobs/{job_id}/stream`, `POST /api/jobs/{job_id}/cancel`.
- OpenAI key: `from services.transcript_cleaner import get_openai_key, DEFAULT_OPENAI_MODEL`
  (key lives encrypted in `data/transcript_config.json`, set from the Settings page;
  env `OPENAI_API_KEY` also works). Call OpenAI with `httpx` directly
  (`https://api.openai.com/v1/chat/completions`) — see `services/descriptions.py` for the pattern.
- Config: `server/config.py` `settings` (pydantic-settings). `settings.data_dir` = `D:\clipforge\data`.
- Router convention: `router = APIRouter(prefix="/api/doodle", tags=["doodle"])`, included in `main.py`.
- Frontend: Next.js 15 App Router. Backend calls go through the `/worker-api/:path*` →
  `http://127.0.0.1:8420/api/:path*` rewrite (next.config.ts) using plain `fetch`.
  Nav: `src/components/layout/sidebar.tsx` `navItems` array. UI: TailwindCSS + shadcn/ui
  components in `src/components/ui/` (button, card, input, select, tabs, etc. — check what
  exists before importing). Look at `src/app/parallel/page.tsx` and `src/app/utilities/page.tsx`
  for page structure/style conventions.
- File size limit: **no file over 500 lines** — split into modules/components.

## File ownership (STRICT — each agent touches ONLY its files)

| Agent | Files |
|---|---|
| Script | `server/services/doodle/script_generator.py` |
| Kokoro | `server/services/doodle/kokoro_service.py`, append to `server/requirements.txt`, pip install into `server/.venv` |
| FFmpeg | `server/services/doodle/renderer.py`, `server/services/doodle/subtitles.py` |
| Backend | `server/services/doodle/__init__.py` (empty), `server/services/doodle/storage.py`, `server/services/doodle/image_providers.py`, `server/routers/doodle.py`, `server/workers/doodle_pipeline.py`, edits to `server/models.py` (JobType enum), `server/config.py` (doodle_dir), `server/main.py` (router + handlers + static mount) |
| Frontend | `src/app/doodle/**`, `src/components/doodle/**`, `src/types/doodle.ts`, edit `src/components/layout/sidebar.tsx` (add nav item), edit `next.config.ts` (add one rewrite) |

Cross-module imports are fine (backend worker imports script_generator/kokoro_service/renderer)
— code against the contracts below; the module will exist at integration time.

## Storage layout

Root: `settings.doodle_dir` = `data/doodle/`. Each project: `data/doodle/{project_id}/`:

```
storyboard.json          ← single source of truth (schema below)
script/script.json       ← raw LLM output
script/script.txt        ← full narration as plain text
prompts/flow_prompts.csv ← columns: index,narration,subtitle,image_prompt,expected_filename
prompts/flow_prompts.json
audio/scene_000.wav …    ← per-scene Kokoro output (24 kHz wav)
audio/final_voiceover.wav
images/scene_000.png …   ← user-uploaded (any ext normalized to the flow_filename)
captions/captions.srt
exports/final_video.mp4
```

## storyboard.json schema (THE shared contract)

```json
{
  "id": "abc123def456",
  "title": "", "description": "", "tags": [],
  "topic": "", "niche": "history", "mode": "topic",
  "status": "created|scripting|script_ready|voicing|voice_ready|rendering|done|failed",
  "error": null,
  "settings": {
    "target_duration_seconds": 180,
    "frame_interval_seconds": 3,        // 2 | 3 | 4 | "auto"
    "aspect_ratio": "16:9",             // "16:9" | "9:16" | "1:1"
    "resolution": "1920x1080",          // derived: 16:9→1920x1080, 9:16→1080x1920, 1:1→1080x1080
    "voice": "am_michael",
    "voice_speed": 0.95,
    "subtitle_style": "youtube_clean",  // "youtube_clean" | "tiktok_bold" | "minimal"
    "burn_subtitles": true,
    "motion_style": "subtle",           // "subtle" | "zoom_in" | "zoom_out" | "pan" | "none"
    "motion_intensity": 0.5,            // 0..1
    "openai_model": null,               // null → DEFAULT_OPENAI_MODEL
    "render_quality": "high",           // "high" | "medium"
    "use_gpu": true,
    "allow_placeholders": false
  },
  "scenes": [
    {
      "index": 0,
      "narration": "Tonight, when the sun goes down, you're not going to sleep the way you think.",
      "subtitle": "Tonight, when the sun goes down...",
      "estimated_duration": 3.0,
      "image_prompt": "A simple hand-drawn doodle of ...",
      "flow_filename": "scene_000.png",
      "image_path": null,               // relative to project dir, e.g. "images/scene_000.png"
      "audio_path": null,               // "audio/scene_000.wav"
      "audio_duration": null            // real seconds from ffprobe, filled by TTS
    }
  ],
  "final_voiceover_path": null,         // "audio/final_voiceover.wav"
  "total_audio_duration": null,
  "export_path": null,                  // "exports/final_video.mp4"
  "created_at": "...", "updated_at": "..."   // ISO 8601
}
```

## Contract: storage.py (Backend agent implements; all others may import)

```python
# server/services/doodle/storage.py
def project_dir(project_id: str) -> Path            # settings.doodle_dir / project_id
def create_project(payload: dict) -> dict           # new id (uuid4.hex[:12]), dirs, initial storyboard
def load_storyboard(project_id: str) -> dict        # raises FileNotFoundError
def save_storyboard(project_id: str, sb: dict) -> None   # atomic write (tmp+replace), bumps updated_at
def list_projects() -> list[dict]                   # storyboard summaries, newest first
def delete_project(project_id: str) -> None
def write_prompt_exports(project_id: str, sb: dict) -> None  # writes prompts/*.csv + *.json + script/*
def missing_images(sb: dict) -> list[int]           # scene indexes with image_path None/nonexistent
```

## Contract: script_generator.py (Script agent)

```python
# server/services/doodle/script_generator.py
async def generate_storyboard(
    *, mode: str,                 # "topic" | "script"
    topic: str | None,            # required for mode=topic
    script_text: str | None,      # required for mode=script (user's own script)
    niche: str,
    target_duration_seconds: int,
    frame_interval_seconds,       # int 2/3/4 or "auto"
    aspect_ratio: str,
    model: str | None = None,
    progress_cb=None,             # async fn(fraction: float, message: str) or None
) -> dict:
    """Returns {"title","description","tags","scenes":[{index,narration,subtitle,
    estimated_duration,image_prompt,flow_filename}]}. Raises RuntimeError with a
    clear message if no OpenAI key or the API fails."""
```

Rules:
- Uses `get_openai_key()` + httpx, `response_format={"type":"json_object"}`, temperature ~0.8
  for script, ~0.7 for prompts.
- English only. Script voice: natural, cozy, storytelling, retention-focused, faceless
  YouTube documentary/explainer. Open with a hook. No generic AI filler ("in this video",
  "let's dive in", "delve").
- Scene length: each scene 2–4 s of narration (≈ 5–12 words at ~150 wpm; `"auto"` interval →
  target 3 s). Total narration word count ≈ `target_duration_seconds / 60 * 150` words.
- **Two-stage generation for long videos**: stage 1 writes the full script (title,
  description, tags, hook, full narration). Stage 2 splits into scenes + writes one image
  prompt per scene, in batches of ≤ 25 scenes per API call (long videos exceed one
  completion) — pass the running scene list summary for prompt variety. Report progress
  between batches via progress_cb.
- Image prompt style (EVERY prompt): simple hand-drawn doodle, white background, thick black
  imperfect marker lines, stick figures / arrows / timelines / big handwritten words where
  useful, basic colored highlights, cozy educational explainer style, minimal shapes,
  "no 3D, no realism, no cinematic lighting, no photorealism", end with the aspect ratio
  (e.g. "16:9"). Prompts must vary scene-to-scene (different compositions: character doodle,
  timeline, map, diagram, labeled object, before/after split…), match the narration
  specifically, avoid copyrighted characters and real people, text in image only when useful
  (single big handwritten words like "HUMAN HISTORY").
- `subtitle` = shortened narration (≤ 42 chars, ellipsis ok). `flow_filename` = `scene_{index:03d}.png`.
- mode="script": don't rewrite the user's script (light cleanup only); split + prompts only;
  still generate title/description/tags from it.
- Validate/normalize the LLM JSON (indexes sequential from 0, required keys present; drop
  empty scenes; clamp estimated_duration 1.5–6.0).

## Contract: kokoro_service.py (Kokoro agent)

```python
# server/services/doodle/kokoro_service.py
VOICES = [
  {"id": "am_michael", "label": "Michael (US male, warm)", "lang": "a"},
  {"id": "am_fenrir",  "label": "Fenrir (US male, deep)",  "lang": "a"},
  {"id": "bm_fable",   "label": "Fable (UK male, cozy)",   "lang": "b"},
  {"id": "bm_george",  "label": "George (UK male, calm)",  "lang": "b"},
]
def is_available() -> tuple[bool, str]     # (ok, human-readable reason if not)
def get_audio_duration(path) -> float      # ffprobe, raises RuntimeError on failure
async def generate_scene_audio(text: str, voice: str, speed: float, output_path) -> float
    # synthesizes ONE scene to 24kHz mono WAV via soundfile; returns REAL duration (ffprobe)
async def generate_all_scene_audio(scenes: list[dict], voice: str, speed: float,
                                   audio_dir, progress_cb=None) -> list[dict]
    # per scene: audio/scene_{index:03d}.wav; sets scene["audio_path"], scene["audio_duration"];
    # progress_cb(done/total, f"Voicing scene {i+1}/{n}") after each; returns mutated scenes
def concatenate_audio_files(files: list, output_path) -> float
    # ffmpeg concat demuxer (or soundfile append) → final_voiceover.wav; returns duration
```

Rules:
- `pip install kokoro soundfile` into `server/.venv`. Append both to `server/requirements.txt`
  with a comment block. Python 3.13 — verify install actually works.
- espeak-ng: try `pip install espeakng-loader` (bundles the lib; misaki uses it as fallback
  G2P). If English synthesis works without the MSI, fine; is_available() must return a clear
  message telling the user to install the espeak-ng MSI from official releases if phonemizer
  init fails.
- KPipeline is sync + heavy: module-level lazy singleton per lang_code (`'a'`=American,
  `'b'`=British — derive from voice prefix). Run synthesis in `asyncio.to_thread`. GPU is
  available (torch CUDA) — let kokoro use it if it supports `device`.
- KPipeline yields chunks (graphemes, phonemes, audio) — concatenate all chunk audio for one
  scene into one wav (24000 Hz).
- Default speed 0.95. NO fallback to any paid TTS. Failures raise RuntimeError with a clear,
  actionable message.
- **Prove it works**: after implementing, run a real synthesis test from the venv
  (`am_michael`, one sentence) and check the wav exists with duration > 0. First run
  downloads ~300 MB model from HuggingFace — that's expected.

## Contract: renderer.py + subtitles.py (FFmpeg agent)

```python
# server/services/doodle/subtitles.py
def build_srt(scenes: list[dict]) -> str     # cumulative REAL audio_duration timings, HH:MM:SS,mmm
def subtitle_style_args(style: str, resolution: tuple[int,int]) -> str
    # returns force_style string for subtitles filter per style:
    # youtube_clean: white text, semi-transparent black box (BorderStyle=4/3 + BackColour),
    #   Arial-ish, moderate size, bottom
    # tiktok_bold: big bold white, heavy black outline+shadow, centered lower third
    # minimal: small white text, thin outline, bottom

# server/services/doodle/renderer.py
async def render_video(project_dir: Path, storyboard: dict, progress_cb=None) -> Path:
    """Assembles exports/final_video.mp4. Returns absolute path. Raises RuntimeError
    with the ffmpeg stderr tail on failure."""
```

Rules:
- Timeline: scene i shown for its REAL `audio_duration` (from storyboard; error if any scene
  with audio lacks it). Concat per-scene audio → `audio/final_voiceover.wav` (do it here if
  not already present). **Last image extended** so video duration == total audio duration
  exactly (`-shortest` on audio never truncates; pad last segment). No black gaps.
- Missing image handling: if `settings.allow_placeholders` and a scene has no image, generate
  a placeholder doodle frame — white background + big black centered text of the scene
  subtitle (ffmpeg `drawtext` or PIL if available; keep it dependency-free → use drawtext,
  escape text). If not allowed and images missing → RuntimeError listing missing scenes.
- Motion (default "subtle"): per-scene `zoompan` at 30 fps — slow zoom in (scale 1.0→~1.08 ×
  intensity), zoom out, or tiny pan; "subtle" alternates zoom-in/zoom-out per scene parity;
  "none" = plain loop. Upscale source 2× before zoompan to avoid jitter (standard trick).
- Geometry: scale to fit resolution with white padding (`scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2:color=white`), force even dims. Resolutions:
  16:9→1920x1080, 9:16→1080x1920, 1:1→1080x1080. 30 fps, H.264 + AAC 192k, `yuv420p`,
  `+faststart`.
- Encoder: if `settings.use_gpu` and `h264_nvenc` available (probe `ffmpeg -encoders` once) →
  nvenc (preset p5, cq 21 high / 26 medium); else libx264 (crf 19 high / 23 medium).
- Subtitles: ALWAYS write `captions/captions.srt`. If `burn_subtitles`, burn with
  `subtitles=...:force_style=...` (mind Windows path escaping in filter: `C\:/...` — build
  with forward slashes and escape the colon, or cd into the project dir and use relative
  paths via `cwd=`).
- Implementation approach (keep it robust): render each scene to a short .ts/.mp4 segment
  (image + zoompan + trim to audio_duration), then concat demuxer all segments, then mux with
  final_voiceover.wav and burn subtitles in the final pass. Progress: per-scene segments →
  0.05–0.75, concat 0.8, final mux 0.9, done 1.0 via progress_cb(fraction, message).
- Run ffmpeg via `asyncio.create_subprocess_exec`, capture stderr; on failure include last
  ~400 chars of stderr in the RuntimeError message.
- Verify final duration with ffprobe ≈ total_audio_duration (±0.5 s) before returning.

## Contract: image_providers.py (Backend agent)

```python
PROVIDERS = [
  {"id": "manual_flow",   "label": "Manual Flow (Google Flow)", "enabled": True,  "default": True},
  {"id": "manual_upload", "label": "Manual Upload",             "enabled": True},
  {"id": "openai_images", "label": "OpenAI Images",             "enabled": False},
  {"id": "deepai",        "label": "DeepAI",                    "enabled": False},
  {"id": "higgsfield",    "label": "Higgsfield",                "enabled": False},
  {"id": "comfyui_local", "label": "ComfyUI (local)",           "enabled": False},
]
DISABLED_MESSAGE = "Paid image API is disabled for now. Use Manual Flow Mode to save credits."
```
Base `ImageProvider` class with `generate(prompt, out_path)` raising `NotImplementedError`;
disabled providers raise `RuntimeError(DISABLED_MESSAGE)`. No API calls anywhere.

## Contract: API routes (Backend agent) — prefix `/api/doodle`

```
GET    /voices                        → {"available": bool, "reason": str|null, "voices": VOICES}
GET    /image-providers               → PROVIDERS list
GET    /projects                      → list of storyboard summaries (id,title,topic,niche,status,
                                        scene_count,images_uploaded,missing_images,created_at,
                                        total_audio_duration,export_path,settings)
POST   /projects                      → body {mode, topic?, script_text?, niche, custom_niche?,
                                        target_duration_seconds, frame_interval_seconds,
                                        aspect_ratio, voice, voice_speed?, subtitle_style?,
                                        burn_subtitles?, motion_style?, motion_intensity?,
                                        openai_model?, render_quality?, use_gpu?}
                                      → creates project, enqueues doodle_script job
                                      → {"project": sb, "job_id": ...}
GET    /projects/{id}                 → full storyboard + computed {"missing_images":[...]}
DELETE /projects/{id}                 → deletes folder
POST   /projects/{id}/voiceover       → enqueue doodle_tts → {"job_id"} (409 if no scenes)
POST   /projects/{id}/render          → body {allow_placeholders?: bool} → enqueue doodle_render
                                      → {"job_id"} (409 if missing images and !allow_placeholders,
                                        response detail lists missing scene indexes)
POST   /projects/{id}/images/{scene_index}   → multipart file upload for one scene; saves as
                                        images/{flow_filename} (normalize to .png name but keep
                                        original bytes; any image ext accepted: png/jpg/jpeg/webp);
                                        sets scene.image_path → returns updated scene
POST   /projects/{id}/images/bulk     → multipart, multiple files AND/OR one .zip; auto-match by
                                        filename `scene_(\d+)` pattern (also match bare numbers
                                        like "3.png" → scene 3); unzip with zipfile; returns
                                        {"matched": n, "unmatched": [names]}
DELETE /projects/{id}/images/{scene_index}   → remove image from scene
POST   /projects/{id}/scenes/reorder  → body {"order": [scene indexes in new order]} → reindexes
                                        scenes AND renames audio/image files + flow_filenames
                                        consistently
PATCH  /projects/{id}/settings        → partial settings update (voice, subtitle_style, motion…)
GET    /projects/{id}/prompts.csv     → FileResponse (regenerate from storyboard first)
GET    /projects/{id}/prompts.json    → FileResponse
```

Worker `server/workers/doodle_pipeline.py` — `register_doodle_handlers(queue)`:
- `doodle_script`: status scripting → call generate_storyboard → merge scenes/title/… into
  storyboard → write_prompt_exports → status script_ready. On error: status failed + error msg.
- `doodle_tts`: status voicing → generate_all_scene_audio → concatenate → set
  final_voiceover_path/total_audio_duration → status voice_ready.
- `doodle_render`: status rendering → renderer.render_video → set export_path → status done.
- All: save_storyboard after mutation; wire progress_cb → queue.update_progress; check
  `queue.is_cancelled(job_id)` between scenes.

`main.py` edits: import + include doodle router; `from workers.doodle_pipeline import
register_doodle_handlers; register_doodle_handlers(job_queue)` in lifespan;
`app.mount("/doodle-files", StaticFiles(directory=settings.doodle_dir), name="doodle-files")`
(after ensure_dirs). `config.py`: add `doodle_dir` property (`data_dir / "doodle"`) + add to
`ensure_dirs`. `models.py`: add `doodle_script`, `doodle_tts`, `doodle_render` to JobType enum.
JobType values are informational — queue matches on strings.

## Contract: Frontend (Frontend agent)

Nav: add `{ label: "Auto Story Doodle", href: "/doodle", icon: PenTool }` to sidebar navItems.
next.config.ts: add rewrite `{ source: "/worker-doodle/:path*", destination: `${workerBase}/doodle-files/:path*` }`
— image previews/exports served at `/worker-doodle/{projectId}/images/scene_000.png` etc.

Types in `src/types/doodle.ts`: `DoodleScene`, `DoodleSettings`, `DoodleStoryboard`,
`DoodleProjectSummary`, `DoodleVoice` — mirror storyboard.json schema exactly.

Pages (client components, plain fetch to `/worker-api/doodle/...`, React Query if the
existing pages use it — follow `src/app/parallel/page.tsx` conventions):

**`src/app/doodle/page.tsx`** — tab home:
- Header "Auto Story Doodle Video".
- Persistent info banner: “Manual Flow Mode: no automatic image API cost. Generate images in
  Flow, then drag and drop them here.”
- "New Video" form (component `src/components/doodle/new-project-form.tsx`):
  - Mode tabs: **From Topic** (default) | From Script (textarea for script_text).
  - topic input; niche select: Ancient Humans, History, Weird Facts, Psychology, Space,
    Science, Mystery, Animals, Custom (custom → free-text input).
  - Video length: 30s / 1 min / 3 min / 5 min / 8 min / 10 min / Custom minutes (number input).
  - Frame interval: 2s / 3s (default) / 4s / Auto.
  - Aspect ratio: 16:9 YouTube (default) / 9:16 Shorts/TikTok/Reels / 1:1 Square.
  - Voice select (from GET /voices; default am_michael; show unavailable state + reason).
  - Subtitle style select (YouTube clean default / TikTok bold / Minimal), burn-in toggle.
  - Motion style select (Subtle ON default / Zoom in / Zoom out / Pan / None).
  - Live **estimated frames** = ceil(durationSeconds / intervalSeconds) (auto→3):
    “≈ 100 images to generate in Flow”.
  - Collapsed "Advanced" section: OpenAI model (text input, placeholder gpt-4o-mini), voice
    speed (0.8–1.1 slider, default 0.95), motion intensity slider, render quality
    (high/medium), use GPU toggle.
  - Submit → POST /projects → navigate to detail page.
- Project list (GET /projects, poll 5s): cards with title/topic, niche, status badge, scene
  count, uploaded/missing image counts, open + delete.

**`src/app/doodle/[id]/page.tsx`** — project detail (keep page < 300 lines; compose components):
- **Progress steps** component: Topic → Script → Flow Prompts → Kokoro Voice → Images
  Uploaded → Captions → Render → Export, derived from status + data (script_ready ⇒ first 3
  done; voice_ready ⇒ voice done; all images ⇒ images done; done ⇒ all). Show: current step,
  active job progress % (poll `/worker-api/jobs/{job_id}` at 1s while a job runs — store
  job_id from mutation responses), estimated frames, missing image count, total audio
  duration, selected voice, aspect ratio.
- **Manual Flow Mode card**: numbered instructions (copy prompt → generate in Google Flow →
  download → drag & drop into the scene slot → when all scenes have images, Render Video).
- Buttons row: **Generate Flow Prompts** (re-runs script job if needed / reveals storyboard),
  Copy All Prompts (clipboard, numbered), Export CSV, Export JSON (link to /prompts.csv|json),
  **Generate Voiceover** (POST /voiceover), **Render Video** (POST /render; if 409 missing
  images → dialog offering "Use placeholder frames" → retry with allow_placeholders).
- **Storyboard table** (`src/components/doodle/storyboard-table.tsx` + `scene-row.tsx`):
  per scene — #, narration, subtitle, image_prompt (truncated, expandable), copy-prompt
  button, audio duration (if voiced) with small <audio> preview, **image slot**: drag-drop
  zone + click-to-browse (single file → POST /images/{index}); when filled: thumbnail from
  `/worker-doodle/{id}/{image_path}`, replace on re-drop, remove button; **status chip**:
  red "Missing image" / amber "No audio" / green "Ready". Reorder scenes via up/down buttons
  (POST /scenes/reorder).
- **Bulk upload zone**: drop many files or a .zip → POST /images/bulk; toast with
  matched/unmatched. Mention auto-match convention scene_000.png…
- Missing images: red warning banner with count; Render disabled (unless placeholder path).
- When done: video player (`/worker-doodle/{id}/exports/final_video.mp4`) + download link +
  duration.
- Render errors / failed status: red alert with storyboard.error, Retry button.

Style: match existing app (dark theme, cards, shadcn). Use lucide icons. No new deps unless
already in package.json (check before importing; write plain HTML5 drag-drop, no dnd lib).

## Testing checklist (QA)

1. Backend boots clean (`.venv` uvicorn) with new router/handlers registered.
2. POST /projects (topic mode, 30 s) → script job → script_ready, scenes ≈10, prompts CSV/JSON on disk.
3. Voiceover job → per-scene wavs with real ffprobe durations + final_voiceover.wav.
4. Upload single image + bulk (zip auto-match) + missing-image 409 + placeholder render path.
5. Render 16:9 and 9:16 → final mp4 duration == audio duration (±0.5 s), subtitles burned.
6. Frontend: tab renders, estimated frames math, drag-drop, progress steps.
7. Grep the new code for any image-generation API call — there must be NONE.
