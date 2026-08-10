# Handoff — AI Stream Clipper, second session

Written 2026-08-10. Everything below was run on **this** machine and checked, not
carried over from a previous document. The handoff this replaces
(`handoff-dynamic-edit.md`) is still accurate about the code it describes but its
environment section is wrong for any machine but the one that wrote it — read
§Environment here first.

## Environment — read before anything else

The last handoff cost a session to path errors. The facts, verified:

| | |
|---|---|
| Repo | `F:\ClipForge` — **there is no D: drive on this machine** |
| Branch | `claude/ai-stream-clipper`, **11 commits ahead of origin, never pushed** |
| Backend venv | `F:\ClipForge\server\.venv` — full `requirements.txt` installed |
| Frontend | `npm run dev` on :3000 (`.claude/launch.json` → `clipforge-web`) |
| Backend | `uvicorn main:app --port 8420` |
| Review page | `.claude/launch.json` → `clipforge-review` serves a project's `dynamic/` on :8777 |
| GPU | RTX 2080 SUPER 8GB. CLAUDE.md says RTX 3060 — that is a different rig. |

Three things must be set or the pipeline fails in ways that look like bugs:

```powershell
$sp = "F:\ClipForge\server\.venv\Lib\site-packages"
$env:PATH = "$sp\nvidia\cublas\bin;$sp\nvidia\cudnn\bin;$env:PATH"
$env:CLIPFORGE_WHISPER_DEVICE = "cuda"
```

1. **`CLIPFORGE_WHISPER_DEVICE=cuda` is mandatory.** `whisper_device: "auto"` resolves
   through `torch.cuda.is_available()`, and the installed torch is `2.13.0+cpu` —
   pulled from PyPI by `kokoro`, which needs no CUDA. faster-whisper does not run
   on torch at all; it runs on CTranslate2, which sees the GPU fine. Auto-detection
   asks the wrong library and silently gives you CPU. **This is a real bug, not
   just a setup step** — see Open problems.
2. **The CUDA DLLs must be on PATH.** `nvidia-cublas-cu12` and `nvidia-cudnn-cu12`
   are installed but Windows does not search `site-packages`. Without them
   transcription dies with `cublas64_12.dll is not found` — after loading the model
   successfully, which makes it look like the GPU works when it does not.
3. **OpenCV must stay below 5** (now pinned). OpenCV 5 dropped `CascadeClassifier`
   from the top-level module and `signals.face_presence` dies mid-analyse.

Scripts derive their data dir from their own location now; `CLIPFORGE_DATA_DIR`
overrides. Run them with `PYTHONPATH=F:\ClipForge\server`.

There is **no `Workflow` tool** in Claude Code. Fan-out is the `Agent` tool, which
has no run-id resume — have each agent write its result to disk before finishing,
and restart only the ones with no file.

## Test data already on disk

| path | what |
|---|---|
| `data/testclip/hd.mp4` | 14 GB, the full 12h VOD at **1080p60** |
| `data/testclip/full.mp4` | 3 GB, same VOD at 480p30 |
| `data/testclip/gameplay.mp4` | 1.1 GB, 12-min 1080p60 slice from 6h30 |
| `data/clipper/2d3375ee3420/` | project on that slice, `ready`, 1920x1080@60 |
| `data/clipper/0c9685df852b/` | project on a 480p slice, `ready` (a workout segment, no gameplay) |

Source: `https://www.youtube.com/live/Cb5Cde_D5SM` — IShowSpeed, *MINECRAFT HARDCORE
ALL BOSSES DAY 2 ft. KaiCenat*, 11h55m, 1920x1080@60.

**Do not use `yt-dlp --download-sections` on this URL.** It hands the job to ffmpeg
with `-ss` before `-i` on a remote stream; measured, ffmpeg used 0.0 seconds of CPU
in 20 seconds and wrote nothing. Download the whole file with the native downloader
(`-N 8`, ~26 MB/s) and cut locally, where a seek is instant.

**Transcode when slicing.** The source is AV1 and OpenCV cannot decode it, so the
analysis passes see nothing. `-c:v h264_nvenc -preset p5 -cq 20` cuts 12 minutes in
about two minutes.

The stream's layout, which matters for the band: **two facecams**, IShowSpeed
top-left and KaiCenat top-right, gameplay between them, chat down the left edge,
"BOSSES BEATEN" and a timer bottom-left, hotbar bottom-centre.

## What was done

Eleven commits, oldest first:

| commit | what |
|---|---|
| `addf646` | caption entry pop, opt-in, verified on a render |
| `0920f48` | the nine reference profiles, measured, in `docs/refs/` |
| `d052220` | recipe reconciled with them |
| `1a4b0ac` | `docs/refs/style-spec.json` — the nine synthesised, 14 disagreements kept |
| `9636274` | shot grammar 1.25/2.40 → **1.80/3.00**, caption centre 43.75% → **51%**, spring pop variant |
| `c0b0d44` | third face rung `face_medium`; `dynamic_edit.py` split into `dynamic_cameras.py` |
| `c1b886e` | gameplay cut guarded on detail as well as motion |
| `2939d18` | clips no longer end one word into a phrase |
| `d5edec4` | opencv pinned below 5 |
| `4b42116` | face detection 8/98 → 44/98 samples; band clears every facecam, not one |
| `3579ba4` | gameplay camera no longer cuts to open menus |

Everything measured rather than asserted:

- The reference cut-rate table in recipe §1 was **wrong on five of nine clips**, twice
  catastrophically (`uRU9SzlVClg` 3.5 → 31.5 cuts/min, `-dHfHZgtXJw` 0 → 24.7). Both
  failures are ours: a jump cut on a locked-off stream leaves the background
  pixel-identical, so scene detection is blind to it — **it will not see our renders
  either**. `docs/refs/README.md` has the corrected table and the methods that worked.
- The caption pop is real and **three of seven captioned references** run it at
  ~1.05 peak at exactly +83 ms. `_pop_tags` matches `uRU9SzlVClg` to three decimals.
  Recipe §3 credited the wrong clip; the numbers were right.
- `push_amount = 0` is correct **for our source type**, not by vote: the two references
  that are locked-off stream captures measure s=1.0000 with no ramp; every clip built
  another way ramps continuously, from -3.2%/s to +46%/s.
- Clip endings: 2/6 → **6/6** end on a word a speaker can stop on.
- Menus in frame: **6/18 → 1/18**.

## Open problems

**1. The second facecam is still missed.** Detection improved 5.5×, but KaiCenat is
turned away for most of the tested window and three sightings do not clear the
persistence bar, so his facecam stays inside the "gameplay" band. The real fix is
detecting the **inset** — a static bordered rectangle, identical in every frame —
rather than the face inside it. That is robust to where anyone is looking.

**2. The UI signal is in the wrong place.** It rejects menu shots correctly, but with
the game showing a menu for most of that window the clip became almost entirely
facecam, which loses the cross-cutting the style exists for. A window whose game
region is mostly UI should rank **lower as a candidate**, before it reaches the shot
planner. `game_ui_above` currently only gates shots.

**3. Whisper device auto-detection is broken.** `transcriber.py` asks
`torch.cuda.is_available()` to decide, and torch is CPU-only here. It should ask
CTranslate2 (`ctranslate2.get_cuda_device_count()`), which is what actually runs the
model. Right now every install silently falls to CPU — ~12 h for this VOD instead of
~80 minutes.

**4. `regions.json` still reports `webcam: null`** on a gameplay stream, and classified
this one as `gaming` at only 0.464 confidence. `render_dynamic_clip.py` works around
it by computing its own face track; the pipeline's own detection is untouched.

**5. `candidates.py` is 778 lines** against the 500-line limit in CLAUDE.md. It was 702
before this session, so the overage is inherited, but it grew and needs splitting.

**6. Nothing is pushed.** Eleven commits live only on this machine.

## Things that will waste your time if you do not know them

- `segments.json` in a project's `analysis/` is **candidate windows, not the
  transcript** — they overlap by design. The transcript is in SQLite at
  `data/db/clipforge.db`, table `transcripts`. Reading the wrong one cost this
  session two rounds of false conclusions about duplicate words.
- `ffmpeg -ss <t> -i` is **not frame-accurate** on these AV1 files; it silently returns
  before/after pairs from the same shot. Use `select='eq(n,K)'`.
- ffmpeg's `drawtext` **segfaults** on this box (font resolution). Use `drawbox`.
- Project settings must be PATCHed nested under `clipper_settings`; a flat body
  returns 200 and changes nothing.
- The clipper's own duration cap is 12 h (`clipper_max_source_duration_s`), and this
  VOD is 11 h 55 m — it fits with five minutes to spare.

## Verification status

Everything in this session was verified **on a render or a measurement**, except
where noted. What has *not* been checked: no clip has been exported through the
pipeline's own `clipper_export` path (only `scripts/render_dynamic_clip.py`), and the
review page has only been built for the 480p project, not the gameplay one.
