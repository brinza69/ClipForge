# AI Stream Clipper — the map

**Read this first.** Every file in the clipper, what it is for, and which
document holds the reasoning behind it. Verified against the tree on
2026-08-16.

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
| `candidate_groups.py` | MOMENTS rather than variants: a stable `moment_id`, one representative cut per moment, the budget that decides who the judge is asked about, and the labelled packet it is asked with. Reuses `dedupe._group` for the grouping itself |
| `selection.py` | which moments reach the board. When a judge ran the board is drawn from the moments it SELECTED, never from two score scales compared against each other; plus the round cap and the declared backfill. Runs in shadow until v2 has been compared against legacy |
| `dedupe.py` | overlap, text and same-payoff grouping; diversity across time, thread and archetype |
| `ranker.py` | the learned ranker. Complete, dormant, needs 40 labelled clips |
| `feedback.py` | recording what the user did with a clip, and — since the `origin` column — WHO did it. Only `manual` events label for training; `auto` (auto_export) and `system` are neutral, and so is the NULL origin of any row written before the column. The measurement that forced this is in the module docstring |
| `review.py` | Pass D (§21–22): what the CLIP looks like, checked before the encode |
| `review_vision.py` | Pass D's second half: a vision model on the RENDERED clip. Off by default, needs an OpenAI key |

### Models

| file | what |
|---|---|
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
| `segment_type.py` | content type per stretch rather than per file, and the signal slicing that allows it |
| `layout.py` | plan one 9:16 frame: which layout, which rects, which safe zones |
| `layout_geom.py` | the rect arithmetic behind it |

### Rendering

| file | what |
|---|---|
| `render.py` | the static path: one filtergraph, one encode, optional dead-air cuts |
| `captions.py` | the caption plan and its overlays |
| `dynamic_edit.py` | the multi-shot planner: where to cut, which camera, where the subject is |
| `dynamic_cameras.py` | the camera rungs and the action band |
| `dynamic_window.py` | the per-window signals the planner needs — dense face track, motion inside the game region, and `ui_panels`, the per-clip UI rectangles the caption keeps out of |
| `dynamic_render.py` | the shot list as one `-filter_complex` with `sendcmd` |
| `serialize.py` | DB rows to API dicts, and `effective_content_type` |

---

## `server/workers/` — orchestration, DB-aware

| file | what |
|---|---|
| `clipper_pipeline.py` | ingest, transcribe, analyze; registers all six handlers |
| `clipper_build.py` | the scoring stage end to end |
| `clipper_judging.py` | running the judge over pools of moments: which get asked about, how many rounds, and the verdict propagated to every cut of a judged moment |
| `clipper_cache.py` | what a previous run left on disk and whether it can still be trusted: the anchor stamp (prompt, mode, engines, duration, chunk plan) and the per-stretch content types. A checkpoint without a stamp is worse than no checkpoint |
| `clipper_finalize.py` | everything after the winners are chosen: layout + caption planning, headlines, the two trace artefacts, the `clips` rows, and auto-export. Split out when Batch 0 took `clipper_build` past 500 lines; re-exported from there |
| `clipper_render_plan.py` | WHAT a render will contain: window, shot list, layout, caption file, dead-air spans. `_decide_render` is the entry point and both handlers consume its one answer |
| `clipper_render_jobs.py` | the export and preview jobs themselves: one ffmpeg encode each, plus Pass D |

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
| `src/components/clipper/analysis-progress.tsx` | the stage list during a run |
| `src/components/clipper/project-card.tsx` | one project in the list |
| `src/components/clipper/candidate-grid.tsx` | the board: sort, filter, bulk actions |
| `src/components/clipper/candidate-card.tsx` | one clip, with its actions |
| `src/components/clipper/clip-editor.tsx` | trim, headline, caption preset and height, over a server-rendered still |
| `src/components/clipper/score-breakdown.tsx` | the 16 sub-scores behind the number |
| `src/components/clipper/reasoning-panel.tsx` | why this clip — anchor, payoff, verdicts (§34) |
| `src/types/clipper.ts` | every clipper type, including `ClipReasoning` |

## Scripts

| file | what |
|---|---|
| `scripts/run_clipper_stage.py` | run ONE pipeline stage in-process, with `init_db()` first. The three traps it removes — stale schema, the worker on 8420, the Windows spawn guard — are in its docstring |
| `scripts/export_clipper_state.py` | transcripts to disk plus `data/clipper/MANIFEST.md` |
| `scripts/render_dynamic_clip.py` | drive the multi-shot renderer by hand |
| `scripts/build_dynamic_review.py` | the review page for a project's `dynamic/` |
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
| `reasoning_mode` | resolved from config, `legacy` on a stock rig | which reasoning engine runs. Selectable: `legacy`, `llm_nominate`, `story_v1`. Known but REFUSED by the API: `story_v2_shadow` (until Batch 2 makes shadow stop reordering the board) and `story_v2` (until Batch 5). Replaces `llm_select` + `reasoning_version`, still read for the projects that predate it — see `services/clipper/reasoning_mode.py`. The form omits the key unless the user picks one, so a rig configured in `config.py` is not overridden by the browser |

`dynamic_edit` being on is what makes the rest of the multi-shot work reachable
— cuts on speech pauses, the wide gameplay framing, Pass D and the audio
ceiling all live on that path and nowhere else. It still falls back to the
static layout on a missing proxy, a plan with fewer than two shots, or any
exception.
