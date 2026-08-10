# Handoff — AI Stream Clipper, third session

Written 2026-08-10, mid-run: the session stopped on credits, not on a problem.
Read `handoff-clipper-session-2.md` §Environment first — it is still correct
about the machine, except where this file says otherwise.

## Where it stopped

**Five of the six open problems from session 2 are fixed and committed**, the
end-to-end run is done, and the export path has been exercised for the first
time. The sixth problem (nothing pushed) is yours to decide.

## Commits this session (5, on `claude/ai-stream-clipper`)

| commit | problem | what |
|---|---|---|
| `1b01a50` | #3 | transcriber asks CTranslate2 about CUDA, not torch |
| `c69c4a7` | #2 | menu-heavy windows rank lower |
| `5238cda` | #1, #4 | every facecam found, from the faces not a contour |
| `573e914` | #5 | Pass C split into three files |
| `36c3280` | — | three faults the first real export exposed |

`main` is **43 commits behind this branch**, still never pushed.

## Do not trust the backend on port 8420

It is not started by this work and it runs whatever code it was launched with.
Two uvicorn processes claim that port, and **the one that wins is running on
the system Python (`C:\Users\vlado\AppData\Local\Programs\Python\Python311`),
not the project venv.** A `Start-Process` of your own uvicorn will fail to
bind and you will not notice — it logs `[Errno 10048]` to stderr and exits
while the API keeps answering.

Worse, the **job queue is shared through the DB**, so anything enqueued via
`POST /analyze` is a race with that stale worker. It cost this session a full
analysis run that silently produced nothing: the artifacts came back byte for
byte identical and it took a process listing to work out why.

Run pipeline stages in-process instead. The pattern that worked:

```python
class StubQueue:
    async def update_progress(self, job_id, progress, message=""): print(message)
    def is_cancelled(self, job_id): return False
    async def enqueue(self, **kw): return "stub"      # swallow it

await clipper_pipeline.handle_analyze("stub", project_id, None, {}, StubQueue())
```

That runs the same handler the worker would, with no queue and no server.
`handle_export` takes the clip id as its third argument.

## What the first real export exposed

Three faults, all found by looking at the file rather than the code. All three
are fixed in `36c3280`; the detail is in that commit message. The short form:

1. **The gaming layout was unreachable in production.** `plan_layout` sniffed
   the content type out of the candidate dict, and neither pipeline caller put
   it there. Every gaming export was a tracking crop with no face band.
2. **The face band reached outside the facecam** and sliced the stats bar
   underneath it in half.
3. **`speech_ratio` measured the noise floor**, reading 94% on a source whose
   transcript covers 34%.

Numbers worth keeping: on this source the whole 57-clip field scores between
**48.2 and 62.2** — a 14-point spread with a stdev near 3. The ranking barely
discriminates, and that is a bigger problem than any single feature. Fixing
`speech_ratio` widened `clarity`'s spread from 12.96 to 18.39 but moved
`overall` almost not at all, because the gaming profile weights clarity at
3/100. **If you want the ranking to mean something, the profile weights and
the sub-score dynamic ranges are where to look, not another feature.**

Re-scoring a project **keeps clips that were already exported** and writes the
new set alongside them (`_write_clips`, deliberate and documented). Duplicate
windows in the clip list after several runs are that, not a bug.

## Environment — two things session 2 called mandatory are now automatic

`CLIPFORGE_WHISPER_DEVICE=cuda` and the CUDA-DLL PATH prelude are **no longer
needed**. `services/transcriber.py` resolves `auto` through
`ctranslate2.get_cuda_device_count()` and puts the site-packages nvidia bin
dirs on PATH itself. Verified: a clean process with neither set loads
large-v3 on the GPU in 3.6s. Setting them by hand still works and costs
nothing.

Everything else in session 2's environment table holds. `opencv<5` still
matters. Run scripts with `PYTHONPATH=F:\ClipForge\server`.

## What was measured, and what it overturned

Session 2's problem list was right about the symptoms and wrong about two
causes. Both corrections are worth keeping:

- **"The second facecam is still missed."** It is not. Over the whole 12-minute
  proxy the tuned detector finds the left facecam in 33 of 120 samples and the
  **right one in 37**, against 10 false positives. Session 2 measured a short
  window and generalised from it. The real fault was elsewhere (below).
- **`faces.json` on disk was stale** — 19 boxes over 360 samples, not one of
  them in a facecam, written before commit `4b42116`. Any conclusion drawn from
  a project's on-disk `faces.json` without re-running analysis is worthless.

### The actual cause of `webcam: null`

Two independent faults, both measured on the 40 sampled frames of
`data/clipper/2d3375ee3420`:

1. **There were two face detectors.** `signals.face_presence` ran a tuned pair
   of cascades with `equalizeHist`; `content_type` ran its own untuned one. The
   untuned one found **0 faces in 40 frames** where the tuned one finds 27 with
   a single false positive. There is now one: `signals.detect_faces`.
2. **`_find_webcam` had the pair inverted.** It searched for a rectangular
   border contour and then asked whether a face sat inside it. That pass
   produced 10 candidate rects over 40 frames, **nine of them seen in a single
   frame** — there was never a stable rectangle for a face to be inside of. It
   also demanded a face in half the frames; the two real facecams manage 14 and
   13 of 40 and could never have cleared it.

So the faces now seed the search and the frame-averaged gradient supplies the
bounds. **Averaging before thresholding is the whole trick** — a persistence
map of per-frame Canny found no usable border lines at all, because on a
compressed 480p proxy the edge flickers by a pixel while the underlying
gradient does not. Four probe approaches failed before this one; don't redo
them:

| tried | result |
|---|---|
| contours on a Canny-persistence map | 189 fragments, facecam interior is full of persistent edges too |
| Canny on the temporal-variance map | found the Minecraft inventory grid, not the layout |
| morphological line extraction on persistence | one horizontal line in the whole frame |
| "static border, live interior" | backwards — the facecam is LOWER variance than the gameplay |

Recovered against ground truth read off the frames:

| | detected | truth |
|---|---|---|
| IShowSpeed, top-left | `(0, 0, 122, 68)` | `(0, 0, 122, 69)` |
| KaiCenat, top-right | `(364, 8, 82, 60)` | `(355, 5, 93, 63)` |

The left one is pixel-accurate. The right one's **left edge is ~9px short**
because the true step there is weak (dark facecam against dark gameplay) and a
lamp inside the inset at x=364 wins the argmax. 9px on a 480 proxy is 36px on
the source — a shoulder. Left alone deliberately: chasing it risks over-fitting
to one stream.

`regions.json` now carries `webcams` (all of them, best first); `webcam` stays
as the best one. Chat/HUD exclusion and the gameplay crop clear every facecam —
the second one used to land in `regions.json` as a **HUD rect**.

### The menu signal

`motion_timeline` now also emits `ui`, the flat-mid-grey share per hop, from
the decode pass it already runs. Free: 1441 samples over the 12-minute proxy in
6 seconds. Measured against the 192x54 gameplay-band scale
`render_dynamic_clip` scores shots with, the coarse 64x36 whole-frame grid
correlates at **r = 0.993**.

`game_ui_ratio` is normalised against the source's **own** p25/p95, not a fixed
threshold — what share of a frame is flat grey depends on how much permanent
chrome a layout carries. On the co-stream: clean gameplay windows 0.000-0.012,
menu stretches 0.478-0.697. Menu stretches sit at 200-240s, 380-420s, 480s and
680-720s.

## Verified end to end

Analysis re-run in-process on `2d3375ee3420` with current code:

| artifact | before | after |
|---|---|---|
| `regions.webcams` | `null` | both facecams, conf 0.912 / 0.703 |
| `regions.hud` | `(360,0,120,44)` — the right facecam | `(420,0,60,44)` — the actual overlay |
| `faces.json` | 19 boxes, none in a facecam | 273 boxes: 99 left, 123 right, 51 stray |
| `motion.ui` | absent | 1441 samples, p25 0.020 p95 0.161 |
| winners' layout | n/a | 9 of 10 `face_top_game_bottom` |

The menu penalty works and is correctly sized: menu-heavy clips now average
**55.1 overall against 56.1** for clean ones, having previously scored HIGHER
(a streamer in an inventory screen is talking, so hook, clarity and caption
suitability all reward it). One clip over 0.5 menu share sits in the top ten.

**A caution about how that was nearly mis-measured.** The first check drew the
menu stretches by hand off a 20-second bucket plot and concluded 7 of the top
10 were menu-heavy. That was wrong — the hand-drawn ranges smear, and a clip
they scored at 0.93 measures 0.295 against the raw series. Take ground truth
from `signals.motion.ui` directly (share of hops over ~0.09, the midpoint
between the two measured modes), never from a plot.

Three clips exported through the real `clipper_export`: 1080x1920, 60 fps,
yuv420p, AAC 48 kHz, ~5.9 Mbps, and the render is a single encode as intended.

## What to do next

1. **Look at the ranking's dynamic range.** 48.2 to 62.2 across 57 clips is
   the biggest remaining problem and no individual feature will fix it.
2. **Caption coverage is 37% of clip length** where the transcript has words
   for 34% — those agree, so captions are not dropping text. But 64 wpm over
   the whole VOD is low for this streamer; whether Whisper is losing speech
   under game audio is unmeasured and worth an hour.
3. **KaiCenat's facecam left edge is ~9px short.** See below.
4. **Push, or decide not to.** 43 commits, one machine.

## Known-unfinished, deliberately

- **`ranker.FEATURE_ORDER` does not include `game_ui_ratio`.** It is a separate
  frozen 22-key tuple from `candidates.FEATURE_KEYS` (now 60), and adding to it
  needs a `MODEL_VERSION` bump. There is no trained model — the ranker needs 40
  labelled clips and has none — so adding an untrainable feature would be
  speculative. Add it when there is feedback to train on.
- **KaiCenat's left edge, 9px short.** See above.
- **Nothing is pushed.** 42 commits, one machine.

## Traps, still true

Everything under session 2's "Things that will waste your time" still holds.
Add these:

- **Do not use PowerShell `-replace` on a source file.** `Get-Content -Raw`
  reads as ANSI and `Set-Content -Encoding utf8` writes UTF-8, so every box
  character in the file turns to mojibake. It cost a `git checkout` this
  session. Use the Edit tool.
- **A here-string after a `;` in a compound PowerShell command gets mangled.**
  Write commit messages to a file and use `git commit -F`.
- The two `test_tiktok_transform.py` failures are expected — the router is not
  mounted, as CLAUDE.md says. Baseline is **280 passed, 2 failed**.
