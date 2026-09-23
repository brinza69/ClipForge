# Short face-detector gaps: pixel-backed continuity

Read CLAUDE.md. User asks implementation with Claude Code; Codex verifies.
Baseline 351fabe. Preserve unrelated changes, no production DB/corpus writes,
model downloads, global YuNet activation or automatic identity claims.

## Measured starting point

Speed d789060e273d at output 16.6s still clips the person under Haar.
Source interval 386.47..404.37. Cached 4Hz observations in
data/claude-auto-framing/paired/d789060e273d/haar/observations.json:
last raw detection at 400.97 (14.50 relative), one box [44,17,25,25];
401.22..404.22 are empty. YuNet observes the moving face there. No detection
is not absence, and copying the last coordinate is not tracking motion.
Multiple/larger content faces earlier in the clip also expose identity risk.

## Work and acceptance

First prototype on the decoded pixels of the SAME re-encoded analysis window.
Use existing OpenCV only. Try short optical-flow continuation of an unambiguous
single detection into clean empty states. Raw boxes/state/addresses stay
unchanged. A separate proposal must retain its seed (time, exact box, decoded
frame), actual decoded target address, method, age and quality diagnostics.
No inferred source PTS from window PTS. No filling unreadable/unavailable rows.

Tracking is a bounded heuristic, not face recognition. Stop on decode/address
failure, missing texture, inconsistent forward/backward flow, poor spatial
support or a cut/appearance break. Do not seed from several possible faces,
bridge a detection of another face, or continue without a finite time budget.
Initial 2s budget is an engineering bound, not a calibrated threshold. Do not
extend it merely to make this clip green. No reseeding from tracked proposals.
Verify the actual pixels on Speed before wiring a planner consumer; reject the
approach if it follows background or creates unsupported coverage.

If prototype is usable, extract a small DB-free helper and wire local analysis
to carry separate motion proposals. The planner may use only a proposal whose
seed exactly matches one of its elected real observations and fits the existing
camera scope; this permits bounded motion from that seed, not arbitrary target
switches. Do not let tracked proposals re-elect the dominant face, alter raw
presence gates, enlarge the global face scale, or become caption detections.
Apply widening with delivered-renderer geometry; freeze effects when widened.
Store scope/provenance/refusals in sidecar plan; unknown never means success.
Existing fixed-anchor protection and gameplay cameras remain authoritative.

Tests must exercise pixel motion with synthetic translated textured target,
blank/occluded/cut frames, another face, invalid addresses, timeout, unchanged
raw observations, rejected seed, source/window clock, and an actual output.
Original Speed plus second Speed, Minecraft false-wall and dialogue controls:
paired MP4s through common render_export, verify metadata/full decode and inspect
fresh frame times around gap/boundaries. Report scale/caption/timing costs and
remaining misses. Keep all production exports unchanged.

Files: face_detector.py (sampling contract), dynamic_window.py (temporary
analysis window), dynamic_face_envelope.py and dynamic_edit.py (consumer;
dynamic_edit is already 500 lines, extract a real helper if needed), tests in
new <=500-line files. Do not build general tracker architecture or a new model.
Map new files in docs/clipper-map.md; Codex writes evidence and handovers.
Interpreter server/.venv/Scripts/python.exe, unset CLIPFORGE_DATA_DIR for tests.
