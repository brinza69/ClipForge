# Automatic face framing: extent and within-shot movement

Read CLAUDE.md. User requests implementation with Claude Code; Codex verifies
independently. No subagents, commits, DB writes/init_db, corpus export writes,
model downloads or default detector activation. Preserve other changes.

## Measured failure and scope

Speed `d789060e273d`, B2 paired exports under
`data/claude-face-detection/batch-b/speed-probe-20260912-195851-910623/`:
Haar boxes are square (median 92 source pixels), YuNet width 64 and height 84.
`_face_samples` discards height; all three face cameras use width as zoom scale.
YuNet's 8s output clips hair and has captions over the face. More detections
are not sufficient to activate it. Source-wide stable anchor is None here.

This batch makes the editorial framing envelope explicit and retains movement
within a shot. It does NOT establish full-head/hand containment, identity,
active speaker, continuous temporal coverage, or source-caption preservation.
Keep YuNet optional and Haar default. Do not tune multipliers on this example.

## Contract

1. Preserve raw observations and existing selection. `_face_samples`, `_dominant`,
   `face['w']`, anchor and game-camera geometry must retain their meanings.
   In a small new module, recover the original largest box for each sample
   SELECTED by `_dominant` (same requested time, source centre and width).
   Never elect a different face using a new size metric. Scale x/w and y/h
   separately from proxy to source. Match only actual samples; empty or
   inconsistent input cannot manufacture an envelope.
2. Editorial scale is a separately named `framing_span`: median of
   max(source box width, source box height) over these elected observations,
   floored by the existing face width. Square observations thus retain legacy
   scale. This is a sizing heuristic, not an observed head extent; use the
   existing CAMERAS multipliers/headroom unchanged. camera_rects uses this
   span only for face-family sizes, never for game bands or subject identity.
   No evidence -> legacy geometry with an explicit unavailable basis.
3. For UNANCHORED face shots, retain the current face_framing result and widen
   only if needed to also contain the local framing proposals for elected
   observations in [t0,t1) whose observed CENTRE is inside that existing
   camera's delivered footprint. This bounded scope was added after the real
   Minecraft control found elected false positives on game textures: at 7s
   [299,41,80,80] in proxy vs real inset face [397,31,27,27]. The false box
   passes the old dominant-cluster gate but lies outside the current camera.
   It must not cause a new full-frame fit. This is conditional protection of
   observations the camera already contains, NOT identity/tracking of a face
   that leaves the camera. Keep excluded counts/scope explicit; exclusion
   does not mean the observation was proved false. Each proposal uses the camera's multiplier and
   headroom, centred at that observation, with span=max(global framing_span,
   observation width, observation height). Preserve the original rectangle;
   do not shrink or discard its coverage. A stable rectangle for the whole
   shot must contain their union. Use even dimensions and geometric rounding
   slack, or existing full fit if a portrait rectangle cannot contain it.
   Check the DELIVERED renderer window, especially edges/2px anchor clamp,
   not only the planned rect. Anchored shots keep the prior local-stray
   protection and are excluded from this new movement widening.
4. If widened/fit, freeze push/snap/shake as the existing guard does. Keep any
   previous framing_adjustment reason (append/report the new cause). Missing
   local observations do not assert safety or absence; retain the current
   fallback. No timing, game selection, caption settings or reaction overrides
   are changed. Existing equivalent-shot merge still runs once after changes.
5. Store a concise framing policy/basis (elected proposal count, scale) in
   the returned subject summary, and adjustment reason on changed shots.
   Observed face width must not become the editorial span. Avoid copying
   whole tracks into the sidecar. Existing fingerprint already covers plan.

## Files / checks

- New helper `server/services/clipper/dynamic_face_envelope.py` (name flexible),
  minimal wiring in `dynamic_cameras.py` and `dynamic_edit.py` (currently
  468/482 lines, <=500 each). Preserve load-bearing comments.
- Existing `dynamic_face_framing.py` may share its rectangle-union arithmetic
  only if needed; no unrelated refactor.
- New tests separate from existing face_framing tests: tall/narrow box uses
  height without changing observed width or game windows; square boxes retain
  scale; no changed election with a competing face; anisotropic proxy scaling;
  local half-open interval/terminal sample; missing evidence; fixed anchor;
  delivered windows contain proposals at image edges; impossible union fit;
  effects cannot undo widening. A moving coloured target encoded and decoded
  must survive near both ends of a shot (not just its median).
- Update docs/clipper-map.md for helper/tests. Leave handover/final report to
  Codex after real probes. Save result in data/claude-auto-framing/RESULT.md.
- Run focused tests with server/.venv/Scripts/python.exe from server directory,
  CLIPFORGE_DATA_DIR UNSET. Codex runs full suite and actual MP4 comparisons.

## Acceptance outside implementation

Codex compares original baseline, new Haar, and optional YuNet on the full
17.9s Speed export, plus fresh Speed and Minecraft/another-source controls.
Inspect original and decoded outputs at preselected times and shot boundaries;
report scale/timing costs and observed failures. Existing exports remain
byte-identical. No claim of automatic-framing completion from unit tests.
