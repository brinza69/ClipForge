# Handoff — AI Stream Clipper, third session

Written 2026-08-10, mid-run: the session stopped on credits, not on a problem.
Read `handoff-clipper-session-2.md` §Environment first — it is still correct
about the machine, except where this file says otherwise.

## Where it stopped

**Five of the six open problems from session 2 are fixed and committed.** The
sixth (nothing pushed) is yours to decide.

An end-to-end run had just started and was **not** finished:

```
POST /api/clipper/projects/2d3375ee3420/analyze  ->  job e7cb92e465a4
```

A backend was left running on port 8420, started with:

```powershell
cd F:\ClipForge\server; .\.venv\Scripts\python.exe -m uvicorn main:app --port 8420
```

It is almost certainly gone now. **Restart it and re-run the analyze call** —
the job is idempotent and the project's artifacts are still on disk. The old
`regions.json` was copied to the scratchpad before the run, but the scratchpad
is session-scoped and will not survive; `git` has everything that matters.

## Commits this session (4, on `claude/ai-stream-clipper`)

| commit | problem | what |
|---|---|---|
| `1b01a50` | #3 | transcriber asks CTranslate2 about CUDA, not torch |
| `c69c4a7` | #2 | menu-heavy windows rank lower |
| `5238cda` | #1, #4 | every facecam found, from the faces not a contour |
| `573e914` | #5 | Pass C split into three files |

`main` is now **42 commits behind this branch**, still never pushed.

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

## What to do next, in order

1. **Restart the backend and re-run analysis on `2d3375ee3420`.** Everything
   below depends on artifacts that run produces. Watch for it to finish; it
   took under 10 minutes last time it ran end to end.
2. **Check the menu penalty actually re-ranked anything.** Compare the top
   candidates' `start` times against the menu stretches above. The penalty is
   worth ~7 points of `overall` on the gaming profile (visual_energy 79 -> 20 at
   weight 12/100). If nothing moved, the penalty is too small — say so with the
   numbers rather than just turning it up.
3. **Export a clip through `POST /api/clipper/clips/{id}/export`.** This has
   **never been run**. Session 2 only ever rendered through
   `scripts/render_dynamic_clip.py`. It is the largest untested surface in the
   feature.
4. **Watch the export and fine-tune.** This is what the user actually asked
   for and it is the part that did not happen.

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
