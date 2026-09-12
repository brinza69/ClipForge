# Face detector B2 — explicit small-face profile and shared sampling

Do not start until Codex accepts B1. User authorizes continued Claude Code
implementation, Codex independently verifies. Stop at actual Claude quota
exhaustion, persist progress, and wait for the user's resume. No other agents.
Read CLAUDE.md and this PRP; preserve unrelated changes; code files <=500 lines.
No commit without Codex acceptance, no DB writes/init_db or corpus export writes.

## Evidence and bounded scope

`docs/refs/clipper-face-detector-b-2026-09-12.md` records native .9 performance
and the candidate selected on development frames, then confirmed on 20 fresh
frames. The latter has better face localization at IoU .3, but smaller returned
boxes fail the stricter comparison against many coarse head-extent annotations.
Do not change labels or claim full-head containment. No tracking/identity claim.

B2 adds a reproducible selectable candidate and lets probes use the existing
decoded-frame sampling path. It does not switch a live worker's default yet.
This is needed to render comparable candidates before authorizing live use.

## Implementation

1. `face_yunet.YuNetDetector` gains two explicit profiles: `native_v1` (existing
   defaults, native BGR, score .9) and `small_faces_v1` (exact 2x linear resize
   in both dimensions, score .75). Same pinned 2023mar model, NMS .3, topK5000.
   Unknown profile is refused. Freeze instance settings at construction and
   report the actual profile, thresholds, scale, and dimensions, not mutable
   globals read later. No automatic profile/threshold choice based on detections.
   Model sees enlarged BGR; map endpoints back to the caller's input pixel
   coordinates before final clamping/rounding. Preserve raw model boxes or
   mapped float boxes in diagnostic evidence. Linear interpolation is not new
   source detail. Never label a framing margin as an observed face.
2. `face_detector.face_presence` accepts optional keyword-only detector instance
   (duck-typed .detect(BGR)); default keeps Haar and identical output behavior.
   Share the accepted seek/decode/index/PTS implementation; do not copy it into
   a second sampler. YuNet must not depend on Haar cascades or silently fall
   back to Haar. Preserve detector metadata alongside decoded address metadata.
   An unavailable inference still carries the decoded address when the frame
   was read. Decoding failure remains distinct from detector failure.
3. `dynamic_window.analyse_window` accepts the optional detector, passes it to
   face_presence only when provided, and preserves returned metadata. Existing
   callers remain unchanged; no worker or project policy switch in B2.
4. Benchmark CLI gets --profile with explicit choices/default native_v1. No
   change to frozen label/image files. Codex will run comparisons against its
   independently saved native API outputs at both profiles.
5. Tests: profiles/actual-instance provenance; 2x input and inverse mapping on
   non-square and odd-size images, clamping, empty/error; no Haar requirement
   or fallback when supplied; same accepted decoded-pixel address convention;
   existing default Haar regressions intact; unavailable metadata stays unknown
   to downstream caption consumers. Use server/.venv/Scripts/python.exe.
6. Update map entries and save concise B2 result separately from B1. Do not
   reread broad history, tune thresholds, download models, or run on Codex's
   held-out corpus unless Codex explicitly requests the benchmark run.

## Subsequent decision, outside B2

Codex will render a separate Speed candidate through the common render path,
compare framing/caption placement and controls, and judge whether live wiring
is justified. The smaller detected box can change zoom, not just detect more
faces. Existing whole-source Haar anchors and local YuNet observations can
disagree; do not hide that disagreement behind a new model's confidence score.
