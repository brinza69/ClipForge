# AI Stream Clipper — Runbook

How to run, tune and debug the feature. Companion to
`plans/ai-stream-clipper-architecture.md` (design) and
`plans/ai-stream-clipper-repository-audit.md` (why it is built on the existing
`clips` table).

---

## 1. Start it

```bash
cd server && .venv/Scripts/python -m uvicorn main:app --host 127.0.0.1 --port 8420
```

```bash
npm run dev
```

Then open <http://localhost:3000/ai-stream-clipper>.

`WORKER_URL_INTERNAL` in `.env.local` must match the port uvicorn actually
binds. When it does not, every `/worker-api/*` call returns an opaque 500 that
reads "Internal Server Error" — `src/lib/api-error.ts` special-cases that
message and says so explicitly.

## 2. The flow

1. Paste a URL → **Check**. This calls `POST /api/clipper/preview`, which runs
   the URL policy check and then yt-dlp metadata **without downloading**.
2. Pick a clip length and a count, tick the rights box, **Create project**.
3. **Start analysis** on the project page.

   Or tick **"Just make the clips — don't wait for me"** in step 2 and skip
   steps 3 and 5 entirely: the analysis starts on creation and the top N render
   as soon as scoring finishes (`auto_export` in `clipper_settings`, 0 = off).
   Everything still appears on the board; it is already rendered when you get
   there. Alternatives are never auto-rendered — they exist so a person can
   compare two cuts of one moment.

   The second box, **"Have a model look at each finished clip"**, turns on Pass
   D's vision half (`vision_review`). It is the only setting in ClipForge that
   spends money — about 0.4¢ a clip on `gpt-5.6-terra` — and it is disabled
   until an OpenAI key is saved in Settings. It reports; it never deletes.
4. Watch the stage list. Leaving the page is safe — progress lives in the
   `jobs` table, not in the browser.
5. Review the ranked candidates, click a score to see the 16 sub-scores,
   approve/reject, then **Export** to render 1080×1920.

## 3. What runs, and roughly how long

| Stage | Job type | Lane | Cost driver |
| --- | --- | --- | --- |
| download + proxy + audio | `clipper_ingest` | heavy | source size and your connection |
| transcription | `clipper_transcribe` | heavy | **the dominant cost** — faster-whisper `medium`; on CPU this is ~1× realtime by design |
| signals + regions + content type | `clipper_analyze` | light | proxy length; bounded frame sampling |
| segment → candidates → score → dedupe → layout → captions | `clipper_score` | light | seconds |
| render one clip | `clipper_export` | heavy | one ffmpeg encode at crf 18 |
| preview render | `clipper_preview` | light | seconds, 540×960 |

**A 6-hour VOD is an overnight job**, almost entirely because of
transcription. Check `/api/transcript/device` first: if CUDA silently fell back
to CPU, that stage goes from tens of minutes to many hours. The endpoint
reports `fell_back_to_cpu` precisely so this is visible.

## 4. Concurrency — the thing that will confuse you first

The queue has two lanes:

* **heavy** — bounded by `CLIPFORGE_MAX_CONCURRENT_JOBS`. Holds
  `clipper_ingest`, `clipper_transcribe`, `clipper_export`, and every existing
  remix/parallel pipeline job.
* **light** — fixed at 2. Holds `clipper_analyze`, `clipper_score`,
  `clipper_preview` alongside the doodle/tiktok wizard steps.

If `CLIPFORGE_MAX_CONCURRENT_JOBS=1` (this rig's default in some shells) and a
`parallel_pipeline` run is going, a clipper job sits in `queued` showing 0%
with an empty message. **That is correct behaviour, not a hang.** Confirm with:

```bash
curl "http://127.0.0.1:8420/api/jobs/?status=running,queued"
```

Raise the limit to 2 if you want a clipper download to proceed alongside other
work — the download is network/disk bound and does not contend for the GPU.
Note that restarting the backend to change it will requeue any in-flight job
(startup recovery does this deliberately), so an OCR or render stage restarts.

## 5. Tuning

Everything is `CLIPFORGE_`-prefixed; see `.env.example` for the annotated list.

| Symptom | Setting |
| --- | --- |
| "Source is 6.3 h long, the limit is …" | `CLIPPER_MAX_SOURCE_DURATION_S` (default 12 h) |
| Clips too short/long | the length preset in the form, or `CLIPPER_MIN_CLIP_S` / `_MAX_CLIP_S` |
| Too many near-identical clips | lower `CLIPPER_OVERLAP_THRESHOLD` / `CLIPPER_TEXT_SIMILARITY_THRESHOLD` |
| Analysis too slow on a long VOD | lower `CLIPPER_PROXY_WIDTH` / `CLIPPER_MAX_SAMPLED_FRAMES` |
| Facecam detection wrong | override the layout per clip, or the content type on the project |
| Want LLM-written headlines | set `CLIPPER_LLM_ENGINE` to `ollama` / `openai` / `anthropic`. Blank = the pass is skipped and headlines come from deterministic extraction. The pipeline never fails because this is unset. |
| Export quality | `CLIPPER_EXPORT_CRF` (lower = better), `CLIPPER_EXPORT_PRESET` |

## 6. Debugging

**Artifacts.** Everything the analysis produced is on disk under
`data/clipper/{project_id}/`:

```
proxy/proxy.mp4      the 480p file every analysis pass actually reads
audio/speech.wav     16 kHz mono, what whisper got
analysis/signals.json    audio RMS/peaks/silence, scene cuts, motion, faces
analysis/regions.json    detected webcam / gameplay / chat / HUD rectangles
analysis/segments.json   Pass B semantic windows
analysis/candidates.json pre-dedupe candidates with their full feature vectors
analysis/meta.json       versions, timings, content-type verdict
```

Also readable over HTTP:
`GET /api/clipper/projects/{id}/artifacts/{signals|regions|segments|candidates|meta}`
(the name is allowlisted — traversal is rejected).

**Common failures**

| Message | Meaning |
| --- | --- |
| `private_address` / `blocked_port` | the URL guard refused an internal address. Intentional: the backend binds `0.0.0.0` with no auth and must not be usable as a LAN proxy. |
| `source_too_long` | over `CLIPPER_MAX_SOURCE_DURATION_S`. |
| `The transcript came back empty` | no speech, or a silent audio track. Check `audio/speech.wav`. |
| `No semantic segments could be built` | too short, or almost no speech. |
| `The source video is no longer on disk` | the media was cleaned up; re-run analysis. |
| ffmpeg errors during export | check the layout plan — `warnings` on the clip usually names the cause (tiny facecam, degenerate crop). |

**Retry is cheap.** `POST /api/clipper/projects/{id}/retry` inspects which
artifacts exist and resumes at the furthest completed stage, so a scoring crash
does not re-download the VOD.

## 7. Scores, honestly

The number on a card is a **rank score**: a weighted blend of 16 heuristic
sub-scores, weighted by a per-content-type profile. It orders clips *within one
source*. It has **not** been validated against real performance data, and it is
not a prediction. Click it to see every component and the reason string.

The learned ranker is opt-in by evidence: it trains from your approve / reject /
export history and is only used once there are at least 40 labelled clips **and**
it beats the heuristic on held-out NDCG@5. Until then `ranker_version` reads
`heuristic-1`. Train it with `POST /api/clipper/ranker/train`; check status with
`GET /api/clipper/ranker`.

## 8. Privacy and limits

* Everything runs locally **except what you switch on**. Outbound calls, in
  full:
  * yt-dlp fetching the source;
  * an LLM for headlines, if `CLIPPER_LLM_ENGINE` is set — sends transcript text;
  * the anchor and judge passes, if `llm_select` is on — sends transcript text;
  * **the vision review, if `vision_review` is on — sends six JPEG frames OF
    YOUR VIDEO to OpenAI per exported clip.** That is the only setting that
    uploads picture rather than text, and it is off by default.
* Full transcripts are never logged.
* There is **no authentication anywhere in ClipForge** — it is a single-user
  local studio bound to `localhost:3000` via CORS. Do not expose port 8420 to a
  network you do not control. The URL guard limits the damage of the fetch path
  but is not a substitute for not exposing the port.

## 9. Not built

Named so silence is not mistaken for completeness: speaker diarisation and
diarisation-driven interview split-screens, natural-language moment search,
OCR timelines (killfeed/scoreboard), live chat-replay ingestion, intro/outro
templates, and automated platform metric scraping. Performance metrics are
attached by hand via `POST /api/clipper/clips/{id}/performance`.
