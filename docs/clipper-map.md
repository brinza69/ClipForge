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
| `dynamic_rhythm.py` | Batch R4: WHEN the edit is allowed to cut, and why. **A cut needs a reason AND a place.** A reason is a declared change in what has to be seen — R3b's `treatment_change`, the source's own `source_scene_cut`, or an `action_beat` inside a stretch R3b MEASURED as action; a place is a boundary where the cut does not land inside a word, taken from `_boundaries` rather than reimplemented. A pause with nothing behind it is the cut this batch removes. The two kinds of reason are ASYMMETRIC: a treatment change is required and cuts even unsnapped and even against `min_shot_s`, with the violation recorded, because holding a crop on an emptied anchor trades a visible defect for an invisible metric — but a required change may NOT be absorbed by a cut at a different moment (two changes 200ms apart collapsing onto one pause hid the treatment between them), and one that would leave a runt final shot is refused into `required_conflicts` rather than shipped with a note: a 100ms flash is not a valid edit. `UNMEASURED` names what §4 asks for that nothing here measures — a keyword regex is not an emotion, and the active speaker is what §3.4 forbids guessing — which is why `talking_head` and `conversation` read `below`, and why that `below` is not a product gate |
| `dynamic_rhythm_vocab.py` | the closed lists R4 cuts by — the three cut reasons, the seven holds, the two required conflicts, what each profile ADMITS and what §4 asks it for that nothing measures. Split from `dynamic_rhythm.py` at 500 lines, on the seam the batch's own rule draws, and re-exported from it. Two entries are deliberately unreachable and documented as such — `required_collision` and the ordering guard in `place` — because the arguments that make them unreachable are subtle and a relaxed rule would otherwise lose a treatment in silence |
| `dynamic_rhythm_pace.py` | the §4 bands, compared against and never enforced. Split from `dynamic_rhythm.py` at 500 lines; nothing here may add or remove a cut. `action` is the one profile with two bands, and the split needs coverage COMPLETE plus spread — with a partial motion series the unmeasured seconds fall silently into `quiet` and a minute nobody measured returns a confident `below`, so both partitions go `unavailable` instead. A cut at an action boundary is a `transition` credited to neither band, counted so the three still sum to the total. `above` is checked BEFORE the short-window rule: three cuts in four seconds is 45/min whatever the ceiling's sampling needs, and only the lower bound needs room — `indeterminate` there hid a verdict the cuts had already demonstrated |
| `dynamic_window.py` | the per-window signals the planner needs, and since R3b the REAL step the motion series is on — `round(fps * hop)` whole frames, which is 0.2s on a 10 FPS proxy however politely 0.25 was asked for — dense face track, motion inside the game region, and `ui_panels`, the per-clip UI rectangles the caption keeps out of |
| `dynamic_render.py` | the shot list as one `-filter_complex` with `sendcmd`; current `RENDER_VERSION` is `render_v3_letterbox`, with full-frame `fit` over a blurred backdrop. **The subtitles are burned AFTER the overlay, and that is a defect a human found while every instrument here missed it:** `pad=...:color=black@0` makes the letterbox bars transparent so the blur shows through, and burning the caption onto that frame wrote the text into the colour planes while leaving the alpha at zero — so `overlay` composited it away. On a `crop` shot the frame is opaque and the caption survived; on a `fit` shot the caption sits in the bar by construction and vanished. Measured before the fix: **331 seconds across 27 of the 88 stored clips had no caption at all**, 7.7% of the corpus, one clip silent for 88% of its length. `caption_placement` had reported those 27 as "the caption lands on the letterbox band" — true about geometry, and it never asked whether the text was drawn |
| `dynamic_geometry.py` | the arithmetic on a plan: sizes, anchors, crop expressions and the `sendcmd` script. Holds `composition` — `crop` (a 9:16 window on a subject) and `fit` (the whole frame, letterboxed, for sequences with no subject) — and, since R1, `visual_key` + `merge_equivalent_shots`: what the viewer actually receives, and the merge that removes a cut only when the delivered picture does not change. The key is the size timeline plus the position expressions, so a shot that MOVES is never absorbed and identical shake still merges (the expression is a function of absolute `t`, so it continues unbroken across the join). Takes `ASPECT` from `dynamic_cameras`, its real owner — going through `dynamic_edit`'s re-export made the geometry import the planner |
| `serialize.py` | DB rows to API dicts, and `effective_content_type` |
| `boundary_completion.py` | Batch R5: is the chosen window a COMPLETE thought, and can one bounded move fix it. `refine_boundaries` already MOVES both edges and `extract_features` already SCORES them; neither answers whether what came out is finished — a score is a number to rank by, this is a verdict with the evidence attached. Closed lists: nine defects, four of them blocking (`start_inside_word`, `end_inside_word`, `end_mid_sentence`, `orphan_tail`), six refusal reasons for the ONE repair. **The hole it closes:** `ends_on_sentence = 1.0 if text.endswith('.!?…')` reads 0.0 for every clip cut from a transcript without punctuation, and 0.0 there is not "ends mid-sentence" — it is "nobody could tell"; `transcriber` strips punctuation unless asked not to. Without it the sentence checks are `unavailable` and `eligible` is None, so the board cannot lose a moment for the way its transcript was made. A cut inside a word stays measurable either way. `TAIL_TIGHT_S` is imported from `edit_quality` so the selection side and the render side count the same 22-of-58 defect. Recorded on every candidate as `boundary_view`; `eligible` decides nothing until the validator has passed the corpus, which is the plan's own condition |
| `evidence_map.py` | Batch R6: where a detected box lands in the OUTPUT frame — the mapper `caption_placement` has always said the caller owes it, and which nothing supplied, so its three occlusion signals were always `unavailable`. **Four transforms, read off the renderer rather than assumed:** proxy pixels (480x270 on the pilots) scaled X and Y SEPARATELY to source; `y += canvas_offset`, because `dynamic_render` PADS BEFORE IT CROPS and its comment explains why — `scale` fixes its output size at configure time, so letterboxing up front is what makes every crop 9:16 and a mapper that crops first describes a renderer this repo abandoned; then the shot's crop window; then one scale to 1080x1920. **The delivered window is built from the size timeline and the ANCHOR, never from `shot["rect"]`:** a single-point timeline proves constant SIZE, not identity with the planner's rectangle, and 837 of the corpus's 1,965 `crop` shots have a delivered window that differs from it — the planner rounds down to even while `_size` rounds to the nearest and `_anchor` re-clamps the centre, so `(144, 288, 444, 792)` is delivered as `(143, 286, 446, 792)`. Reading the rectangle moved 140 shots' mapped boxes by up to 4 output pixels. It REFUSES rather than approximates in four places: a shot whose size timeline has more than one point, a shot that cannot be read at all (distinct from one whose crop moves — the first version returned `MOVING_CROP` for a shot with no `t0`, a specific claim about geometry made about a record nobody could read), missing proxy dimensions (`dynamic_edit` falls back to `or src_w`, assuming a scale of 1 where the real one is 5.3, and this does not copy that), and a box that is not four finite numbers. A box that MISSES the crop is `off_frame`, which is neither a refusal nor a zero: it is not in the delivered picture. A partly unreadable signal becomes `None` rather than a shorter list. **The other two signals stay `unavailable` for want of a DETECTOR, not a mapper:** `regions.hud` holds 8 rectangles across all 20 projects and every one is exactly 60x44 at (0,224) or (420,0) in a 480x270 frame — `content_geom`'s corner heuristic emitting a fixed box, not a measured extent — and `regions.chat` is `None` in all 20; nothing produces per-shot text rectangles at all, since `source_captions` answers whether the SOURCE has burned subtitles and reports band indices, not boxes. Mapping the corner boxes would report "the caption covers this much of the game UI" out of a rectangle whose size nobody measured, which is the ancestor of the 66% claim. **It is not `panels_to_keep_out` and must never become it:** that function skips face shots because mapping a game panel through a face crop is arithmetic with no referent at 5-8x, and a FACE through a FACE crop has one — the crop was built around that face, and the transform here is the renderer's own |
| `caption_placement.py` | Batch R6, second half: what the burned caption actually LANDS ON — face, game UI, the source's own text, or the blurred letterbox band. Recorded, applied to nothing. **THREE AXES, kept apart:** `conflicts` is what was measured, `unavailable` is what nobody supplied, `refused` is what somebody supplied wrongly — folded together, a NaN caption height reported "nobody set one" and a shot whose evidence was the number 3 reported all three signals as merely missing, with a `worst` computed over it. A refused record joins no count. `share` is the UNION across signals and boxes, never a maximum: a face over 39.6% of the caption and text over a different 39.6% is 79.2% unreadable, and max-of-a-box-then-max-of-a-signal called a single 60% face worse. It also says `share_complete` — a union over the signals that were measured is a FLOOR, not a coverage — and `share` is `None`, never 0.0, when nothing was measured at all. It never averages across shots, because a face crop makes the face fill the output while a game shot puts it in a small inset — the same error that once reported "66% of the caption sits on game UI" for captions sitting on a hoodie. **Its evidence is PER SHOT in output pixels and the CALLER maps it**, which is not a job `panels_to_keep_out` can do on its own: that function deliberately SKIPS face shots, and its comment says why — mapping a panel through a face crop is arithmetic with no referent at a scale factor of 5-8x. Faces and source text need their own mapper; there is no generic one. **And the contract for that mapper is measured, not reasoned:** `shot["rect"]` looks like the crop the renderer takes and is not — `dynamic_geometry.visual_key` says the picture is what `build_sendcmd` schedules, and refuses any shot whose size timeline has more than one point. A mapper projecting a face through the rectangle is right only while every timeline is a single point, which today it is: `_size_timeline` returns one point for all 2,126 stored shots. But 2,024 of them are LABELLED `move: push`/`pull` and stand still only because `push_amount` is 0.0 in every stored style — reading the label instead of the timeline says "95% of shots move", the opposite of the truth. So the mapper reads the timeline and REFUSES a multi-point shot rather than approximating it, and the audit counts them. The letterbox comes from `dynamic_geometry.canvas_size`, not from a `frame` key on the shot — no shot has ever carried one, 161 `fit` shots, all inside the 58 sidecars of the pilot corpus, 27 of which contain at least one, zero with either key — and it is kept OUT of `share`: it occludes nothing of the source, which is not a claim that the text is readable there, since `render_v3_letterbox` fills the strip with a blurred copy of the frame and nothing here measures contrast. `COMPOSITIONS` is ENFORCED, not described: an unknown composition is refused rather than treated as a `crop`, and a shot that names none is `unavailable` for the letterbox question only — with `None` and a missing key the ONLY two absences, since `or ""` was turning a composition of `0`, `False` or `[]` into "nobody said". Overlap is a share of the CAPTION, not of the rectangle. `evidence` holds exactly the three `OCCLUSIONS` and nothing else — the letterbox has its own `off_source`, because `evidence` and `share` are the same thing at two granularities and a non-occlusion in the same dict produced a shot reading `evidence: {on_letterbox_band: 1.0}` beside `share: 0.0`, both right and the pair unreadable; `off_source` is None on a `crop` and None when the source size is unknown, never 0.0. More evidence entries than shots is a REFUSAL — the caller's idea of the edit and the edit disagree, and the extras were being sliced off in silence; a shorter list stays legitimate, since the shots it does not cover come back unavailable |
| `caption_placement_vocab.py` | the closed lists the placement report may use, and nothing else. Split from `caption_placement.py` at 500 lines on the seam the batch is about: `LANDS_ON` (measured to be there), `UNAVAILABLE` (nobody supplied it), `REFUSALS` (somebody supplied it wrongly). `LANDS_ON` and not `CONFLICTS`, which is what it was called — three of its four members are things the caption COVERS and the fourth is a place it SITS, so the name said the opposite of the comment beside it. `OCCLUSIONS` is the three that are, and is exactly what `share` unions. Same pattern as `dynamic_rhythm_vocab.py`, and for the same reason: a reviewer can check the axes against each other without reading the arithmetic that fills them |
| `caption_placement_geom.py` | a caption band, a rectangle, and how much of the first the second covers. The arithmetic under `caption_placement`, split out at 500 lines; it knows nothing about shots, evidence or reports. Two rules live here as comments because they were expensive: `_rows` returns None for a rectangle it cannot read and None is UNREADABLE — it used to flow into `overlaps`, which answers 0.0, an absence of measurement presented as a measurement of absence — and a rectangle that does not FIT the frame is unreadable too, because `min(1.0, ...)` used to clamp it in silence: these boxes come from a caller's mapping, and a mapping that puts a face off the bottom of the output has failed, so reporting it as "covers nothing" is the 5-8x scale error in a new place (2px of slack, the same `source_captions` allows its detector); and `source_band` returns a PAIR, because its None has two meanings, "measured, and there is no letterbox" and "nobody supplied the dimensions". `covered` is a union rather than a maximum, which is the whole of §R6's "how much of the caption can nobody read" |
| `caption_choice.py` | Batch R6: WHY the delivered caption sits where it does — the proposed position, the alternatives that lost, and the reason the winner won, none of which existed before: `resolve_position` returns two floats and forgets the search. **It explains, it does not re-implement**, which is the rule R5 paid for when `_snapped()` copied `_fit`'s guards to report on them and the copy drifted. The answer comes from `resolve_position` called; the preset from the same function with an EMPTY layout; the grid from `captions.scan_grid(*captions.scan_bounds())`, the same two calls the search makes; coverage from `captions._overlap_area`, imported rather than restated. The first version rebuilt the grid from the ROUNDED return values with `int(round(...))` where the search uses `int(...)` — 61 candidates against the search's 60, the extra one at 0.7542 above a real `hi` of 0.75, a position that could never have been chosen reported as one that lost. The candidate bands are the ones the search FILTERED TO — `_widest_band(inside or clear)`, where `inside` is the clear positions clipped by the 55-75% clamp — and not the clear runs labelled `in_spec` by where their centres fall: that version reported a run from 0.1542 to 0.6742 as `taken` and `in_spec: false` beside a reason reading "inside the spec clamp", a contradiction inside one payload, and the sweep written to catch it asked the report whether an in-clamp band existed, so it found nothing. A check that consults the representation it is checking compares it with itself. A run the clamp only TRIMMED reports `clipped_from`; one it removed entirely is in `excluded_by_clamp`. `UNEXPLAINED` is in the vocabulary and is reachable — a reason that always finds one is a label applied after the fact. A search that never ran chose nothing and rejected nothing: when the preset is clear `resolve_position` returns before the scan, so `searched` is false, `rejected` is empty and no band is `taken`. **And the `bottom` preset is not reachable by the scan at all** — it is exactly `hi` at 0.75 while the grid stops at 0.7442, because 59.58 steps truncate to 59, so a caption left at its preset sits where the search could not have put it; `chosen_inside_a_clear_run` says so rather than leaving it to be inferred. **The finding it carries rather than fixes:** a non-finite keep-out rect clears `_norm_rect` — every comparison against a NaN is false — and then overlaps the caption box at EVERY scan position, so one malformed entry erases the band search and MOVES the delivered caption (measured: 0.3092 → 0.4642 on one facecam rect plus one NaN rect). Live behaviour in `captions`; this batch counts it and does not move delivered captions |
| `captions_geom.py` | the caption box, the positions it may take, and the scan between them: `CAPTION_BOX_H_PCT` / `_W_PCT`, `CLIPPER_CAPTION_CENTER_PCT`, `SCAN_STEP_PCT`, the 55-75% spec clamp, `_base_y_pct`, `scan_bounds`, `scan_grid`, `_widest_band`. Split out of `captions.py` (which was 520 lines, over the limit) and split HERE because this is the part a second reader needs: `caption_choice` reports which positions the search considered, and a rebuilt copy of the grid put a candidate above the upper bound. One grid, and both the search and the report ask for it. Everything is re-exported from `captions` so callers are unchanged |
| `caption_contrast.py` | Batch R6: what the caption palette guarantees against ANY backdrop. §R6 asks whether the caption passes the contrast on the blurred letterbox band, and the corpus says that is not a corner case — 27 of the 27 clips with a `fit` shot and known geometry have the caption on that band. **The answer is arithmetic, not a frame sample.** An outlined glyph separates from any backdrop by one of its two colours — the outline works against a pale backdrop, the fill against a dark one — so there is a FLOOR, and no frame can be below it. SOLVED, not swept: the floor is the SQUARE ROOT of the contrast between the glyph's own two colours — `sqrt(21) = 4.58` for white in black, at relative luminance 0.179. The first version swept 256 greys with a comment claiming the closed form only held for extreme pairs; it holds for every pair, and the sweep was the approximation, erring high by 0.025 because a backdrop pixel is 8-bit per CHANNEL while a coloured pixel's luminance lands anywhere between. **The finding is in the palette, not on the letterbox:** every fill clears both bars (4.39-4.58), but the HIGHLIGHT colour the karaoke animation paints does not — `Neon Pop` 2.33 and `Viral Gradient` 2.72 cannot GUARANTEE 3.0:1, and no frame sample would have named it. **That sentence has a precise reading and a loose one, and the loose one is false:** a floor of 2.33 means there EXISTS a backdrop luminance where the separation falls that far, not that the caption never reaches 3:1 — on most real backdrops it does. The honest one-line form is "the minimum separation this palette can guarantee, assuming a uniform backdrop, an opaque fill and a visible outline", never "the rendered contrast passes". An unreadable colour is REFUSED, never defaulted to black: black is one end of the luminance range and would be the most favourable possible answer. An alpha channel is READ rather than guessed at: `hex_to_ass_color` turns `#RRGGBBAA` into `&HAABBGGRR` and `_rgba_from_hex` hands the same byte to `pysubs2.Color`, and both default a 6-digit colour to `a = 0`, so this codebase uses ASS transparency where 00 is opaque. An 8-digit colour with alpha 00 is read; a translucent one is refused for a reason rather than out of doubt, because a translucent glyph composites with the backdrop and the floor argument assumes two fixed colours — and a style whose `outline_width` is zero or missing is refused, because every number here assumes two colours are on screen and a bare fill's worst backdrop is its own colour at 1.0. It is a LUMINANCE floor — WCAG ignores hue — over a UNIFORM backdrop, which is what a blur produces and a `crop` over detailed source does not; and the thresholds are borrowed from WCAG 2.1 AA, so `calibrated: false` rides with every verdict |
| `source_chrome.py` | Batch R6: is a video PLAYER's own chrome burned into the export — `detected` / `not_detected` / `unavailable`, and a WARNING about publishing rather than a verdict about it. **Runs on the rendered EXPORTS, not the proxies, and that is why it found anything:** eight uniform frames of each source proxy found nothing and the conclusion written from that was "the corpus has no positive", while the v2 human review had already listed browser UI among the v3 renderer's defects. Measured at one frame per second: **14 of 14 Moist exports carry it** (5 to 32 frames with a hit each — `Watch later` at the top, `SHARE` and `SAVE` at the bottom) against **27 exports from ten other projects with ONE hit between them**, a single `Search` in one frame out of 1,222 frames and 4,468 tokens. So two frames separates them completely, with a margin of 5 against 1. **The hypothesis I brought was the wrong one:** the URL family produced 39 matches on the positives and 0 on the negatives, and every one of the 39 was at confidence 0.00-0.01 — garbage like `NOU:L Do.com catchvechackndt`, zero real URLs — so it is not in the module at all; the discriminator that works is a control label from a closed vocabulary, read confidently, in more than one frame. `not_detected` NEVER means clean: the recogniser read eight tokens and none above confidence 0.5 on `pilotf81b`, the one source known to carry burned captions, so recall is low and undemonstrated. The denominator is frames ANALYSED, never sampled — and it cannot be handed in: `classify([1, 1], analysed=6)` answered `detected`, honouring a `SAMPLES_MIN` of 6 over a list of two, so the counts and the denominator must now agree exactly. An impossible read fails the WHOLE frame atomically: a NaN confidence passed `value < CONF_MIN` — every comparison against a NaN is false — and manufactured a `detected` out of a model that had failed. And the CADENCE travels with the verdict and is refused if it is not the measured one, because `FRAMES_MIN` counts frames and two frames is 0.1 seconds of video at one cadence and 60 at another. Thresholds chosen with the answer visible on 14 positives from one source and 27 negatives from ten — more than `source_captions` had, still not a calibration, and `calibrated: false` rides with every verdict. One thing deliberately NOT used and written down so it can be tested rather than adopted: the single false positive sits at y=0.551 while every true hit clusters at y<=0.083 or y>=0.911, so a position rule would separate them perfectly and would be a threshold chosen on one negative sample |
| `source_captions.py` | Batch R6: does the SOURCE already carry burned-in subtitles — `present` / `absent` / `unknown`. The defect is in the baseline: 15 of 15 go ghost exports shipped with TWO caption systems and nothing could tell. **`present` is the expensive answer**: it is the state that DISABLES ClipForge's own caption layer, so a wrong one ships a clip with no captions at all. `absent` keeps the layer, `unknown` changes nothing. `present` is reachable on one band clearing both bars; `absent` needs EVERY band clearly negative — judging only the busiest band let a watermark at 14/14 mask a real caption track at 9/14. The denominator is what the model ANALYSED, not what was sampled, because a detector throwing on every frame leaves bands that look exactly like a source with no text. Uses `easyocr`'s locally cached CRAFT weights, detection only, ~5s per source; the dependency is OPTIONAL and its absence costs the run nothing. What separates a subtitle from a HUD label is three properties together and no one of them alone — one band, persistent, and WIDE: `pilot6b38`'s watermark is in 13 of 14 frames, MORE persistent than the true positive, and 0.079 of the frame wide against 0.45. **Thresholds chosen with the answer visible on four labelled sources**; `calibrated: false` rides with every verdict. Two brightness heuristics failed first — see `docs/clipper-caption-detection.md`, which exists so nobody runs them again |
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
| `scripts/audit_caption_contrast.py` | Batch R6: the contrast floor over two populations that are not the same question — the seven shipping presets (what any future clip can be given) and the distinct palettes actually burned into the 101 exports. **Measured: 2 of 7 presets cannot guarantee the bar** (`Neon Pop` 2.33, `Viral Gradient` 2.72 on the highlight), and both are ACCEPTED as of 2026-08-31 — neither has ever been used, and `Neon Pop` cannot reach 3.0 without ceasing to be a saturated pink. The exception lives in `caption_contrast.KNOWN_SHORTFALLS` and is keyed on the palette — the style's OWN name plus the three colours the floor is computed from plus the outline width — so repainting one into something worse fires the gate again. The name comes from the style rather than from the caller, because the audit labels a preset by its id and a stored export `project/clip (x99)`: the first version matched the exception in one population and not the other, so the first real use of an accepted preset would have made the presets pass and the exports fail on the same palette; and the pass line names them, because "every palette clears the bar" printed over two that do not is a green run contradicting its own table, and the disk holds 2 distinct palettes — 99 exports on `Bold Impact` (4.58 fill / 3.87 highlight) and 2 with no style at all, which is a refusal with its own row. Fails the run on a refusal — including a `caption_plan` or a `style` that is present and not a record, which is not the same fact as an absent style — on either colour below the large-text bar, and on an empty population: this script's method is a sweep, and a sweep over nothing is silent and green. A style that is ABSENT is counted and not failed on, the same way the placement audit treats the same fact — an export can genuinely carry no captions, and neither instrument can tell that from a sidecar that lost the style. Tested through `main()` in `test_clipper_contrast_audit.py`, where the empty-population case lives: this script's method is a sweep, and a sweep over nothing prints "every palette clears the bar" over no palettes at all |
| `caption_corpus.py` | Batch R6: ONE export's caption placement, measured. Split out of `scripts/audit_caption_placement.py` at 500 lines, on a real seam — this measures one sidecar and knows nothing about denominators, gates or exit codes — and it puts the measurement somewhere a test can import, which `scripts/` is not. The analysis directory is derived from the sidecar's PATH rather than from a second `CLIPFORGE_DATA_DIR` read at import time: the two constants disagreed the moment a test pointed the environment elsewhere, and the script saw the temp corpus while the module saw the real one. Refuses a sub-record that is not a record, an assumed output height, and unreadable face inputs — and keeps a sidecar with no `source_start` apart from those, because it voids its own face column and nobody else's |
| `scripts/audit_caption_placement.py` | Batch R6: runs `caption_placement` and `caption_choice` over the 101 sidecars on disk. Two questions, because only two have answers: can the delivered position be explained by today's rule, and does the caption land on the letterbox. The face, UI and source-text signals come back `unavailable` on purpose — nothing maps them into the output frame yet, and `panels_to_keep_out` skips face shots, so there is no mapper. **Measured: 27 of the 27 clips with a `fit` shot and known geometry have the caption on the letterbox band**, so §R6's contrast question is the state of every letterboxed export rather than a corner case. And **8 of 99 stored positions are ones today's rule would not produce** — it checks the position the clip's own style ASKED FOR, not any of four presets, which is what raised the count from 2: six clips in `2d3375ee3420` sit at 0.51, the `center` value, under a style that says `bottom`, and the lax check called all six explained. One of the eight (`0c9685df852b/205a6ec12b00`, y=0.75) sits on 7.8% of a face keep-out the current rule moves it off. The rendered height is READ off the mp4, never assumed at 1920 — a fallback exists and declares itself and fails the run. A project asked for and not found is a refusal, not a smaller corpus. A sidecar with no caption plan is counted as `unavailable` and does NOT fail: an export can genuinely carry none. Tested through `main()` — `test_clipper_caption_audit.py` — which is where four of its own defects were found. **Since `evidence_map` exists the face signal is answered too, and the first real numbers are:** 2,126 shots, of which **964 have no face sample in their window** — the detector runs about every two seconds and a shot is one to four, so nearly half the corpus is `unavailable` rather than clean — 1,162 have faces mapped into the output, 161 hold more than one sample so their answer is "at any point in the shot" rather than "throughout it", 459 face boxes miss the crop entirely, and **26 of the 99 placed clips have the burned caption landing on a detected face**. That 26 is a FLOOR on every axis, and the report says so: those 964 unsampled shots, plus the UI and source-text signals which have no per-shot detection at all, mean **zero clips have a fully established worst case** — printed, because otherwise "26 clips" reads as "and the other 73 are clean" |
| `scripts/detect_source_chrome.py` | Batch R6: runs the chrome detector over the rendered exports. `--expect detected|not_detected|unavailable` turns it into a gate; without it it only reports, because a detector graded by whoever tuned it is not being graded. An export whose video cannot be read gets a row and fails the run, and a project asked for and not found is a refusal rather than a smaller corpus. The pass line says "every export was decided; `not_detected` is not `clean`", never "no chrome found" |
| `scripts/detect_source_captions.py` | Batch R6: runs the detector over each project's proxy. `--expect present|absent` turns it into a gate against the v2 human review — 4 of 4 on the pilots. NOT against `docs/source-labels.md`, which identifies the sources and carries no caption labels at all. Without `--expect` it only reports, because a detector graded by whoever tuned it is not being graded. A missing proxy gets a row and fails the run |
| `scripts/measure_boundary_snap.py` | Batch R5a: what the final-end snap MOVES, measured on the corpus as stored, before anybody re-scores. 261 truncated windows -> 0, none refused for the minimum, the maximum or the media; 260 pushed out, 1 pulled back; median 0.10s but p90 0.66s and 120 of 261 over 0.15s, so the move is not small. Three exceed 3s on transcripts carrying a 5.72s word — the snap is still right, and no word-length guard was added because that would be a new uncalibrated constant. It measures the MOVE, not its consequence: what it does to scores, dedupe groups and the board needs a re-score on a clone |
| `scripts/audit_clipper_boundaries.py` | Batch R5's gate: aggregates the recorded `boundary_view` over `candidates.json`, per project and pooled. Re-measures nothing, so the audit and the pipeline cannot disagree about what a defect is. A candidate with no verdict is `missing`, never "clean"; a project where EVERY candidate lacks one is named `predates_r5_rescore_needed` instead, because that is a thing to do rather than a bug to chase. Validates every key the schema promises BEFORE counting — `view.get("defects") or []` on a record that never carried the key reads as a clean window, and a corpus of holes aggregates into a pass. Prints the denominator before the count for the same reason. `--recompute` loads the transcript from the DB and runs the canonical `attach` over stored windows, measuring the RULE on a corpus scored before R5 without re-scoring it — which is not the same evidence as the pipeline having written it, and the report says so. Also prints the corpus tail distribution, which is what `TAIL_PAD_S = 0.40` would be re-derived from rather than inherited. **Exits 2** |
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
`test_clipper_boundary_completion.py` (R5: is the window finished) ·
`test_clipper_boundary_audit.py` (R5's gate: what the corpus report refuses to count) ·
`test_clipper_boundary_snap.py` (R5a: the end lands off a word) ·
`test_clipper_snap_gate.py` (R5a's gate, through `main()`) ·
`test_clipper_source_captions.py` (R6: is there text burned into the source) ·
`test_clipper_source_chrome.py` (R6: is a player's chrome burned into the export) ·
`test_clipper_chrome_gate.py` (R6: what reaches the chrome gate's exit code) ·
`test_clipper_caption_contrast.py` (R6: what the palette guarantees) ·
`test_clipper_caption_placement.py` (R6: what the caption lands on) ·
`test_clipper_evidence_map.py` (R6: where a detected box lands in the output) ·
`test_clipper_caption_placement_refusals.py` (R6: what it refuses to say) ·
`test_clipper_caption_choice.py` (R6: why it sits there, and what it beat) ·
`test_clipper_caption_choice_census.py` (R6: what it refuses, and the keep-out census) ·
`test_clipper_caption_audit.py` (R6: what reaches the placement gate's exit code) ·
`test_clipper_contrast_audit.py` (R6: what reaches the contrast gate's exit code) ·
`test_clipper_delta_gate.py` (R5a's delta tool, through `main()`) ·
`test_clipper_rhythm.py` (R4: what earns a cut) ·
`test_clipper_rhythm_place.py` (R4: where it lands — every review finding) ·
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
