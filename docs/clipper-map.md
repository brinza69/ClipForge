# AI Stream Clipper — the map

**Read this first.** Every file in the clipper, what it is for, and which
document holds the reasoning behind it. Verified against the tree on
2026-08-28.

It exists because four sessions in a row began by grepping the codebase to
rediscover where things live, and because the old map in `CLAUDE.md` listed
five files that do not exist.

> **When you add a file, add it here in the same commit.** A map that is only
> mostly true is worse than none: the next session trusts it and greps anyway.

---

## Where to start

| you want | read |
|---|---|
| the current state of the world, known problems, traps | `handover/areas/clipper/CURRENT.md` |
| what every file is (this document) | you are here |
| why the reasoning works the way it does | `story-engine.md` |
| planul auditat pentru Reasoning v2 | `plans/ai-stream-clipper-reasoning-v2.md` |
| următorul plan: selecție + montaj content-aware până la activarea în produs | `plans/ai-stream-clipper-production-engine-v1.md` |
| ce anume din acel plan a fost verificat în cod, și cu ce dovadă | `plans/ai-stream-clipper-reasoning-v2-review.md` |
| which brief requirement is built, section by section | `story-engine-spec-status.md` |
| ground truth for the detectors — what each source actually is | `source-labels.md` |
| how to run the pipeline by hand | `ai-stream-clipper-runbook.md` |
| the measured recipe behind the multi-shot edit | `dynamic-edit-recipe.md` |
| what is already on disk and can be skipped | `../data/clipper/MANIFEST.md` |

Older handoffs are history, superseded but not wrong about the code they
describe: `handover/archive/clipper/handoff-clipper-session-3.md`,
`handover/archive/clipper/handoff-clipper-session-2.md`,
`handover/archive/clipper/handoff-dynamic-edit.md`.

---

## The pipeline, in order

Six job types, registered in `workers/clipper_pipeline.py`. Each writes its
output to disk before the next starts, so a crash resumes rather than
re-downloading.

```
ingest  →  transcribe  →  analyze  →  score  →  export / preview
```

| stage | handler | writes |
|---|---|---|
| `clipper_ingest` | `clipper_pipeline.handle_ingest` | `source/`, `proxy/proxy.mp4`, `audio/speech.wav`, `meta` |
| `clipper_transcribe` | `clipper_pipeline.handle_transcribe` | the `transcripts` row |
| `clipper_analyze` | `clipper_pipeline.handle_analyze` | `signals`, `faces`, `regions`, `regions_by_segment`, frames, `content_type` |
| `clipper_score` | `clipper_build.handle_score` | `segments`, `atoms`, `promises`, `threads`, `graph`, `anchors`, `segment_types`, `candidates`, `reasoning_run`, `selection_trace`, the `clips` rows |
| `clipper_export` | `clipper_render_jobs.handle_export` | `exports/<clip>.mp4` + a `.json` sidecar |
| `clipper_preview` | `clipper_render_jobs.handle_preview` | `previews/<clip>.mp4` |

---

## `server/services/clipper/` — the logic, DB-free and unit-testable

### Ingest and signals

| file | what |
|---|---|
| `ingest.py` | download or copy the source, build the 480p proxy, extract audio, sample frames |
| `signals.py` | Pass A: per-hop loudness, peaks, silence, speech, motion, scene cuts, faces |
| `storage.py` | the ONE place that knows artifact paths and names. Adding an artifact means adding it here |
| `urlguard.py` | URL policy check before anything is fetched |
| `series.py` | scaling a measured series onto 0..1, ONCE. `dynamic_edit` and `dynamic_regimes` scale different populations — per-shot means against per-sample values — and that difference is deliberate; the arithmetic under it is shared, because a duplicated helper is one edit away from being a second rule. Returns whether the series had any spread, since a flat one lands at 0.5 and 0.5 sits under a default cut-off of 0.6 |
| `ffmpeg_tools.py` | `run`, `ffmpeg_bin`, `video_info`, `even`, filter-path escaping |

### Understanding the stream

| file | what |
|---|---|
| `segmentation.py` | Pass B: semantic windows, sentence splitting, `norm_token`, signal views |
| `atoms.py` | the stream as utterances that carry their own signals (§1), plus `search` (§25) |
| `threads.py` | narrative arcs by lexical chaining, and the two graph edges anything reads (§3, §5) |
| `episodes.py` | what the stream has been about, per stretch (§2). Read by the anchor prompt |
| `promises.py` | setups that could pay off later, and what a callback costs (§4) |
| `story.py` | the payoff-first reasoning: anchors, context debt, hook latency, archetypes, edit variants |
| `story_evidence.py` | the ONE representation of the narrative evidence. Deterministic grounding of a model claim against the atoms it names, `remeasure` after every boundary change, and `semantic_payoff` — the payoff features read now instead of the audio detector's. Marks a failed match, never drops it |

### Choosing clips

| file | what |
|---|---|
| `candidates.py` | Pass C entry point; `generate_candidates`, `refine_boundaries`, `merge_nominations` |
| `candidate_boundaries.py` | where a clip starts and ends. Sentence snapping, reaction keep, tail release |
| `candidate_proposals.py` | turning anchors into candidate windows |
| `candidate_terms.py` | the frozen feature vector, the word lists, and the boundary constants |
| `vocal_bursts.py` | laughter and shouting from the audio — what `laughter_score` reads now that the word list never fired |
| `dead_air.py` | dead seconds inside a chosen window, and the arithmetic of removing them (§15) |
| `scoring.py` | the sub-scores, the four named score scales and eligibility |
| `scoring_profiles.py` | the ten weight rows and nothing else. Moved verbatim when `scoring.py` crossed 500 lines — the comments above each row ARE the record of what was measured on which source, and `scripts/score_contribution.py` was run before and after to prove the ordering did not move |
| `candidate_groups.py` | MOMENTS rather than variants: a stable `moment_id`, one representative cut per moment, the budget that decides who the judge is asked about, the labelled packet it is asked with, and `story_census` — how many story moments exist, how many are grounded, and which quarter of the source they sit in. Reuses `dedupe._group` for the grouping itself |
| `verdict_propagation.py` | What happens to a verdict AFTER the judge gives it: every cut of a judged moment inherits it, blended against its own heuristic score, into `selection_score` and never into `overall`. Split from `candidate_groups.py` at 500 lines; re-exported from it |
| `selection.py` | which moments reach the board. When a judge ran the board is drawn from the moments it SELECTED, never from two score scales compared against each other; plus the round cap and the declared backfill. Runs in shadow until v2 has been compared against legacy |
| `dedupe.py` | overlap, text and same-payoff grouping; diversity across time, thread and archetype |
| `ranker.py` | the learned ranker. Complete, dormant, needs 40 labelled clips |
| `feedback.py` | recording what the user did with a clip, and — since the `origin` column — WHO did it. Only `manual` events label for training; `auto` (auto_export) and `system` are neutral, and so is the NULL origin of any row written before the column. The measurement that forced this is in the module docstring |
| `review.py` | Pass D (§21–22): what the CLIP looks like, checked before the encode |
| `review_vision.py` | Pass D's second half: a vision model on the RENDERED clip. Off by default, needs an OpenAI key |

### Models

| file | what |
|---|---|
| `edit_profiles.py` | Batch R2: which editing grammar a clip gets. Maps the ten EXISTING content types onto six profiles (`talking_head`, `conversation`, `action`, `exploration`, `instructional`, `conservative`) and holds the closed regime list R3 will assign from. A weak classification buys a SAFER edit, never a bolder one: missing or low confidence resolves to `conservative`, and a missing confidence is never reconstructed from the source's score — "low" and "never measured" are different answers. A person's override is trusted without a number, because the provenance is the evidence. **The pace bands are chosen guardrails, not measurements**; the file says so and the tests refuse to assert them. Also holds `edit_mode` — `legacy_dynamic` / `content_aware_shadow` / `content_aware`, the last refused until the gate — with the same shape as `reasoning_mode`. `band_for` is THE band accessor since R4: `action` is the one profile with a second, quieter band, and a caller reading `cuts_per_min` straight out of the dict judges a gaming clip's lulls against the busy one |
| `reasoning_mode.py` | WHICH engine a project runs, as one setting. Also the compatibility mapping for the `llm_select` + `reasoning_version` pair it replaced — both of which `_normalise_settings` used to drop in silence, which is why the story engine could not be turned on from the API at all |
| `reasoning_trace.py` | what a scoring run DID: `reasoning_run.json` (versions, chunk plan, every provider attempt and fallback, the settings actually in force) and `selection_trace.json` (why each candidate ended where it did, including the ones the judge never saw). Pure; the worker feeds it and `storage.py` writes it |
| `chunking.py` | how a long stream is handed to a model: chunks bounded by the CLOCK as well as the byte budget, an overlap so a moment on a seam is whole somewhere, coverage accounting that names the gaps, and a quota that follows the span. Replaces the character-only split that turned four hours into 3h21m + 38m |
| `llm_engine.py` | reaching a model and recording every attempt: `_ask`, `parse_json`, and the trace notes. Split out when Batch 0's tracing took `llm_select` past 500 lines; re-exported from there so nothing else had to change |
| `llm_prompts.py` | what we ASK a model: the nomination and anchor prompts, versioned because the cached anchor artifact is only valid for the prompt that produced it |
| `llm_select.py` | anchor detection and the nomination pass, chunked and versioned |
| `llm_judge.py` | comparative ranking from three perspectives (§18, §19) |
| `headline.py` | the clip's headline text |

### Seeing the frame

| file | what |
|---|---|
| `content_type.py` | the parts that need a decoded image: frame features, face boxes, chat/HUD/gameplay regions |
| `content_facecam.py` | finding the facecam, and the only place its constants live. Four sessions and sixteen approaches are in the comments; `scripts/score_facecam.py` is the scoreboard. Read them before changing a number. **Patch THIS module when sweeping a constant** — `content_type` re-exports them, and assigning there binds a copy the detector never reads |
| `content_geom.py` | the pure half — rect maths, signal summaries, the classifier itself, and `scene_independence`: whether a candidate rect holds a second camera or a piece of the same picture. That is what says a facecam is there when it has no border to find |
| `segment_type.py` | content type per stretch rather than per file, and the signal slicing that allows it. `verdict_at` returns the type WITH its confidence and provenance — `type_at` throws the confidence away, which was fine until an edit profile needed to tell a measured verdict from a fallback |
| `layout.py` | plan one 9:16 frame: which layout, which rects, which safe zones |
| `layout_geom.py` | the rect arithmetic behind it |

### Rendering

| file | what |
|---|---|
| `render.py` | the static path: one filtergraph, one encode, optional dead-air cuts. Holds `RENDER_VERSION = render_static_split_v1`, the counterpart to `dynamic_render`'s — a static export stamped with the dynamic version is attributable to a grammar of shots it never had |
| `captions.py` | the caption plan and its overlays |
| `dynamic_edit.py` | the multi-shot planner: which camera, where the subject is. Calls `merge_equivalent_shots` ONCE, on the finished shot list, so every caller gets a plan with no invisible cut. It replaced `_merge_dead_cuts`, which compared the planned RECTANGLE and so could not see composition: two `fit` shots have different rects and deliver the identical full frame, which is how 116 invisible cuts reached the pilot corpus |
| `dynamic_subject.py` | two questions about the subject. Per span: is any face present — hysteresis over the 0.25s track, deciding `crop` vs `fit`. Per source: `stable_track` anchors face-family crops on a fixed webcam overlay. Since R3a, `anchored_track` drops every detection that is not on the fixed anchor and `creator_presence` back-dates a confirmed absence to where it began — recorded as `creator_view` beside each export, applied to none of them. Measured on the Moist pilot: 36% of the samples carrying a face are anchor-compatible — the wording matters, because nobody labelled the boxes and geometry is all this knows. The presence effect is deliberately NOT quoted from `faces.json` — its median gap there is 6.7s, which rounds `ENTER_S` to one sample and measures a different rule |
| `dynamic_cuts.py` | WHERE the edit cuts, on the clock — sentence ends, peaks, rhythm. Split from `dynamic_edit.py` at 500 lines; knows nothing about cameras |
| `dynamic_cameras.py` | the camera rungs and the action band |
| `dynamic_regimes.py` | Batch R3b: what a stretch of a clip IS — `speaker`, `conversation`, `action`, `visual_evidence`, `reaction`, `safe` — decided PER SAMPLE over the CLIP's intervals. Per shot was a majority vote and produced a `reaction` from two people never on screen together. It refuses to guess in five places: a motion series on ANOTHER CLOCK (whole frames make a 10 FPS proxy sample every 0.2s; index 0 is a sentinel and value `j` covers the interval BEFORE it, so it is resampled onto the canonical bins first); coverage and variability as SEPARATE axes, since a complete flat series is real; absent word times; a face track shorter than the clip, whose tail is `creator_unknown`; and evidence, which is averaged only over measured samples and is `None` — never 0.0 — where nothing was measured. The visual key never says `crop_creator`: `stable_track` finds a geometrically stable anchor, not an identity, so it is `crop_anchor`, `crop_subject` or `fit_full` |
| `dynamic_rhythm.py` | Batch R4: WHEN the edit is allowed to cut, and why. **A cut needs a reason AND a place.** A reason is a declared change in what has to be seen — R3b's `treatment_change`, the source's own `source_scene_cut`, or an `action_beat` inside a stretch R3b measured as action; a place is a boundary where the cut does not land inside a word, taken from `_boundaries` rather than reimplemented. A pause with nothing behind it is the cut this batch removes, and the two kinds of reason are ASYMMETRIC: a treatment change is required and cuts even unsnapped and even against `min_shot_s`, with the violation recorded, because holding a crop on an empty anchor trades a visible defect for an invisible metric. §4's bands are compared against and never enforced — `action` alone is judged on two of them, split into the seconds R3b called action and the rest, and both come back `unavailable` when the motion series was never measured. `indeterminate` is a real verdict: under `60 / lo` seconds the band's own floor expects no cut yet. `UNMEASURED` names what §4 asks for that nothing here measures — a keyword regex is not an emotion, and the active speaker is what §3.4 forbids guessing — which is why `talking_head` and `conversation` read `below` |
| `dynamic_window.py` | the per-window signals the planner needs, and since R3b the REAL step the motion series is on — `round(fps * hop)` whole frames, which is 0.2s on a 10 FPS proxy however politely 0.25 was asked for — dense face track, motion inside the game region, and `ui_panels`, the per-clip UI rectangles the caption keeps out of |
| `dynamic_render.py` | the shot list as one `-filter_complex` with `sendcmd`; current `RENDER_VERSION` is `render_v3_letterbox`, with full-frame `fit` over a blurred backdrop |
| `dynamic_geometry.py` | the arithmetic on a plan: sizes, anchors, crop expressions and the `sendcmd` script. Holds `composition` — `crop` (a 9:16 window on a subject) and `fit` (the whole frame, letterboxed, for sequences with no subject) — and, since R1, `visual_key` + `merge_equivalent_shots`: what the viewer actually receives, and the merge that removes a cut only when the delivered picture does not change. The key is the size timeline plus the position expressions, so a shot that MOVES is never absorbed and identical shake still merges (the expression is a function of absolute `t`, so it continues unbroken across the join). Takes `ASPECT` from `dynamic_cameras`, its real owner — going through `dynamic_edit`'s re-export made the geometry import the planner |
| `serialize.py` | DB rows to API dicts, and `effective_content_type` |
| `edit_quality.py` | Batch R0: the export audit as code. Per-clip structural metrics off the sidecar — shots/min, shortest shot, `fit → fit` cuts that show the same image, lead-in and tail, composition and regime mix. The composition list is CLOSED (`crop`, `fit`). An ABSENT composition is age — an old plan, undecidable, not a defect; a composition that is present and impossible (`null`, a number, an unknown string) is corruption and fails the gate. Same rule for `duration` and `drop_spans`: absent is unknown, present and unusable is a defect. Reads shot order AS PERSISTED, refuses a malformed plan instead of repairing it, and spells a missing measurement `unavailable`, never 0 — one undecidable boundary makes the whole count `unavailable`, and a time that parses is not a time that happened: shots and words are checked against the WINDOW for finite, in-range, ordered values (a zero-length word is normal — 156 of 8.249 in the pilots — a backwards one is not), and a shot, word or trim span that fails refuses that half outright — an absent `drop_spans` is not a defect, a corrupt one is. `defects` is a LIST: a clip can be wrong in more than one way. When `drop_spans` are present the shot half is refused outright: a trim changes the edit, not just the clock, and reconstructing the delivered sequence is **Batch R8**'s — R1 is about equivalence, not trim. Exact equivalence only |
| `edit_quality_totals.py` | the aggregation: project and corpus figures. Split from `edit_quality.py` at 500 lines. It imports that module and that module must NOT import it back — the re-export it started with made the pair a cycle that worked only if you imported them in the right order. A rate is built only from clips carrying BOTH shots and duration; a total is a total or it is `unavailable`, with the lower bound published under its own name. Pooled and per-source rates are different questions and both are printed |
| `render_input.py` | what an export was made FROM, and the digest over it: `FINGERPRINT_KEYS`, `render_input`, `input_fingerprint`. The ONE projection — `clipper_render_jobs` stamps with it, `audit_clipper_exports` recomputes and compares. Re-exported from `edit_quality` |

---

## `server/workers/` — orchestration, DB-aware

| file | what |
|---|---|
| `clipper_pipeline.py` | ingest, transcribe, analyze; registers all six handlers |
| `clipper_build.py` | the scoring stage end to end |
| `clipper_judging.py` | running the judge over pools of moments: which get asked about, how many rounds, and the verdict propagated to every cut of a judged moment |
| `clipper_cache.py` | what a previous run left on disk and whether it can still be trusted: the anchor stamp (prompt, mode, engines, duration, chunk plan) and the per-stretch content types. A checkpoint without a stamp is worse than no checkpoint |
| `clipper_finalize.py` | everything after the winners are chosen: layout + caption planning, headlines, the two trace artefacts, the `clips` rows, and auto-export. Split out when Batch 0 took `clipper_build` past 500 lines; re-exported from there |
| `clipper_scoring.py` | one run's candidates, scored: feature extraction, the local content verdict with its confidence and provenance, the heuristic score, and the optional blend with the learned ranker. Split from `clipper_build.py` when R2's propagation pushed it past 500 lines |
| `clipper_captions.py` | the caption track for one render: the words on the clip's clock, the height the export picks when it finds game UI the score-time plan could not have known about, and the .ass the renderer burns. Split from `clipper_render_plan.py` at 500 lines and re-exported from it |
| `clipper_render_plan.py` | WHAT a render will contain: window, shot list, layout, caption file, dead-air spans. `_decide_render` is the entry point and both handlers consume its one answer. It also builds R4's boundary list and clip-relative scene cuts into `_rhythm`, because the words, the scene list and the audio peaks exist nowhere else — working data, popped before the sidecar with the rest |
| `clipper_shadow_views.py` | everything a render RECORDS about the edit it did not make: R3a's `creator_view`, R3b's `regime_view`, R4's `rhythm_view`. Split from `clipper_render_plan.py` in R4 at 500 lines, on the seam the batches drew themselves — that file answers WHAT to render, this answers what to say about it, and nothing here may move a delivered frame. All three share ONE clock: the hop comes from the first and is handed down, so they cannot drift into describing different timelines and then be compared as if they described one |
| `clipper_render_jobs.py` | the export and preview jobs themselves: one ffmpeg encode each, plus Pass D. Also writes the sidecar the audit reads: since R0 it carries `render_version` (whichever renderer actually ran), `input_fingerprint` (computed through `edit_quality.input_fingerprint` over the recipe only — no titles, scores or ids), `drop_spans`, `caption_y`, and the source size. The caption plan stays STORED and the render-time height rides beside it, so the two decisions can still be told apart. Stamped at write time; the 58 pilot exports predate the keys and stay `unavailable` |

Not clipper: `remix_pipeline.py`, `parallel_pipeline.py`, `doodle_pipeline.py`,
`utility_jobs.py`.

## `server/routers/`

`clipper.py` (projects, settings, artifacts) and `clipper_clips.py` (clip-level
operations). Split to stay under the 500-line limit.

## Frontend

| path | what |
|---|---|
| `src/app/ai-stream-clipper/page.tsx` | project list and the create form |
| `src/app/ai-stream-clipper/[id]/page.tsx` | one project: progress, then the board |
| `src/components/clipper/source-form.tsx` | URL or upload, plus the settings |
| `server/routers/clipper_settings.py` | what a settings dict is allowed to say. Both HTTP entry points go through it, which is what stops a key from being dropped in silence |
| `server/routers/clipper_runs.py` | starting, cancelling, resuming and inspecting a run. Mounted under the same prefix; no URL changed |
| `src/components/clipper/pill.tsx` | the choice-group toggle every settings row is built from |
| `src/components/clipper/reasoning-mode-field.tsx` | which reasoning engine to use. Offers ONLY the modes the API accepts, and defaults to sending nothing so a rig configured in `config.py` is not overridden by the browser |
| `src/components/clipper/edit-mode-field.tsx` | which editing grammar to use, same shape and same reasons. `content_aware` is left out because the API refuses it |
| `src/components/clipper/analysis-progress.tsx` | the stage list during a run |
| `src/components/clipper/project-card.tsx` | one project in the list |
| `src/components/clipper/candidate-grid.tsx` | the board: sort, filter, bulk actions |
| `src/components/clipper/candidate-card.tsx` | one clip, with its actions |
| `src/components/clipper/clip-editor.tsx` | trim, headline, caption preset and height, over a server-rendered still |
| `src/components/clipper/score-breakdown.tsx` | the 16 sub-scores behind the number |
| `src/components/clipper/reasoning-panel.tsx` | why this clip — anchor, payoff, verdicts (§34), and since R2 the editing grammar it resolved to, with the reason in words. Displays what the backend resolved; the type-to-profile map is NOT repeated in TypeScript |
| `src/types/clipper.ts` | the clip, the project and the settings. No longer every clipper type: R2 took it to the 500-line limit and three groups moved out. It re-exports all of them, so `from "@/types/clipper"` still works everywhere |
| `src/types/clipper-reasoning.ts` | what the engine DECIDED and what Pass D thought: `ClipStory`, `ClipVerdict`, `ClipReasoning`, `ClipFinding`, `ClipReview` |
| `src/types/clipper-captions.ts` | `CaptionChunk` and `CaptionPlan` |
| `src/types/clipper-editing.ts` | `EditMode` and `EditProfile` — the editing decision, not the clip |

## Scripts

| file | what |
|---|---|
| `scripts/run_clipper_stage.py` | run ONE pipeline stage in-process, with `init_db()` first. The three traps it removes — stale schema, the worker on 8420, the Windows spawn guard — are in its docstring |
| `scripts/export_clipper_state.py` | transcripts to disk plus `data/clipper/MANIFEST.md` |
| `scripts/render_dynamic_clip.py` | drive the multi-shot renderer by hand |
| `scripts/build_dynamic_review.py` | the review page for a project's `dynamic/` |
| `scripts/measure_creator_presence.py` | how much of what the detector sees is actually the creator, per project, off the stored analysis. The evidence for R3a's anchor tolerance, and the check that a project with no anchor is left untouched |
| `scripts/build_shot_merge_fixture.py` | the R1 gate on the real corpus, and the generator of the versioned projection the test uses (`server/tests/fixtures/pilot_shot_plans.json`). Reports 1.341 → 1.225 off `data/clipper`; `--write` freezes it. The fixture exists because the corpus is private, mutable and about to change: a correct re-render rewrites those sidecars with the merged shot list, and a test reading them would then contradict the number it checks |
| `scripts/audit_clipper_exports.py` | rerun the v3 export audit on any set of projects. Reads only; `--all`, `--clips`, `--json`, `--output`. ENUMERATES the union of mp4s and sidecars and MEASURES their intersection, so `missing_sidecar` / `orphan_sidecar` / `unreadable_sidecar` / `mislabelled_sidecar` / `unidentified_sidecar` are findings rather than clips, and each measured sidecar is tied to the file it was found in — `clip_id` must match the filename and `project_id` the project being audited. **Exits 2** on any of those, on a refused artifact, on shots that do not join, or on a fingerprint that no longer matches its plan (an UNDECIDABLE boundary does not fail it — an old plan is old, not corrupt) (`unavailable` is fine — the pilots predate the key); a gate that always exits 0 is a report. Baseline on the four pilots: **58 clips, 1.341 shots, 29,3349/min pooled (29,5930/min as the mean of the four sources — different questions, both printed), 116 `fit → fit`, shortest 0,605s, 58/58 starting on the first word, 22/58 with ≤50ms of tail** |
| `scripts/prune_clipper.py` | reclaim disk from finished projects. Dry-run by default; never touches exports or analysis |
| `scripts/score_contribution.py` | weight x sd per sub-score — which one actually orders the board. Found dead features twice, three sessions apart |
| `scripts/score_content_type.py` | the content classifier scored against `source-labels.md` — 6/11 |
| `scripts/facecam_dataset.py` | 68 labelled candidate rects + what each feature separates |
| `scripts/facecam_train.py` | the classifier that lost to `corner_proximity`, leave-one-source-out |
| `scripts/measure_inset_border.py` | border coverage and persistence per candidate rect — the measurement that showed two facecams have no border at all |
| `scripts/evaluate_clipper_reasoning.py` | read the two traces off disk and report what happened: how much of the field the judge saw, how many story candidates reached it, chunk coverage, fallbacks. Reports, never asserts — run it before and after a change and diff the `--json` |
| `scripts/score_facecam.py` | facecam detection scored against `source-labels.md`. Run it before believing any change to the seed — baseline **9/9**, and it also diffs the RECTS against `docs/refs/facecam-golden.json`, because a change that keeps every count while moving the geometry reads as no change at all. `--bless` regenerates the record |

## Tests

One file per area, all pure — no ffmpeg, no network, no live model. The suite
runs against a throwaway data directory (see `tests/conftest.py`).

`test_clipper_analysis.py` (candidates, scoring, dedupe, layout) ·
`test_clipper_story.py` (story engine, promises, callbacks) ·
`test_clipper_atoms.py` · `test_clipper_threads.py` · `test_clipper_episodes.py` ·
`test_clipper_dead_air.py` · `test_clipper_segment_type.py` ·
`test_clipper_review.py` ·
`test_clipper_content_features.py` · `test_clipper_dynamic.py` ·
`test_clipper_dynamic_export.py` · `test_clipper_llm_select.py` ·
`test_clipper_ranker.py` · `test_clipper_render.py` · `test_clipper_signals.py` ·
`test_clipper_storage.py` · `test_clipper_captions.py` · `test_clipper_api.py` ·
`test_clipper_urlguard.py` · `test_clipper_resume.py` · `test_job_claim.py` ·
`test_clipper_regenerate.py` ·
`test_clipper_rhythm.py` (R4: a cut needs a reason and a place) ·
`test_clipper_rhythm_pace.py` (R4: the §4 bands, compared not enforced) ·
`test_clipper_rhythm_export.py` (R4 on the wire) ·
`test_clipper_transcribe_progress.py` · `test_transcriber_resilience.py` ·
`test_downloader_cookies.py`

## A project on disk

```
data/clipper/<project_id>/
  source/      the original
  proxy/       proxy.mp4 — EVERY analysis pass reads this, never the original
  audio/       speech.wav, 16 kHz mono
  frames/      sampled JPEGs, capped at clipper_max_sampled_frames
  analysis/    signals, faces, regions, regions_by_segment, segments, atoms,
               promises, threads, graph, anchors, segment_types, candidates,
               meta, transcript.json, reasoning_run, selection_trace
  exports/     <clip>.mp4 + <clip>.json sidecar + <clip>.ass
  previews/    low-res renders
  thumbs/      poster frames
```

`data/clipper/MANIFEST.md` lists every project and which stages are already
done. Regenerate it with `scripts/export_clipper_state.py`.

---

## Switches

Per-project keys in `clipper_settings`, each falling back to a `config.py`
default. Full table in `handover/archive/clipper/handoff-clipper-session-4.md`.

| switch | default | turns on |
|---|---|---|
| `dynamic_edit` | **ON** since 2026-08-17 | multi-shot export instead of one static split screen |
| `trim_silence` | off | dead-air removal from inside a window (§15) |
| `vision_review` | off | a vision model judges the rendered clip — the only part of the pipeline that spends money (~0.4 cents/clip on gpt-5.6-terra) |
| `auto_export` | 0 (off) | render the top N as soon as scoring finishes, instead of stopping at the board. With the source form's "don't wait for me" box, a pasted link becomes finished files with no second visit |
| `reasoning_mode` | resolved from config, `legacy` on a stock rig | which reasoning engine runs. Selectable: `legacy`, `llm_nominate`, `story_v1`, `story_v2_shadow`. Shadow computes and persists `shadow_rank` but keeps the delivered board legacy. `story_v2` is known but REFUSED until the golden review proves it better. Replaces `llm_select` + `reasoning_version`, still read for old projects; the form omits the key unless the user picks one, so it does not override server config |

`dynamic_edit` being on is what makes the rest of the multi-shot work reachable
— cuts on speech pauses, the wide gameplay framing, Pass D and the audio
ceiling all live on that path and nowhere else. It still falls back to the
static layout on a missing proxy, a plan the PLANNER built from fewer than two
shots, or any exception. Since R1 that threshold reads
`shot_count_before_merge`, not the surviving count: a clip whose only fault was
an invisible cut merges down to one shot, and counting that would have moved it
onto the static renderer — different crop, different captions, different
renderer version.

The current dynamic planner is **not production-approved** merely because v3
fixed geometry. The 58-export audit found 1,341 shots (29.3/minute), 116 exact
`fit → fit` no-op cuts, aggressive boundaries and source-awareness defects. R0
turned that audit into a gate and R1 removed the 116 from the planner — the
rendered exports still carry them until the pilots are re-rendered.
Implementation resumes at production-engine **Batch R2**, not by tuning
constants ad hoc.
