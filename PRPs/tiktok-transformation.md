# PRP: Video Transformare TikTok

Blueprint for the 9-step wizard that turns a TikTok transformation/build clip
into a Romanian-narrated, captioned 1080×1920 @60 fps video.

**Contracts and decisions live in `docs/clipforge-transformation-decisions.md`.
Read that first — this file is the map, that file is the law.**

## Goal

Paste a TikTok link → get back a ≥60 s vertical video narrated in Romanian by
the "Narator" voice, captioned, plus three 9:16 thumbnails and a ready-to-paste
description. Resumable: close the project mid-flow and continue later.

## Flow

```
Import → Cadre → Script → Voce → Montaj → Subtitrări → Thumbnail → Descriere → Export
```

Nine UI steps, **seven** background jobs: "Montaj" and "Subtitrări" are settings
screens whose values feed the single fused export encode (decision D7).

## Architecture

Disk-first, mirroring the doodle feature (decision D1):

- `data/tiktok/{project_id}/project.json` is the source of truth.
- `services/tiktok_transform/storage.py` owns the schema, `paths()`, `STEPS`,
  `DEFAULT_SETTINGS`, and the reload-mutate-save helpers.
- `workers/tiktok_pipeline.py` holds one handler per job type.
- `routers/tiktok.py` exposes `/api/tiktok/...`; the frontend reaches it through
  the Next proxy as `/worker-api/tiktok/...`.
- No new SQL table and no Pydantic response models — routers return raw dicts,
  exactly like `routers/doodle.py`.

## What is reused (do not rebuild any of this)

| Need | Reused from |
|---|---|
| Link validation, metadata, download | `services/downloader.py` (handles TikTok's HEVC-silent-audio bug) |
| Error messages for bad/private links | `downloader._classify_error()` |
| Frame grab / dimension probe | `services/caption_overlays.py` |
| LLM calls, API keys, retry | `services/transcript_cleaner.py`, `services/retry.py` |
| Voice synthesis | `services/elevenlabs.py::synthesize()` |
| Voice + caption defaults | `data/variant_presets/narator.json` |
| Caption ASS + safe zones | `services/caption_overlays.py`, `services/captioner_presets.py` |
| Sync video to voice length | `services/speed_match.py::compute_speed_plan()` |
| Fused encode reference | `workers/remix_pipeline.py::_stage_match_and_caption()` |
| Jobs, progress, SSE, cancel | `job_queue.py`, `routers/jobs.py` |
| Wizard UI patterns | `src/app/doodle/[id]`, `src/components/doodle/` |

## Task list

1. `storage.py` — project.json schema, paths, step state machine. **Done.**
2. `frames.py` — scene detection + sharpness ranking → 12–30 candidates.
3. `vision.py` — OpenAI vision notes per selected frame (decision D5).
4. `script_gen.py` — Romanian script, 1100–1250 chars, spec §7/§8 structure.
5. `voice.py` — ElevenLabs → WAV 48 kHz, gain/fade/normalize.
6. `subtitles.py` — cues from the script, SRT + ASS inside the 9:16 safe zone.
7. `montage.py` — the one fused encode + spec §16 preflight report.
8. `thumbnails.py` / `description.py` — 3 variants, RO description + hashtags.
9. `workers/tiktok_pipeline.py`, `routers/tiktok.py`, frontend wizard.

## Gotchas specific to this feature

```python
# The source clips have NO speech (music only) — faster-whisper yields nothing.
#   Facts about the video can ONLY come from the vision pass. If vision fails,
#   FAIL the step; never let the LLM invent materials/costs/locations (spec §7).
# ElevenLabs clamps speed to 0.7-1.2 — the spec's 1.30 is unreachable via API.
# ElevenLabs returns MP3; convert to WAV 48 kHz before the montage.
# 60 fps from a 24-30 fps source is frame duplication, not new motion (D8).
# tiktok_render is NOT in the doodle job lane — it is a full encode (D10).
# Every handler reloads project.json fresh before mutating (concurrent stages).
```

## Acceptance

Spec §21: paste link → download → see/select/mark frames → ~1200-char Romanian
script → ElevenLabs voice → auto-synced montage → captions → 3 thumbnails →
description → 1080×1920 @60 fps export → reopen the project without losing
progress → open the result and copy the description.
