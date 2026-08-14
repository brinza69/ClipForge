# Handoff — AI Stream Clipper, fourth session

Written 2026-08-14. Everything here was run on **this** machine and measured,
not carried over. Supersedes `handoff-clipper-session-3.md`, which it corrects
on one point (the branch had in fact been pushed).

Read `docs/story-engine.md` alongside this: it is the design document for the
reasoning work, this is the state-of-the-world.

---

## The one thing to read first: the dynamic editor is not wired in

**You asked where the self-editing went. It was never connected.**

`services/clipper/dynamic_edit.py`, `dynamic_cameras.py` and
`dynamic_render.py` are complete, tested (`tests/test_clipper_dynamic.py`) and
carry a lot of measured design work — shot grammar, camera rungs, cut
placement, hit flashes. Verified by grep: **nothing in `workers/` or
`routers/` imports any of them.** The only caller is
`scripts/render_dynamic_clip.py`, a standalone script.

The pipeline's export (`workers/clipper_render_jobs.handle_export`) calls
`services/clipper/render.render_clip` with a single static `layout_plan`. That
produces one fixed two-lane frame for the whole clip — facecam band on top,
gameplay below — with no cuts.

So every clip the pipeline has ever exported is a static split screen. The
multi-shot editor exists beside it, unreachable.

**This is the single biggest gap in the product** and it is not a missing
feature, it is a missing wire. Connecting it means giving `handle_export` a
path that builds a `dynamic_edit` plan instead of a `layout` plan and calls
`dynamic_render.render_dynamic_clip`. The hard part is already done; what is
missing is the per-window signal gathering that `render_dynamic_clip.py` does
inline (`analyse_window`, `region_motion`) and which would need to move into
the pipeline properly.

---

## Environment

Unchanged from session 3 except where noted. `F:\ClipForge`, branch
`claude/ai-stream-clipper`.

- **CUDA setup is automatic.** `CLIPFORGE_WHISPER_DEVICE` and the DLL PATH
  prelude are not needed — see session 3.
- **`opencv<5`** still matters.
- **`PYTHONPATH=F:\ClipForge\server`** for scripts.
- **PR #27 was merged** on 2026-08-10. Everything since is unmerged on the
  branch; open a new PR.

### The backend on port 8420 is not yours

Two uvicorn processes claim it and the winner runs on the **system Python**,
not the venv. A `Start-Process` of your own uvicorn fails to bind and you will
not notice — it logs `[Errno 10048]` and exits while the API keeps answering.
The job queue is shared through the DB, so anything enqueued races with that
stale worker. It cost session 3 a whole analysis run.

Run stages in-process instead. The harness is in the scratchpad pattern:

```python
class StubQueue:
    async def update_progress(self, job_id, progress, message=""): print(message)
    def is_cancelled(self, job_id): return False
    async def enqueue(self, **kw): return "stub"

await clipper_pipeline.handle_analyze("stub", pid, None, {}, StubQueue())
```

**On Windows this script MUST have `if __name__ == "__main__":`.** The
transcriber spawns its whisper worker with `mp.get_context("spawn")`, which
re-imports the calling module in the child. Without the guard the child re-ran
the entire pipeline from ingest and then died — and the error surfaced as "the
worker ran out of memory", which it had not.

---

## What was built this session

Twelve commits. In order:

| commit | what |
|---|---|
| `1b01a50` | transcriber asks CTranslate2 about CUDA, not torch |
| `c69c4a7` | menu-heavy windows rank lower |
| `5238cda` | every facecam found, from the faces not a border contour |
| `573e914` | Pass C split into three files |
| `36c3280` | three faults the first real export exposed |
| `2289a97` | three audio features that were pinned, not measuring |
| `7028937` | a re-score no longer re-proposes an exported moment |
| `3c81382` | LLM judges which moments are worth clipping |
| `0dac907` | sharpen the face band |
| `2b68b19` | the judging pass needs its own model |
| `60b4654` | reason from the payoff backwards (story_v1) |
| `1651e57` | comparative ranking, three perspectives |
| `d96dcb0` | semantic dedupe, archetype diversity |
| `acdee42` | promises and callbacks |
| `c18799a` | event atoms |
| `a72f1d6` | split the proposal half out of candidates.py |
| `642e530` | timeline retrieval, context debt from evidence |
| `af68b03` | narrative threads, and only the graph edges something reads |
| `cc450e9` | recall setups from inside the chunk too |
| `9df2a8e` | one bad chunk no longer costs the whole file |
| `341cfb8` | three faults a 4-hour run exposed |
| `f9ddeb6` | a layout plan records the frame it was measured in |

Test baseline: **421 passed, 2 failed** — the two `test_tiktok_transform.py`
failures are pre-existing and documented in CLAUDE.md (router not mounted).

---

## The recurring lesson, stated once

**On a source that is always busy, an absolute threshold measures nothing.**
This bit four separate times:

| feature | absolute bar | what happened |
|---|---|---|
| `audio_peak_ratio` | 1 peak / 8s | read **exactly 1.000 on all 57 windows** |
| `audio_dynamic_range` | `(top-min)/top` | sd 0.027 at a median of 0.998 |
| `game_ui_ratio` | would have been fixed | normalised against the source's own p25/p95 from the start |
| atom evidence marks | `peaks >= 1` | tagged **94%** of lines |

Every fix was the same shape: compare a moment against its own stream.

**A second lesson, also four times:** a structure nobody reads is not a
feature. `ARCHETYPE_SHAPE`, `context_debt` and `hook_latency` were all defined
before anything consumed them. Each was caught only by asking "what reads
this?" — do that before writing the next one.

---

## Reasoning: `reasoning_version = "story_v1"`

Full design in `docs/story-engine.md`. In one line: the old chain found
moments where something was loud; this one starts from the payoff and
reconstructs the earliest start that carries every fact the payoff needs.

Measured on `data/clipper/2d3375ee3420` (12-min slice):

| | heuristic | +LLM 0.5 | +LLM 0.7 | LLM only |
|---|---|---|---|---|
| the blast-furnace bit (good) | #45 of 46 | #14 | **#6** | #4 |
| "let's cook our food" (filler) | #2 | #6 | #8 | #13 |

Comparative ranking undid the score compression:

| | before | after |
|---|---|---|
| score range | 20.1 | **63.8** |
| stdev | 3.90 | **15.67** |
| story-built winners | 1 of 8 | **5 of 8**, five archetypes |

**Both switches are OFF by default.** `clipper_llm_select` and
`clipper_reasoning_version`. Turn them on per project in `clipper_settings`.

Cost, measured with tiktoken across 19 real transcripts (90–395 tokens/min):
**3.7 cents** for a 12-hour gaming stream, **11.1** for a talk-heavy one.
Frontier *output* dominates at $10/1M against $2.50 input, which is why the
judge asks for scores and eight-word reasons, never prose.

---

## The 4-hour run, and the five faults it found

12 minutes could not have surfaced any of these.

1. **Transcription lost the whole file to one bad chunk.**
   `faster_whisper.find_alignment` raised `IndexError: boolean index did not
   match indexed array` past the halfway mark of a 4-hour VOD. The iterator is
   LAZY, so the crash landed in the consuming loop, not in the wrapped call
   that created it. Two finished hours went with it. Fixed: `_tolerant` wraps
   the iterator; the result carries `failed_chunks`.

2. **The judge saw only the first 25 minutes.** `MAX_JUDGE_CLIPS = 80` sliced
   candidates in *timeline* order, so on a 4-hour source it judged the
   earliest 80 and left the rest unranked with their heuristic scores intact.

3. **The recall window was wrong at scale.** Setups were filtered to "before
   this chunk", but at this source's density a chunk holds five hours — so the
   callback feature was dead on any source under ~5.5 hours, the 12-minute
   slice included.

4. **Thread keywords were alphabetical.** `sorted(vocab)[:12]` returned the
   first twelve alphabetically, not the twelve that identify the arc.

5. **A layout plan carried no resolution.** Repointing a project from a 480p
   cut to the 1080p one and exporting produced garbage — a zoomed corner as
   the "facecam". An 854×480 plan *fits inside* a 1920×1080 frame, so a bounds
   check cannot tell it is wrong. Plans now record `src_w`/`src_h`.

### Timings, measured on 4 hours of source

| stage | time |
|---|---|
| ingest (proxy + audio) | 4 min |
| transcribe | ~33 min (7× realtime, GPU) |
| analyze | ~20 min |
| score (incl. 3 LLM passes) | 3 min |
| export per clip | 12–24 s |
| **total** | **~60 min** |

Roughly **one minute of processing per four minutes of stream**. A 12-hour
stream is about 3 hours. Face detection is capped at `MAX_FACE_SAMPLES = 2000`
so it does **not** grow with duration.

---

## Known problems, in priority order

### 1. The dynamic editor is not wired in
See the top of this file. Every export is a static split screen.

### 2. Region detection is global, and a long stream is not
On the 4-hour slice, `regions.json` found **one** facecam on a source that has
two. The 400 sampled frames are spread uniformly across four hours, and the
first hour is a gym-camera segment with a completely different layout. The
detection blends two formats and fits neither.

The 12-minute slice found both facecams correctly, so this is specifically a
long-stream problem: **layout is detected once, globally, for a source whose
layout changes.**

### 3. Captions collide with in-game UI
A caption landed over the Minecraft inventory panel. `resolve_position` does
implement collision avoidance, but `keep_out` comes from detected HUD/chat
rects and HUD detection returned `[]` on this source.

`docs/refs/style-spec.json` carries the rule the nine references produced:

> "Place the text centre in the largest vertical band that contains neither
> the facecam subject's head box nor the top of the game HUD, clamped to
> 55–75% of frame height."

The code honours the measured *position* (`CLIPPER_CAPTION_CENTER_PCT = 0.51`,
citing all seven captioned references) but not the rest of the rule: it nudges
±4% up to five times rather than searching for the largest clear band, and it
does not apply the 55–75% clamp.

**The profiles cannot fix this on their own.** They measure finished clips —
cut rate, caption geometry, colour, hook, loudness. None of them measures a
source stream; the spec says so itself: *"DO NOT SPEND A SHOT ON A DEAD REGION.
Not measurable from the nine (none contains gameplay)."* They say where to put
a caption, not what to avoid. Avoidance needs source detection.

**The concrete fix**: `game_ui_ratio` — the flat-mid-grey menu detector built
this session — already separates cleanly (gameplay 0.000–0.012, menus
0.478–0.697). It is currently temporal only, one value per hop. A **spatial**
version of the same measure would give the inventory's rectangle, which drops
straight into `keep_out`.

### 4. The facecam rect is too tall on this source
The detected webcam is 312px where the real inset is ~272, so the streamer's
stats bar bleeds into the face band. `_fit_inside` correctly stays inside the
*detected* rect — the rect itself is wrong.

### 5. Callback linking is validated only by mocks
No callback has ever fired on real data. The mechanism is tested end to end
against mocked answers, and on the 4-hour run the setups reached the model and
it linked none. It may be right to decline: "we're gonna give you diamond" is
not really paid off by mining one. **This needs a source with a confirmed
callback.**

### 6. Smaller
- `emotion` is flat (8% of the gaming profile, sd 4.1) because
  `laughter_score` is 0 on every window — Whisper does not transcribe laughter
  as "haha". Needs audio detection, not a word list. Do **not** drop the term:
  that would reward its absence.
- The learned ranker is complete and dormant — it needs 40 labelled clips.
- Real-ESRGAN: service written, binary not installed, not wired to the
  clipper. `unsharp` already took the cheap part of the gain.
- KaiCenat's facecam left edge reads 9px short. Four edge-picking rules were
  scored against every edge whose truth is known; nothing fixes it and two of
  them break another edge. **Do not re-litigate without a new signal.**

---

## Not built, from the upgrade spec

**P2** — multimodal Pass D (§21–22), diarization (§23), pairwise human
feedback (§26), boundary-learning dataset (§27).

**P3** — game-specific detectors (§24), post-publish metrics (§28), advanced
learned ranker.

**Unprioritised** — frontend inspection UI (§34; all the data is in the
`clips.reasoning` column already, nothing displays it), golden cases (§36).

---

## What I would do next

1. **Wire the dynamic editor into export.** Biggest gap, and the work is
   mostly moving `render_dynamic_clip.py`'s per-window analysis into the
   pipeline.
2. **Region detection per segment**, not once globally. A long stream changes
   layout and nothing notices.
3. **Spatial `game_ui_ratio`** → caption keep-out, plus the missing 55–75%
   clamp from the style spec.
4. **Watch three clips.** Every measurable property has been checked; the
   subjective ones — does the opening hold you, does the pace work — have not,
   and no script can.

---

## Traps

Everything in sessions 2 and 3 still holds. Add:

- **Never use PowerShell `-replace` on a source file.** `Get-Content -Raw`
  reads as ANSI and `Set-Content -Encoding utf8` writes UTF-8; every box
  character becomes mojibake. Use the editor.
- **A here-string after `;` in a compound PowerShell command gets mangled.**
  Write commit messages to a file and use `git commit -F`.
- **`sentences_from_words` returns index ranges (`i0`/`i1`), not word lists.**
  Reading a `words` key off it silently produced an empty atom for every
  sentence.
- **`_write_clips` preserves exported clips** across a re-score, deliberately.
  Duplicate windows in the clip list after several runs are that, not a bug —
  but they also polluted three separate measurements this session. Clear them
  before comparing boards.
