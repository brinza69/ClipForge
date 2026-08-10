# Video Transformare TikTok — decision & contract register

Single source of truth for the wizard's architecture. Conclusions and their
consequences only (no chain-of-thought), per the feature spec §23.7.

## D1 — Reuse the doodle architecture, not a new SQL table
`project.json` per project under `data/tiktok/{id}/` is the authoritative,
resumable state; jobs are tracked with the existing `JobModel`.
**Why:** `JobModel.project_id` has no FK constraint, which is exactly why doodle
projects (never rows in `projects`) can enqueue jobs. Same trick here.
**Consequence:** no migration needed; `services/tiktok_transform/storage.py`
owns the schema; the frontend never stores step state (it re-derives from
`project.steps`).

## D2 — CLAUDE.md's file map is stale (found during the audit)
`server/services/exporter.py`, `server/workers/pipeline.py` and
`server/services/scorer.py` **do not exist**. The real render pipeline is
`server/workers/remix_pipeline.py`. `services/captioner.py::generate_captions()`
is dead code (zero callers) — the live caption path is
`services/caption_overlays.py`.
**Consequence:** build on `caption_overlays` + `remix_pipeline` primitives.

## D3 — Montage = original video speed-matched to the voice-over
User decision. The source clip stays the base and is time-stretched onto the
voice length via `services/speed_match.compute_speed_plan()`; no cutting or
re-ordering of segments.
**Why:** it is the proven production path (remix/parallel pipelines ship it),
needs no new media code, and cannot desync.
**Consequence:** spec §10's re-ordering presets, hook-reveal-first and
transitions are explicitly **out of scope**. Frame selection still exists — it
feeds the vision pass (step 3) and thumbnails (step 7).

## D4 — Output must be ≥ 60 s
User requirement. The ~1200-character Romanian script at ElevenLabs speed 1.1
lands at ~65–72 s, and speed-match stretches the video onto that length, so the
export is ≥ 60 s by construction. `settings.min_output_duration_s = 60.0` is
enforced in the pre-export check; `split_into_parts` from the Narator preset is
deliberately **not** applied (one long video, not parts).

## D5 — Frame understanding uses OpenAI vision
User decision. Selected frames are sent to an OpenAI vision model to produce
chronological notes, which are the only factual input to the Romanian script.
**Why:** these clips have no narration (music only), so `faster-whisper` yields
nothing; without vision the model would invent details, which spec §7 forbids.
The OpenAI key is already configured and managed by `transcript_cleaner`.
**Consequence:** frames leave the machine. Vision failure must degrade to a
clear error, never to a fabricated script.

## D6 — Voice/caption defaults come from the existing "Narator" preset
`data/variant_presets/narator.json`: voice `8nBBDfYxYXmDNaqTCxPH`, speed `1.1`,
captions `bold_impact` @ scale 2.0, 1 word per chunk.
**Consequence:** the wizard sounds/looks like the rest of the rig by default;
every value stays overridable per project.

## D7 — Montage + subtitles + export are ONE fused ffmpeg encode
The wizard shows 9 steps, but "Montaj" and "Subtitrări" are settings screens.
Only `tiktok_render` encodes: speed-match vfilter + scale/pad to 1080×1920 +
libass burn, in a single pass.
**Why:** remix_pipeline fuses them precisely to avoid a second generation of
compression loss.

## D8 — 60 fps is frame duplication, and that is intentional
Sources are 24–30 fps, so `-r 60` duplicates frames; no new motion is created.
Spec §15 permits this (interpolation optional, not default). `minterpolate` is
available through `compute_speed_plan` but stays off by default (slow, artefacts).

## D9 — Known API limits, surfaced not hidden
- ElevenLabs clamps `speed` to **0.7–1.2**; spec §9 asks 0.90–1.30. Above 1.2 is
  unreachable via the API, so the UI slider stops at 1.2.
- ElevenLabs returns **MP3**; the spec wants WAV 48 kHz → converted with ffmpeg.
- `config.export_fps = 30` is dead config (no consumer). The wizard's fps lives
  in `project.settings.export_fps` (60). Two sources of truth already existed;
  we do not add a third.

## D10 — Jobs share the doodle lane
The wizard's 7 job types join `DOODLE_LANE_TYPES` so heavy `parallel_pipeline`
runs (the video factory) cannot starve them — the exact problem that lane was
created for.

---

# Service contracts (implementation must match these signatures)

All services are **blocking** unless marked async, are called from job handlers
via `run_in_executor`, take an optional
`on_progress: Callable[[float, str], None]`, and must use the existing
`_ffmpeg_bin()` / `creationflags=0x08000000` idiom on Windows.

```python
# services/tiktok_transform/frames.py
def extract_candidates(video_path: str, out_dir: str, *, target: int = 20,
                       min_frames: int = 12, max_frames: int = 30,
                       scene_threshold: float = 0.28,
                       on_progress=None) -> list[dict]
    # -> [{"index": int, "t": float, "file": str, "sharpness": float,
    #      "score": float}]  chronological, deduped, no black/blurry frames

# services/tiktok_transform/vision.py
def describe_frames(frames: list[dict], *, model: str | None = None,
                    on_progress=None) -> list[dict]
    # frames: [{"index","t","file"}] -> [{"index","t","note"}] (RO or EN notes)

# services/tiktok_transform/script_gen.py
def generate_script(vision_notes: list[dict], settings: dict,
                    *, on_progress=None) -> dict
    # -> {"text","chars","words","estimated_duration"}
def regenerate_part(text: str, part: str, settings: dict) -> str   # "hook"|"final"

# services/tiktok_transform/voice.py
def synthesize_voice(text: str, out_path: str, settings: dict,
                     *, on_progress=None) -> dict
    # ElevenLabs -> MP3 -> WAV 48 kHz (+gain/fade/normalize) -> {"path","duration"}

# services/tiktok_transform/subtitles.py
def build_subtitles(text: str, voice_duration: float, settings: dict, *,
                    srt_path: str, ass_path: str,
                    video_w: int = 1080, video_h: int = 1920) -> dict
    # -> {"srt","ass","cues":[{"text","start","end"}]}

# services/tiktok_transform/montage.py
def render_final(source_video: str, voice_path: str, ass_path: str | None,
                 out_path: str, settings: dict, *, on_progress=None) -> dict
    # ONE fused encode -> {"path","duration","width","height","fps","size",
    #                      "speed_factor"}
def preflight_report(project: dict) -> dict
    # spec §16 -> {"ok": bool, "checks":[{"name","ok","detail"}]}

# services/tiktok_transform/thumbnails.py
def generate_thumbnails(project: dict, out_paths: dict, *,
                        on_progress=None) -> list[dict]
    # 3 variants (before_after | mystery | final_result), each 1080x1920
    # -> [{"variant","path","text"}]

# services/tiktok_transform/description.py
def generate_description(project: dict, variant: str = "normal") -> dict
    # -> {"text","hashtags":[...]}   variants: short|normal|engagement|no_tags
```

## REST surface (`/api/tiktok`, proxied as `/worker-api/tiktok`)

```
GET    /projects                     -> [summary]
POST   /projects        {url,title}  -> project           (creates + enqueues import)
GET    /projects/{id}                -> full project dict
DELETE /projects/{id}
PATCH  /projects/{id}/settings       -> project
POST   /projects/{id}/frames         -> {job_id}
PATCH  /projects/{id}/frames         {selected:[i], marks:{}} -> project
POST   /projects/{id}/script         -> {job_id}
PATCH  /projects/{id}/script         {text} -> project      (manual edit)
POST   /projects/{id}/voice          -> {job_id}
POST   /projects/{id}/thumbnails     -> {job_id}
POST   /projects/{id}/description    -> {job_id}
GET    /projects/{id}/preflight      -> preflight report
POST   /projects/{id}/export         -> {job_id}
GET    /projects/{id}/file/{kind}    -> streams frame/voice/thumb/export
```

Job progress reuses the existing `GET /api/jobs/{id}` + SSE stream — no new
transport. Every handler follows the doodle crash-safe triple: load → mark
running → save; work; on success/exception **reload fresh** → mark → save.
