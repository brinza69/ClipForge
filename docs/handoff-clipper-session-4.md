# Handoff — AI Stream Clipper, sessions 4 and 5

Session 4 written 2026-08-14, session 5 appended 2026-08-16. Everything here
was run on **this** machine and measured, not carried over.

**This is the state of the world.** For what every file is and where it lives,
read `clipper-map.md` — it exists so nobody has to grep the tree again.

## Contents

**Current state**
- [Built and OFF — the switches](#built-and-off--the-switches-and-what-is-behind-each) — three features exist and do nothing until turned on
- [Session 5 — what shipped](#session-5--2026-08-16-what-shipped)
- [What I would do next](#what-i-would-do-next--all-but-one-done-on-2026-08-16)
- [Not built, from the upgrade spec](#not-built-from-the-upgrade-spec)

- [What is fragile](#what-is-fragile--2026-08-17) — where it is most likely to be
  wrong without saying so, ranked

**Known problems**, in priority order — [jump](#known-problems-in-priority-order)
1. ~~The dynamic editor is not wired in~~ — FIXED
2. Region detection is global — HALF FIXED, and the other half is understood
3. Captions collide with in-game UI
4. The facecam rect is too tall — measured
5. Callback linking is validated only by mocks
6. ~~Every clip ends on the last word~~ — FIXED
7. ~~`context_debt` decided by one unverified string~~ — FIXED
8. A portrait facecam cannot be detected — measured, six approaches failed
9. The classifier called a gaming stream `talking_head` — improved, not solved
10. Smaller

**Measurements worth not repeating**
- [The recurring lesson](#the-recurring-lesson-stated-once) — an absolute threshold on a busy source measures nothing
- [The 4-hour run and the five faults it found](#the-4-hour-run-and-the-five-faults-it-found)
- [Facecam detection scored against the labels](#facecam-detection-scored-against-the-labels--2026-08-16) — 7/8, and two more rules rejected
- [The first human judgement of a dynamic clip](#the-first-human-judgement-of-a-dynamic-clip--2026-08-16)

**Environment and traps**
- [Environment](#environment) — including why the backend on 8420 is not yours
- [Traps](#traps) — the things that will cost you an hour

Older handoffs are history: `handoff-clipper-session-3.md`,
`handoff-clipper-session-2.md`, `handoff-dynamic-edit.md`. They are still
accurate about the code they describe.

---

## The one thing to read first: the dynamic editor is not wired in — FIXED 2026-08-16

**Left as written because the diagnosis is the useful part.** It was fixed on
2026-08-16: `dynamic_window.py` carries the per-window analysis into the
pipeline and `handle_export` has a multi-shot branch, off by default. What
follows is what the problem was.

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

### A backend without `--reload` is running yesterday's code

`.claude/launch.json` started uvicorn without `--reload` until 2026-08-17, and
that cost the same twenty minutes twice in one session. Both times the symptom
was a NEW setting arriving at the API as `None` — first `auto_export`, then
`vision_review` — which looks exactly like the `_normalise_settings` whitelist
bug that had just been fixed, and is not it. The server simply had no idea the
key existed.

`--reload` is in the config now. If a setting you just added comes back `None`,
check that before touching the code: restarting the API is cheaper than
re-diagnosing a bug you already fixed.

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

## Built and OFF — the switches, and what is behind each

Everything here EXISTS, is tested, and does nothing until it is turned on.
Written down because a feature that ships off is the easiest kind to forget,
and this repo has lost work to exactly that six times over.

All three are per-project keys in `clipper_settings`, each falling back to a
`config.py` default:

**One of the three is no longer off.** `dynamic_edit` was turned ON on
2026-08-17 by the owner, after a viewer judged the edit and an A/B settled the
one fault they named. The row below still describes what it does; only its
default changed. The other two remain opt-in.

| switch | config default | what it turns on |
|---|---|---|
| `dynamic_edit` | `clipper_dynamic_edit = True` (2026-08-17) | Multi-shot export. Plans a shot list and cuts between camera framings instead of rendering one fixed split screen. Verified on a real export: 9 shots, 6 hit flashes, 21s to render an 18s clip. Falls back to the static layout on a missing proxy, a plan with fewer than two shots, or any exception. |
| `trim_silence` | `clipper_trim_silence = False` | §15 dead-air removal. Cuts wordless silences out of the MIDDLE of a chosen window, in the same encode, and moves the captions with them. Measured on 920 candidates: 298 get a cut, ~3.1s each. |
| `llm_select` + `reasoning_version` | `False` / `"legacy"` | The story engine. Anchors reasoned back from the payoff, comparative ranking, three judge perspectives. |

`trim_silence` deserves its own note: it sat in the settings dict from the day
the clipper shipped with **nothing reading it**, defaulted to `True`, and every
export ignored it. Its default is now `False` and it is wired — so the change
that made it real also made it stop claiming to be on.

Two things are visible without any switch, because neither changes a
deliverable: the reasoning panel (the "why" button on a clip, §34) and the
transcription progress message.

## The first human judgement of a dynamic clip — 2026-08-16

Someone watched one. Until now every property of the multi-shot edit had been
measured and none had been judged.

**Rhythm: good.** Nine cuts in eighteen seconds, and the shot grammar's
1.80/3.00s rates hold up. Nothing to change.

**Camera choice: nearly right.** The alternation between face and gameplay
lands where it should.

**The one fault: the gameplay shots crop away too much of the game.** This
points at a specific constant. Both gameplay rungs are 9:16 slices of a 16:9
frame, which is narrow by construction, but they differ:

| rung | height | width from a 1920px frame |
|---|---|---|
| `game` (`game_height_pct` 0.86) | 86% | ~522px — 27% of the frame |
| `game_tight` (`game_zoom` 0.64) | 64% | ~389px — 20% of the frame |

The clip's plan was `fmgmGFmGg`; the two `G`s are `game_tight`, and that is
almost certainly what was seen.

**This is the one area where the nine references cannot help.** The recipe says
so itself — none of them contains gameplay. So a viewer's judgement is the only
evidence that exists for how tightly a game should be framed, and the style
constants were never calibrated for it.

### The A/B, and what it settled — 2026-08-16

Three renders of that same clip, identical shot list and captions, only the two
framing keys moved:

| | `game_height_pct` / `game_zoom` | tight rung | of frame width | what the viewer sees |
|---|---|---|---|---|
| A | 0.86 / 0.64 | 388px | 20.2% | no hotbar, no hearts |
| B | 1.00 / 0.87 | 526px | 27.4% | the game HUD is in |
| C | 1.00 / 1.00 | 606px | **31.6%** | all of it, plus a sliver of the stream's counter |

**The owner chose C, and it is the default now.** 0.86 was there to skip the
stream's top HUD and bottom bar and it did — along with the HUD of the GAME,
which is the half a viewer needs to follow what is happening.

**31.6% is a ceiling, not a setting.** At full frame height the width of a 9:16
crop is fixed by the aspect ratio. If a gameplay shot still reads too tight,
nothing in `DEFAULT_STYLE` will fix it — that needs a letterboxed game band,
which is a different layout and a build rather than a tune.

C also made a latent defect reachable: with both keys at 1.00 the `game` and
`game_tight` rungs are the same rectangle, so a `g`->`G` step is a cut where the
picture does not change. `test_adjacent_shots_never_share_a_camera` had asserted
that invariant since the editor was written, but on the camera NAME, which
cannot see two names for one rect. `_merge_dead_cuts` joins them.
Measured: one cut removed at 1.00/1.00, none at either wider ladder.

## Facecam detection scored against the labels — 2026-08-16

With eleven sources labelled by eye there is finally a scoreboard. Restricted
to the stretch where each source's truth is stable (a source whose camera
changes mid-stream has no single correct answer for the whole file):

| source | want | got | |
|---|---|---|---|
| IShowSpeed EARLY, gaming part | 1 | 0 | ✗ |
| moistcr1tikal, left edge mid-height | 1 | 1 | ✓ |
| Minecraft 4h, after the gym | 2 | **2** | ✓ |
| Minecraft 12m | 2 | 2 | ✓ |
| gym 12m, fullscreen | 0 | 0 | ✓ |
| go ghost, edited | 0 | 0 | ✓ |
| apartament, edited | 0 | 0 | ✓ |
| Jensen Huang, edited | 0 | 0 | ✓ |

**7 of 8, which is far better than this file assumed.** Two things it settles:

- **The 4-hour source DOES give both facecams** when detection is confined to
  the stretch that has them. Known problem #2 is a framing problem, not a
  detector problem — and it is why the 20-minute stretches F1b uses are too
  short here: 33 frames per stretch against 330 for the whole gaming run, and
  the second cluster cannot clear its hit-rate gate on 33.
- **The edited sources are right for the right reason.** `webcam: None` on all
  three.

### Two more rules scored and rejected

Session 3 scored four edge-picking rules and told the next person not to
re-litigate without a new signal. The labels are that signal. Two more were
tried and both are WORSE than doing nothing:

| rule | result |
|---|---|
| widen `_WEBCAM_ASPECT` to (0.55, 2.0) so a portrait inset can pass | **5/8** — does not fix the portrait source, and two edited sources start reporting invented insets of 188x182 and 134x188 |
| try the edge search widest-first and keep the first reach whose rect is a plausible inset | **6/8** — does not fix it either, and adds a 200x168 false positive on moistcr1tikal |

So the aspect band is not the defect. It is the only thing currently rejecting
runaway rects, which is why relaxing it costs more than it gains.

### A second instance, with an identical signature — 2026-08-16

Jynxzi's stream has a bottom-left inset (labelled by eye) and detection returns
none, exactly like the EARLY STREAM source. Both fail the same way:

| | EARLY STREAM | Jynxzi |
|---|---|---|
| cluster hits / rate | 32 of 40, **0.80** | 32 of 40, **0.80** |
| rect from `_snap_inset` | (0, 12, 220, 256) | (0, 0, 158, 270) |
| frame | 480x270 | 480x270 |
| rejected by | area, aspect | area, aspect |

**Two independent sources with the same signature is the new signal session 3
asked for.** It is not a quirk of one stream: on both, the strongest face
cluster this detector ever produces yields a rect spanning nearly the FULL
FRAME HEIGHT.

**A lead, and a measurement that was aimed at the wrong edge.** The bottom-edge
search runs rows 212-270 and 219-270 with a peak-over-median of 1.75 and 1.92
against the 3.0 `_FACECAM_EDGE_DOMINANCE` bar, so it falls through to the frame
border. But these insets are BOTTOM-left: their bottom edge probably IS the
frame bottom, which would make 270 the right answer and the TOP edge the one
running away — it lands at y=0 and y=12 where the inset plausibly starts around
y=130.

So the next thing to measure is the BACKWARD (top) edge search, not the forward
one. Recorded rather than guessed at, because the difference decides whether
`_FACECAM_EDGE_DOMINANCE` is even involved.

### The seed, measured properly — 2026-08-17. Three more approaches, all refuted

Run `scripts/score_facecam.py` before believing anything here. It scores
detection against `source-labels.md` and its baseline is **6 of 9**. Note it
does NOT reproduce the "4-hour source gives both facecams" result above at any
frame range — that measurement came from the per-segment path, which samples
the proxy itself, and this one uses the sampled frames on disk. Treat the 4h row
as a constant rather than a discriminator.

**1. `_FACECAM_EDGE_DOMINANCE` is NOT involved, which settles the lead this file
left.** The previous entry said the top edge was the one to measure and that the
answer would decide whether the dominance bar mattered. Measured: on Jynxzi's
main cluster the top search's peak-over-median is **21.73** against a bar of
3.0, and on EARLY STREAM 3.92. Both sail through. The bar was never the gate.

**2. The REACH is the mechanism.** `_FACECAM_OUTER = 4.0` is in multiples of the
median face box, and the box wobbles — 22 to 62 px across eighths of one source.
On Jynxzi the box is 50 px, so the top search runs rows **0 to 144** on a 270-row
frame: more than half the picture. Inside a window that wide the strongest
gradient is whatever the busiest thing in the frame is, and on both sources it is
the stream's own top chrome.

Capping the reach at a fraction of the FRAME instead fixes both failures
outright, with rects that match the labels: EARLY STREAM `182x134@0,88` and
Jynxzi `158x118@0,106`, both bottom-left as labelled.

**3. And it costs more than it gains, for a reason worth knowing.** Score with
the cap: **4 of 9**. The four edited sources, which were clean negatives, start
reporting insets — `gym 12m`, `go ghost`, `apartament`, `Jensen Huang`.

> **The detector's precision on edited sources is an artefact of its imprecision
> on gaming ones.** The runaway rects are rejected by `_WEBCAM_AREA`, and that
> rejection is the only thing keeping the edited sources clean. Improve the
> geometry and the false positives appear, because nothing is discriminating —
> the area gate was doing it by accident.

That is why every approach to this edge trades one side for the other. It is the
same shape as the aspect band, which this file already records as "the only thing
currently rejecting runaway rects".

**4. A border test restores the negatives and loses the targets.** Requiring all
four sides of the snapped rect to be real steps (each clearing 1.8x its own
neighbourhood) puts the four edited sources back to 0 — and rejects EARLY STREAM
and Jynxzi too. **6 of 9 again, failing differently.**

**5. Scene-cut rate does not separate edited from locked-off.** The obvious
non-geometric discriminator, refuted in one measurement: insets run 1.50-3.92
cuts/min, and the edited sources are 0.18, 7.67 and 9.67. `go ghost` sits BELOW
every inset source, so no single threshold exists.

**What this leaves.** The geometry fix is known and it works; what is missing is
a discriminator that answers "is there an inset here at all", independent of how
big the rect came out. Until something does that job on purpose, the area gate
will keep doing it by accident and the reach cannot be fixed.

**6. `content_type` is not that discriminator, and cannot become one.** The
attractive idea — the classifier already knows which sources are edited, so skip
inset detection on those and the reach cap becomes safe — dies on one lookup:

| source | truth | classified |
|---|---|---|
| EARLY STREAM | inset | `talking_head` |
| moistcr1tikal | inset | `talking_head` |
| Jynxzi | inset | `talking_head` |
| go ghost | EDITED | `talking_head` |
| Jensen Huang | EDITED | `talking_head` |
| gym 12m | fullscreen | `commentary` |
| apartament | EDITED | `commentary` |

`talking_head` sits on both sides of the line and so does `commentary`. The
classifier's categories describe CONTENT — one person talking — and the question
here is FORM: edited versus locked off. Fixing the classifier would not help,
because it is not measuring this axis at all. Worth knowing before someone files
"the classifier is wrong" and "the facecam is missed" as one problem again.

**7. Judging the edges over the rect's own extent does not do it either.** The
sharpest remaining idea, and different in kind from the rest: `_snap_inset`
builds its column profile from the rows spanned by the FACE BOX — a band a
fraction of the inset tall — so a short coincidence and a full-height border are
indistinguishable to it. An inset's border runs the whole side. Re-measuring each
edge over the FOUND RECT instead, at bars from 1.3 to 1.7 and caps of 0.25 and
0.30: **5/9, 5/9, 6/9, 4/9, 5/9, 6/9.** Never better than doing nothing.

Eleven approaches have now failed on this edge, and the last two were the two
best remaining ideas.

### 8. THE ANSWER: those two facecams have no border — 2026-08-17

Twelve approaches, and the reason all of them failed is not in any of them.

The discriminator everything pointed at was the border: an inset is composited,
so its edge exists all the way round and sits on the same pixels in every frame,
where a rect grown around a face in an edited shot stops wherever some texture
happened to be. Two measures were built and run before any gate was written —
**coverage** (what fraction of the rect's perimeter is a gradient ridge) and
**persistence** (in what share of frames the ridge is at that exact pixel):

| rect | truth | coverage | persistence |
|---|---|---|---|
| Minecraft 12m | inset | 0.90 | 0.85 |
| Minecraft 4h | inset | 0.91 | 0.72 |
| moistcr1tikal | inset | 0.94 | 0.47 |
| `go ghost` | none | 0.28 | 0.34 |
| apartament | none | 0.67 | 0.73 |
| **EARLY STREAM** | **inset** | **0.41** | **0.31** |
| **Jynxzi** | **inset** | **0.44** | **0.84** |

**The measure works.** Known insets score 0.90–0.94; a source with no facecam
scores 0.28. It is not a bad signal.

**The two failing sources score at phantom level.** Best case for any threshold
over `coverage × persistence` is 10 of 13 rects, and the three it gets wrong are
these two plus moistcr1tikal — 5 of 9 by source, below the 6 of 9 baseline.

**Then look at the frames, which is what settles it.** Reproduce with
`scratchpad/border_signal.py` or just crop the labelled rect out of a sampled
frame. IShowSpeed's camera at that moment FILLS its region with chat composited
straight over it, and Jynxzi's webcam sits on a game menu screen with **no drawn
frame around it at all** — a borderless composite. Beside them the Minecraft
inset has a hard rectangular edge you can see.

**There is no border to find.** Every approach tried on this edge — four in
session 3, two in session 5, six on 2026-08-17 — is a rule over a gradient, and
on these two sources the gradient carries nothing, because nothing was drawn.
That is not a tuning problem and no threshold reaches it.

**So the correction to the entry above:** capping the reach appeared to "fix
both failures outright" because the rects it produced landed in the right
corner. Their border coverage is 0.41 and 0.44, at phantom level, so the edges
were never on anything. That was position luck, not a fix.

**What would actually work**, for whoever picks this up. A borderless facecam
has to be found from the FACE, not from its frame: the cluster's own extent
scaled by a factor, or a foreground/background separation over the region, or a
small learned classifier on the candidate patch. All three are real work rather
than a constant, and all three are more promising than a thirteenth rule over
`gx`/`gy`. **Do not write another one.**

### What the one remaining failure actually is

On the EARLY STREAM gaming stretch the face cluster is as strong as this
detector ever gets — **32 hits in 40 frames, rate 0.80** — and `_snap_inset`
grows it into **220x256**, 44% of the frame, against a real inset of about
120x138. The rect is wrong before any gate sees it; the gates then reject it,
and a facecam found with high confidence is reported as none.

That is six approaches now that do not fix this edge, four from session 3 and
two here. It needs something other than a better rule over the same gradient
profile — the seed, or a different signal entirely. **Do not spend another
session widening bounds.**

## What is fragile — 2026-08-17

Not a bug list; the bug list is below. This is where the system is most likely
to be WRONG WITHOUT SAYING SO, ranked by how much a failure costs before anyone
notices. Written after three sessions of running it on real sources, and every
entry has a measurement behind it.

**1. Facecam detection, and it is not close.** Its precision on edited sources
is an ARTEFACT of its imprecision on gaming ones — the runaway rects are
rejected by `_WEBCAM_AREA`, and that rejection is the only thing keeping the
edited sources clean. Demonstrated: fix the geometry and four false positives
appear. Nothing is discriminating; a gate is doing it by accident. 6 of 9 on the
labelled sources, eleven approaches failed, and every failure trades one side
for the other. A wrong answer here silently picks the wrong layout for every
clip in the source.

**2. The settings whitelist is a systemic trap, not an incident.**
`_normalise_settings` keeps only keys already present in `_default_settings()`,
so a key added to the frontend and forgotten there is discarded in silence —
and every unit test of the feature stays green, because the value never
arrives. It bit twice in one day: `auto_export`, then `vision_review`. The cheap
guard is a test that compares the TypeScript `DEFAULT_SETTINGS` against the
Python defaults and fails when they diverge.

**3. Hands-off made concurrency normal.** `max_concurrent_jobs = 2` and both
`clipper_transcribe` and `clipper_export` sit in the heavy lane, so pasting
three links now means whisper on the GPU while another project renders. That
used to be an exotic case; the auto-export switch made it the ordinary one.

**4. A partial transcript passes as a whole one.** `_tolerant` stopped one bad
chunk losing a 4-hour file, and the result now carries `failed_chunks` — which
NOTHING downstream reads. A transcript with holes scores and clips as if it
were complete.

**5. The dynamic editor's framing rides on per-window detections.** The guard
added on 2026-08-17 catches the catastrophe (a face shot whose crop excludes the
face) and fires on 6% of face shots. It does not catch drift, only collapse.

**6. Both halves of Pass D are young.** Two of the local half's three checks
were WRONG the first time they touched real data. The vision half is one model,
one prompt, no versioned eval, judged on six clips, and non-deterministic.

**7. Features that measure nothing look exactly like features that do.**
`speech_ratio` is 0.90–1.00 on every source; `laughter_score` is 0 everywhere
while `emotion` holds 8% of the gaming profile; callbacks fired 6 times in 927
candidates. A score built from five live signals and three dead ones reads the
same as one built from eight live ones.

**8. Nothing prunes the disk.** `data/clipper` is past 56 GB and every project
keeps its source, proxy, audio, frames and exports forever. C: has 10 GB free.

**9. Documentation drift is a measured rate, not a worry.** Five stale claims
and two byte-identical duplicate files found in a single reading pass on
2026-08-17. The map-and-handoff discipline is the mitigation and it only works
if the next person keeps it.

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

### 2b. A face shot can frame no face — found by Pass D, 2026-08-17

The first thing Pass D caught that nothing else was looking for, on the first
run with the vision half on. Worth reading as evidence that the pass earns its
place, and as a defect in its own right.

Clip `e8fa6b35ea66` of project `547301635e54`, shot 15, `face_medium`,
31.07–33.49s. The frames show Minecraft dirt. No face.

The camera is correct: `face_medium` is `y=34 h=334`, and the subject cluster
sits at `cy=176`, inside it. The SHOT's rect is `y=260 h=334` — 226 px lower,
below the facecam entirely.

`plan_dynamic_edit` re-centres each face shot on `_centre_at(samples, t0, t1)`,
the median of the detections **inside that shot's window**. The cluster centre
is computed over the whole clip and is stable; a single window's detections are
not, and when they are bad the crop walks off the inset that the cluster had
found correctly.

This is the seed-wobble family from problem #4 showing up somewhere new: there
the unstable face box moved the EDGE SEARCH, here it moves the CROP. The
obvious guard is to clamp the per-shot centre to within the cluster's own
tolerance of the subject centre — `_dominant` already computes that tolerance
for exactly this reason. Not done: it changes framing on every dynamic export
and wants its own before/after.

### 3. Captions collide with in-game UI — FIXED 2026-08-17, with one gap named

`resolve_position` had implemented collision avoidance since the clipper
shipped, and it avoided the rects in `keep_out`, which came from HUD detection.
On this source `regions.hud` is `[]` — **the avoidance was working perfectly
against an empty list.** The missing half was never the algorithm.

`dynamic_window.ui_panels` supplies the list, per CLIP rather than per project,
because a panel opens and closes and `regions.json` is measured once for a whole
stream. PERSISTENCE is the whole design: a cell counts as UI only if it is flat
mid-grey in half the sampled frames. That is what separates a panel from grey
stone terrain, and it was confirmed by looking — a single-frame grey mask selects
a hillside, the persistence map selects the subscriber counter and the BOSSES
BEATEN box.

**The first version of this detector found nothing on five real clips.**
`PANEL_MIN_AREA = 0.015` was a guess, and the persistent UI on a real stream is
not one big panel but a counter, a timer, a hotbar. At 0.002 they come back, at
identical coordinates on three different clips — which is itself the evidence,
because a stream's chrome does not move.

**Still open:** a transient game menu. The Minecraft inventory that caused the
original defect opens for a few seconds inside a 38-second clip and cannot clear
a persistence bar set where terrain fails it. What is detected is the STATIC
stream chrome, which is a real keep-out — Pass D's one verified true positive was
a caption over the stream's own overlay bar — but it is not the whole of §21.

The old text follows, because the diagnosis is still the useful part.
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

### 4. The facecam rect is too tall on this source — measured 2026-08-15

The detected webcam is 312px where the real inset is ~272, so the streamer's
stats bar bleeds into the face band. `_fit_inside` correctly stays inside the
*detected* rect — the rect itself is wrong.

**Ground truth is h=68** on the 480x270 proxy, read off four frames spread
across the source: the inset ends exactly at the top of the white subscriber
counter, and h=78 is the counter's *bottom* edge.

Four things were measured before touching anything, and they redirect the fix:

1. **It is not a constant error, it flips.** Detecting on eighths of the source
   gives `none, 78, 68, 68, 78, 78, 68, 68` — unstable inside one segment, on
   one layout.
2. **So per-segment detection (problem 2) would not fix it.** The flip happens
   within the Minecraft stretch, not between it and the gym segment.
3. **`argmax` in `_snap_edge` is not the culprit.** On the averaged row
   profile, y=68 is the STRONGEST peak in every sample measured — 55.6, 66.4,
   71.4, 79.9, 70.9 — against 54.6, 60.3, 55.6, 54.9, 62.8 at y=79. Given the
   choice it already picks correctly. (So the outward-biased rules session 3
   scored are still the wrong direction, and there is no need to retry them.)
4. **The seed moves the window off the edge.** `_snap_edge` only searches
   `[cy + 0.75·h, cy + 4·h]` around the median face box, and that box is not
   stable: its height measures 22, 23, 29, 32, 51 and 62 px across the eighths.
   When the cluster merges the face with something else the window starts below
   y=68 and the true edge is not a candidate at all.

**So the fix belongs in the seed, not in the edge rule** — the face cluster is
what wobbles. That is the same machinery session 3 rebuilt, with a documented
regression list, so it wants its own pass rather than a quick patch. Not small,
which is why it was left after being measured.

### 5. Callback linking is validated only by mocks
No callback has ever fired on real data. The mechanism is tested end to end
against mocked answers, and on the 4-hour run the setups reached the model and
it linked none. It may be right to decline: "we're gonna give you diamond" is
not really paid off by mining one. **This needs a source with a confirmed
callback.**

### 6. Every clip ends on the last word — FIXED 2026-08-15

Found by watching the three exported clips — the first time anyone
had. All three end **exactly** on Whisper's `word.end` for their final word
(13048.82 / 13083.16 / 4401.02, matching to the millisecond). The listener
hears the last word truncated.

`_nearest_sentence_end` picks the right *sentence* — that part works. What is
missing is the pad after it. `TAIL_PAD_S = 0.25` is commented "breathing room
kept after the last word" but `_trim_tail` applies it only as a downward
ceiling (`max(floor, min(end, last + TAIL_PAD_S))`), and only when there is
dead air to trim. When the end already sits on the last word it returns
untouched. **The pad can never extend, only trim.**

Measured on this source: 300 sentence-final words each followed by ≥1.2s of
silence, walking the RMS envelope in `speech.wav` forward from `word.end` to
the local noise floor:

| p25 | p50 | p75 | p90 | p95 |
|---|---|---|---|---|
| 0.09s | **0.16s** | 0.26s | 0.40s | 0.57s |

So the cut loses real audio on most sentence endings, and 0.4–0.57s on the
worst tenth — which is what a listener notices.

**Fixed**: `candidate_boundaries._keep_release`, called last in
`refine_boundaries` so it pads whatever word survived the orphan drop.
`TAIL_PAD_S` is now 0.40 and works in both directions — `_trim_tail` will not
cut closer, `_keep_release` will not end closer — so the constant finally does
what its comment always said. Bounded by the next word (minus `RELEASE_GAP_S`),
the media end and the duration cap. Applied to the three real clip ends it adds
exactly 0.40s to each. Five unit tests plus a regression assertion that no
refined candidate ends on a word's timestamp when there is silence to use.

Sixth instance of a constant that does not do what its name says, after
`ARCHETYPE_SHAPE`, `context_debt`, `hook_latency`, `audio_peak_ratio` and
`game_ui_ratio`.

### 7. `context_debt` was decided by one unverified string — FIXED 2026-08-15

Both Minecraft clips carried `context_debt` 0.5, half of maximum. Watched cold,
both were understood without effort.

Measured: the word evidence scores both **0.000**, correctly. The whole 0.5
came from `debt = max(debt, 0.30 + 0.20 * listed)` with a single item on the
model's `unresolved_context` — an unverified list, produced per ANCHOR, so
every edit variant paid it no matter how much setup its own window carried.
That is the opposite of what `latest_complete_start` exists to do.

**Fixed**: the list now contributes (`LISTED_CONTEXT_W = 0.12` per item, capped
at three) instead of setting a floor. The two real clips go 0.500 → 0.120, and
five listed items still read as a real debt. Thin evidence — two clips, one
viewing, one source — which is enough to stop an override that contradicted
every other signal and not enough to tune on.

### 8. A portrait facecam cannot be detected at all — measured 2026-08-15

First result from a source that is not the Minecraft co-stream. `IShowSpeed —
EARLY STREAM!`, 4h24m, a gaming stream with an obvious facecam bottom-left,
chat down the left edge. `regions.json`: **`webcam: None`, `webcams: 0`.**

Traced to the shape gate in `_find_webcams`. The real facecam cluster is found
easily — **29 hits in 40 frames, rate 0.72**, centred at (65, 132), exactly
where the eye puts it. It is then thrown away:

```
hits=29 rate=0.72 med=(65,132,64,64) -> rect=(0,12,150,256)
                                        area=0.296 aspect=0.59  REJECT:aspect
```

`_WEBCAM_ASPECT = (1.1, 1.9)` — landscape only. The Minecraft insets measure
122x68, aspect **1.79**, which sits just inside the top of that band. The band
was drawn around one stream's layout, so **a portrait or square facecam is
structurally undetectable**, no matter how confidently it is found.

There is a second fault stacked on it: `_snap_inset` grew a ~120x138 inset to
150x256, nearly the full 270px height of the proxy, because the chat column
above the facecam leaves no horizontal edge to stop at. Even a perfect snap
(aspect ~0.87) would still fail the gate, so the aspect bound is the primary
cause and the runaway snap is the same seed problem as #4.

**Do not just widen the band.** Fitting (1.1, 1.9) to one source is what caused
this; refitting it to two sources is the same mistake with a bigger sample. The
gate wants to express what a facecam actually is — a small, stable, face-
containing inset near an edge — and `hits`, `rate` and `drift` already measure
that far better than the aspect ratio does.

### 9. The content classifier called a gaming stream `talking_head`

Same source: `content_type = talking_head`, confidence **0.426**. It is a
Fortnite/Minecraft stream. This is not cosmetic — `content_type` selects the
weight row in `scoring.PROFILES`, so every clip from this source is scored
against the wrong profile, silently. `plan_layout` reads it too.

Predicted before the run as one of three possible outcomes, and it is the worst
of the three: a wrong answer given confidently enough to be used.

### 10. Smaller
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

## What I would do next — all but one done on 2026-08-16

1. ~~**Wire the dynamic editor into export.**~~ **DONE.** `dynamic_window.py`
   carries the per-window analysis into the pipeline and `handle_export` has a
   multi-shot branch. Verified on a real export: 9 shots, 6 hit flashes, 21s.
   Off by default (`dynamic_edit`).
2. ~~**Region detection per segment.**~~ **DONE.** `regions_by_segment`, with
   `_regions_for` giving each clip the layout of the stretch it sits in. The
   first 40 minutes of the 4-hour source now correctly report no facecam
   instead of being handed a Minecraft inset rect.
3. ~~**Spatial `game_ui_ratio`** → caption keep-out, plus the missing 55–75%
   clamp from the style spec.~~ **DONE 2026-08-17.** `dynamic_window.ui_panels`
   detects the panels per clip, `captions.panels_to_keep_out` maps them through
   every shot's crop, and `resolve_position` searches for the widest clear band
   inside the spec's 55–75% instead of trying six ±4% nudges. Verified on a real
   export: the caption moved 0.75 → 0.554 in the burned .ass and Pass D went
   from REVISE to APPROVE on the same clip.
4. ~~**Watch three clips.**~~ **DONE**, and it was worth more than the rest of
   the list put together. Four defects came out of looking rather than
   measuring: clips ending on the last word's timestamp, `context_debt` decided
   by one unverified string, the caption panel printing a scaled value with an
   "s" after it, and `-t` letting ffmpeg read past the window to refill trimmed
   seconds. All four passed every test that existed.

---

## Session 5 — 2026-08-16, what shipped

Sixteen commits. The order matters: the labels came in the middle and changed
what the rest of the list was for.

**Built and wired**

| | |
|---|---|
| Multi-shot export | The dynamic editor reaches `handle_export`. Off by default. |
| Dead-air trimming (§15) | Wordless silences cut out of the middle in the same encode, captions remapped. Off by default. Measured: 298 of 920 candidates get a cut, ~3.1s each. |
| Episodes (§2) | A late chunk is told what the stream has been about. No model call — threads and atoms already held it. The last P1. |
| Reasoning panel (§34) | `clips.reasoning` had been written and served since the story engine shipped and nothing displayed a field of it. |
| `content_type` per stretch | 0 of 12 stretches right on the 4-hour source, to 9 of 12. |
| Regions per stretch | See item 2 above. |
| Anchors checkpointed | With a fingerprint, so a prompt change recomputes instead of silently reusing. |
| yt-dlp auth | Cookies and a JS runtime; without both, a public VOD returns storyboards only. |
| Transcribe progress | The worker's own message reaches the queue instead of a constant "Transcribing". |
| Test database | The suite ran against the real `clipforge.db`. It does not now. |
| `scripts/export_clipper_state.py` | Transcripts to disk plus a manifest of what is already done. |

**Measured and deliberately NOT changed**

- The facecam aspect band. Two more rules scored against the labels, both worse
  than leaving it alone (5/8 and 6/8 against 7/8). See the scoreboard above.
- The `game_zoom` crop, which a viewer said takes too much off the gameplay.
  One viewing is a start, not an answer; the A/B costs twenty seconds.

**Session 6 — 2026-08-17**

| | |
|---|---|
| The shot planner was never given the words | `_candidate()` passed four keys and `dynamic_edit` reads a fifth. `_boundaries` placed every cut on audio peaks and scene changes instead of speech pauses, and `_speech_ratio` reported the streamer silent in every shot of every clip ever exported. 11 shots with the words against the 9 that shipped. |
| Gameplay framed as wide as the crop allows | `game_height_pct` and `game_zoom` both 1.00 after a three-way A/B. See the section above. |
| Pass D, local half (§21–22) | `review.py`. Three checks, one per failure seen on a real export. Advisory, written to the export sidecar under `review`. |
| The audio shipped clipped, at 96 kHz | `loudnorm` in single pass aims at its TP, it does not enforce it — +0.2 dBFS against −1.5 — and it leaves the stream at its own internal rate. Reported by ear, confirmed by measurement. |
| Both renderers level the same way | The static path had NO loudness processing at all, so a clip that fell back to it shipped quieter than one that did not. One `loudness_chain()` in `ffmpeg_tools`, used by both. Verified on real renders: 48 kHz, −13.3 LUFS, −1.4 dBFS on each. |
| `dynamic_edit` ON by default | See the switch table above. |

**The thing that nearly made the whole session pointless:** every fix listed
under session 6 lands on the multi-shot path, which was off by default. With
stock settings an export touched none of it — not even the audio ceiling, which
lives in `dynamic_render.py`. Worth checking, whenever a session ends, that the
work is on a path something actually takes.

Pass D's own first run is the lesson worth keeping: **two of its three checks
were wrong the first time they touched real data**, in ways no unit test would
have caught. It compared proxy pixels to source pixels, and it judged the
co-streamer's face against a crop framing the other person — reporting "100% of
the face is outside the crop" on every clip it saw. Both are pinned by tests
now. A checker needs checking.

**Ground truth that now exists**

`docs/source-labels.md` — eleven sources labelled by eye. Every threshold in
the detectors had been fitted to one Minecraft co-stream, and there was no way
to tell a correct rule from one that happened to fit. There is now.

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
