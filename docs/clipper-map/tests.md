# AI Stream Clipper map — the test files

Part of the clipper map; start at [`docs/clipper-map.md`](../clipper-map.md).

## Tests

One file per area, all pure — no ffmpeg, no network, no live model — except where a row says it needs
ffmpeg. The suite runs against a throwaway data directory (see `tests/conftest.py`).

`test_clipper_analysis.py` (candidates, scoring, dedupe, layout) ·
`test_clipper_reaction_layout.py` (explicit content fit, bounds refusals, HUD mapping, common export sidecar and decoded edge/shape markers) ·
`test_clipper_reaction_editor.py` (source-frame API, manual save/clear, binding and ordinary render) ·
`test_clipper_reaction_editor2.py` (resolver failure propagation, frozen source token, actual ASS/MP4 caption and preview checks) ·
`test_clipper_reaction_binding_regressions.py` (independent source clock/pixels, invalid-window and malformed-plan refusals, recovery) ·
`test_clipper_reaction_captions.py` (blur-gap placement, supported envelope, bypasses and refusal behavior) ·
`test_clipper_reaction_gap_regressions.py` (suppressed/removed layers, actual preset style and ASS-control counterexamples) ·
`test_clipper_reaction_caption_hint.py` (the no-gap refusal names the tallest content box that leaves a caption slot, found by the save's own builder and resolver) ·
`test_clipper_clip_caption_source.py` (a clip's own source-subtitle answer: precedence over the project, unchanged dicts without it, the endpoint and its feedback event, both decision sites, the ALTER on an existing table, turning the layer on over a framing with no caption slot, and that a rescore keeps the answer) ·
`test_clipper_mutation_atomicity.py` (an edit and its event are one transaction; races forced with explicit barriers between two SQLite connections) · `test_clipper_caption_source_migration.py` (the ALTER on an old DB, twice) · `test_clipper_project_caption_source.py` (the project answer: strict values, no rescore, only flipped inheritors invalidated, 422/409 with zero writes) · `test_clipper_*_r.py` (Codex's wave-1 corrections: headline snapshot, metadata events, busy lock, export_invalidated, policy in every response, two OS processes, reject reason; R4b: the atomic submit, one export identity, claim + enqueue reconciliation; `test_clipper_export_attempt_r.py`: review F1/F2/F3/F6; `test_clipper_export_current_r.py`: R4c — the record keeps the executed paths, a stale `.ass` is not a layer, `/export-file` refuses a non-current export; `test_clipper_caption_on_demand_r.py`: D2 — an alternative's plan built per render, suppress/manual/empty/untimed, preview = export, MP4 probes; `test_clipper_caption_d2r_r.py`: D2r — Codex's mixed timed/untimed counterexample refused, `empty_after_remap`, concurrent warnings writes, `warnings` shape, argument parity with regenerate/finalize; `test_clipper_caption_d2r2_r.py`: D2r-2 — point timestamps admitted and concordant with the builder, stale preview publication checked by file hash and row, legacy `warnings` on the read path; `test_clipper_project_attempts_r.py`: D2r-3 — the project error is the latest analysis attempt's, `/retry` refused on ready/active with zero jobs, `last_preview` latest-not-last-failed in one query, `discarded` from the marker only) · `test_clipper_review_cohorts.py` (B3/B3r: which runs can be reviewed blind; substitution, permutation, duplicates and old traces are never proof) · `test_jobs_no_redirect.py` (GET /api/jobs answers without a slash, so the sidebar badge stays inside the proxy) ·
`test_clipper_end_acoustics.py` · `test_clipper_end_tail.py` (EN3: every guard, off by default, never earlier, through refine_boundaries) · `test_clipper_end_cases.py` (the five E0 endings, fixtures in `tests/data/endings/`) · `test_clipper_end_en2.py` (R1 later-only, R2 truncated WAV) · `test_clipper_end_offloop.py` (R3 through the real JobQueue + handle_score: heartbeat during a blocked refinement, cancel / lost ownership stop and publish nothing, retry = in-loop results) · `test_clipper_end_tail_sidecar.py` (EN3T/EN3Tr: the binding, record validation, the tolerance) · `test_clipper_end_tail_render.py` (EN3Tr: real-encoder witnesses — dynamic, other-duration plan, short source, static, trim, no probe) · `test_clipper_end_tail_replay.py` (next-29 §3: a present record kept, legacy absent, invalid refused, changed end not applied, same recipe) ·
`test_clipper_ow1_races.py` (OW1: orphan vs retry both orders, refused publication by cause, failures, one score, the stop's bound, new upstream) · `test_clipper_ow1_readers.py` (OW1: manifest refusals, pinned readers, retry/rescore routing, late G1 board, G1 reader after G2, cleanup after the last thread) · `test_clipper_ow1r2_claim_publish.py` (OW1r2 R1/R2: takeover before capture on the same and on another queue, no claim → no write, cancel/error after a commit keeps the generation, the caller's cancel waits for the commit, rollback before commit, G1 replaced by G2 before its cleanup) · `test_clipper_ow1_stop_attempts.py` (OW1r2 R3: two attempts of one job — refused old cancel / old heartbeat signal only their own attempt, the unregistered-task case, a person's cancel; the queue's signal proven with the handler's `finally` held) ·
`test_clipper_proxy_clock.py` (the replica's arithmetic and refusals; real-ffmpeg tie and 20 s recipe probes — needs ffmpeg) · `test_clipper_proxy_clock_evidence.py` (every VALIDATED_CLOCKS evidence file is in the repo, its sha256, and the numbers it claims) · `test_clipper_proxy_provenance.py` (the recipe = the old command, full hash + cache, legacy = provenance_missing, no stale; real media — needs ffmpeg) · `test_clipper_proxy_verification.py` (AD12-r: cancel between hash blocks, cache vs full verification, build identified before the encode, refusal on change) · `test_clipper_scene_address.py` (AD3, pure: rows/legacy join, zero/negative/duplicate/rounding-collision counts, the state table row by row, isolation verdict and grab binding incl. the showinfo lookahead frame, the source reader's refusals, no global bump) · `test_clipper_scene_address_real.py` (AD3, `real_ffmpeg`, skips off the validated build: six rates on every phase, windowed == whole-file reader, start/end, changed neighbours, identity changes, decode error ≠ zero scenes and unknown build through `build_signals`) · `test_clipper_scene_address_optin.py` (AD3c: the 3b pass is off by default; off keeps the 3a facts as `not_requested`, never calls the addresser, and a failed decode keeps its own state) · `test_clipper_window_address.py` (CC1, pure: stats parsing, the twelve phases, face time vs the FIRST window frame, gap/duplicate/off-grid rows, every window state, build/recipe/decoder changes and — C1r — every missing or null reader component, the panels reader certified on its own, coverage kept on refusal) · `test_clipper_window_address_real.py` (CC1, `real_ffmpeg`: barcode witness on every addressed point, the ±1-slot slip caught by the barcode, gap, drop3, shifted and unprobeable sources, damaged windows, default-off argv/result identity) · `tests/data/proxy_clock/` (the evidence itself, `-text`; its README has the reproduction command) · `test_job_heartbeat_lease.py` (heartbeat on a controlled clock: exact expiry, the same-tick case, the fenced old owner) ·
`test_clipper_rescore_keeps_edits.py` (a rescore keeps a clip a person worked on, whole: the event set, legacy NULL origins, machine origins, overlap with fresh candidates, the shared pre-dedupe predicate, other projects, and no unattended render slot for a kept clip) ·
`test_clipper_story.py` (story engine, promises, callbacks) ·
`test_clipper_atoms.py` · `test_clipper_threads.py` · `test_clipper_episodes.py` ·
`test_clipper_dead_air.py` · `test_clipper_segment_type.py` ·
`test_clipper_review.py` ·
`test_clipper_blind_review.py` (rubric, shuffle, membership and separate evaluation feedback) ·
`test_clipper_review_media.py` (S8a: full-file identity, frozen presentation, incomplete/mixed-run boards and real review API refusal) ·
`test_clipper_review_seal.py` (S8b: no partial disclosure, immutable/idempotent answers, actual concurrent requests, cross-process locking and retry after feedback write failure) ·
`test_clipper_content_features.py` · `test_clipper_dynamic.py` ·
`test_clipper_face_framing.py` (local/median conflict, fixed-anchor regression, actual encoded crop pixels) ·
`test_dynamic_face_envelope.py` (editorial span, temporal membership and effects) ·
`test_dynamic_face_envelope_geometry.py` (delivered geometry and bounded spatial scope) ·
`test_clipper_framing_contract.py` (independent association/rounding/refusal counterexamples, Minecraft false target, moving target verified in MP4 pixels) ·
`test_clipper_gap_contract.py` (independent decoded-pixel tracking, stops, raw-state preservation and actual renderer integration) ·
`test_clipper_gap_consumer_contract.py` (independent contradictory-provenance counterexamples, exact seed and fixed-anchor protection) ·
`test_face_gap_framing.py` (motion consumer authorization and provenance) ·
`test_clipper_dynamic_export.py` ·
`test_clipper_render_output_contract.py` (shared output options/sidecar contract) ·
`test_clipper_shared_export.py` (real encoder: normal/replan/frozen replay, decoded captions, cancellations) · `test_clipper_llm_select.py` ·
`test_clipper_caption_clock.py` (decoded caption timing and per-word clock) · `test_clipper_editor_frame.py` (API edits, PNG versus MP4, suppression, stale exports, frame-index selection) ·
`test_clipper_caption_faces.py` (local crop/time mapping, conservative refusals, real burned pixels and manual/suppression controls) ·
`test_clipper_face_detector.py` (read states, invalid addresses, actual decoded-pixel clock oracle and one-frame mutation checks) ·
`test_clipper_face_detector_threads.py` (FD1: a first use racing a load, no instance used by two threads, warm threads frame-by-frame against one thread, the single-thread answer pinned to the shared detector's) ·
`test_face_yunet.py` (B1: adapter state failures, box clamping/invalidity, model identity, maximum-cardinality matching with greedy counterexample) ·
`test_benchmark_detectors.py` (B1: benchmark exit codes, annotation/GT validation, cascade availability, uncertain-frame handling, refused-frame accounting, F1 edge case, model identity in rows) ·
`test_benchmark_report_contract.py` (B1 report contract: inferred/scored/paired_scored counts, known GT subtotal and unknown frames, gt_count field, uncertain-frame errors/unavailability in aggregate, field type validation) ·
`test_face_yunet_b2.py` (explicit profiles, actual settings/dimensions, inverse mapping, preprocessing failure) ·
`test_face_presence_detector.py` (optional detector, no Haar dependency, exception/address preservation, default compatibility) ·
`test_benchmark_detectors_b2.py` (profile selection and actual instance settings in benchmark output) ·
`test_clipper_face_observation_flow.py` (real window extraction through render planning to caption counters; decoded-window clock, legacy uncertainty, EOF and terminal sample exclusion) ·
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
`test_clipper_publish_preflight.py` (R7: the verdict, and the word §22 had no room for) ·
`test_clipper_publish_checks.py` (R7: the seven checks, and what each refuses to claim) ·
`test_clipper_publish_corpus.py` (R7: assembling the inputs, and naming the missing ones) ·
`test_clipper_bounded_correction.py` (R7: the one correction, and its four conditions) ·
`test_clipper_publish_audit.py` (R7: what reaches the exit code, and where the OCR cache may not live) ·
`test_clipper_quote_resolver.py` (S7: where a quote is, and what the resolver refuses to guess) ·
`test_clipper_output_identity.py` (R7: what the render produced, and the only route to a provenance pass) ·
`test_clipper_caption_policy.py` (who decides whether a layer is burned, and why not the detector) ·
`test_clipper_source_treatment.py` (SC1: resolve, the F4/F5 split against its approved record, the gate under both layers) ·
`test_clipper_source_treatment_auth.py` (SC1r: the approval binds each side's treatment; a config cannot supply its own approval; the gate refuses an incomplete structure before the executor) ·
`test_clipper_source_treatment_manifest.py` (SC1: the mask from its bytes; manifest hash vs semantic identity) ·
`test_clipper_source_treatment_render.py` (SC2: §4 regressions 1–5, 7–9 through `build_dynamic_cmd` + real ffmpeg on lossless synthetic CFR sources) · `test_clipper_source_treatment_witness.py` (SC2: regression 6, the answered split with both lossless witnesses; 10, editor still == export) · `test_clipper_source_treatment_record.py` (SC2: the record corroborates argv vs manifest; retry and superseded attempts; the publish guard) · `test_clipper_source_treatment_gate.py` (SC2: the gate at execution on export and editor still, both layers; the versioned destination) · `test_clipper_fingerprint_v3.py` (SC2: canonical none, unreadable ≠ none both ways, schema difference first, stable digest, no v2 rescue, the audit) · `test_clipper_source_treatment_editor.py` (SCB2r: the editor still is corroborated like the export — replaced patch, another attempt's manifest, a changed seek/overlay, a patch changed mid-render: refused before the command runs, no image) · `test_clipper_source_treatment_preview.py` (SCB2 video gate, through the real `handle_preview`: burn and suppress treated and confirmed on pixels, a mask/glyph/patch changed after the decision refused before the encode, static refused even past the gate) ·
`test_clipper_source_caption_survival.py` (with our layer off, does the crop keep the source's own subtitle) ·
`test_clipper_source_caption_observation.py` (where the source's subtitle lines are, at a time) ·
`test_clipper_render_record.py` (what the ffmpeg call actually carried) ·
`test_clipper_caption_region.py` (the region that holds the subject and the observed subtitle) ·
`test_clipper_caption_labels.py` (which observed text is dialogue, and the four inventory rows) ·
`test_clipper_layout_policy.py` (does the source have a second region, and the off-subject seconds) ·
`test_clipper_fingerprint_v2.py` (the two fingerprint schemas and the five migration probes) ·
`test_clipper_reasoning_cache.py` (S7a: strict envelopes, targeted invalidation and JSON round trips) ·
`test_clipper_reasoning_chunks.py` (S7b: per-chunk recovery, provenance and malformed-state refusal) ·
`test_clipper_judge_cache.py` (S7c: exact-question reuse, retry, provenance and worker checkpoint wiring) ·
`test_clipper_anchor_identity.py` (S7d: source-scoped identity, detector wiring, variant propagation and anchor census) ·
`test_clipper_dedupe_scale.py` (S7e: heuristic group topology, selection-scale leaders, legacy fallback and malformed-score refusal) ·
`test_clipper_run_identity.py` (S7f: one identity across both traces, every fresh clip and the render sidecar) ·
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
`test_downloader_cookies.py` ·
`test_clipper_caption_display.py` · `test_clipper_caption_display_r.py` · `test_clipper_caption_display_api.py` · `test_clipper_caption_display_file.py` (BURST: the card's caption report, its blocks, clocks and level; missing evidence never verified; R2/R3) · `test_caption_overlays_tiling.py` · `test_clipper_caption_no_time_exit.py` (BURST1r2) · `test_clipper_preview_attempt_r.py` (R1b: the immutable per-attempt preview selected with its record in one transaction; both finishing orders, same-worker retry, rollback before and at commit, cleanup) · `test_clipper_preview_claim_r1c.py` (R1c: through `_process_next`, a takeover on another worker and on the same worker selects nothing; only the claimed attempt ends its row)
