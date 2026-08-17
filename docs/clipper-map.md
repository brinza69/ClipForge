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
| the state of the world, known problems, traps | `handoff-clipper-session-4.md` |
| what every file is (this document) | you are here |
| why the reasoning works the way it does | `story-engine.md` |
| which brief requirement is built, section by section | `story-engine-spec-status.md` |
| ground truth for the detectors — what each source actually is | `source-labels.md` |
| how to run the pipeline by hand | `ai-stream-clipper-runbook.md` |
| the measured recipe behind the multi-shot edit | `dynamic-edit-recipe.md` |
| what is already on disk and can be skipped | `../data/clipper/MANIFEST.md` |

Older handoffs are history, superseded but not wrong about the code they
describe: `handoff-clipper-session-3.md`, `handoff-clipper-session-2.md`,
`handoff-dynamic-edit.md`.

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
| `clipper_score` | `clipper_build.handle_score` | `segments`, `atoms`, `promises`, `threads`, `graph`, `anchors`, `segment_types`, `candidates`, the `clips` rows |
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

### Choosing clips

| file | what |
|---|---|
| `candidates.py` | Pass C entry point; `generate_candidates`, `refine_boundaries`, `merge_nominations` |
| `candidate_boundaries.py` | where a clip starts and ends. Sentence snapping, reaction keep, tail release |
| `candidate_proposals.py` | turning anchors into candidate windows |
| `candidate_terms.py` | the frozen feature vector, the word lists, and the boundary constants |
| `dead_air.py` | dead seconds inside a chosen window, and the arithmetic of removing them (§15) |
| `scoring.py` | the sub-scores and the ten weight profiles |
| `dedupe.py` | overlap, text and same-payoff grouping; diversity across time, thread and archetype |
| `ranker.py` | the learned ranker. Complete, dormant, needs 40 labelled clips |
| `feedback.py` | recording what the user did with a clip |
| `review.py` | Pass D (§21–22): what the CLIP looks like, checked before the encode |

### Models

| file | what |
|---|---|
| `llm_select.py` | anchor detection and the nomination pass, chunked and versioned |
| `llm_judge.py` | comparative ranking from three perspectives (§18, §19) |
| `headline.py` | the clip's headline text |

### Seeing the frame

| file | what |
|---|---|
| `content_type.py` | the parts that need a decoded image: frame features, face boxes, region detection |
| `content_geom.py` | the pure half — rect maths, signal summaries, and the classifier itself |
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
| `clipper_render_jobs.py` | export and preview, static and multi-shot |

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
| `scripts/export_clipper_state.py` | transcripts to disk plus `data/clipper/MANIFEST.md` |
| `scripts/render_dynamic_clip.py` | drive the multi-shot renderer by hand |
| `scripts/build_dynamic_review.py` | the review page for a project's `dynamic/` |

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
`test_clipper_urlguard.py` · `test_clipper_resume.py` ·
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
               meta, transcript.json
  exports/     <clip>.mp4 + <clip>.json sidecar + <clip>.ass
  previews/    low-res renders
  thumbs/      poster frames
```

`data/clipper/MANIFEST.md` lists every project and which stages are already
done. Regenerate it with `scripts/export_clipper_state.py`.

---

## Switches

Per-project keys in `clipper_settings`, each falling back to a `config.py`
default. Full table in `handoff-clipper-session-4.md`.

| switch | default | turns on |
|---|---|---|
| `dynamic_edit` | **ON** since 2026-08-17 | multi-shot export instead of one static split screen |
| `trim_silence` | off | dead-air removal from inside a window (§15) |
| `auto_export` | 0 (off) | render the top N as soon as scoring finishes, instead of stopping at the board. With the source form's "don't wait for me" box, a pasted link becomes finished files with no second visit |
| `llm_select` + `reasoning_version: "story_v1"` | off | the story engine |

`dynamic_edit` being on is what makes the rest of the multi-shot work reachable
— cuts on speech pauses, the wide gameplay framing, Pass D and the audio
ceiling all live on that path and nowhere else. It still falls back to the
static layout on a missing proxy, a plan with fewer than two shots, or any
exception.
