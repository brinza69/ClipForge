# Local reaction framing probe — 13 September 2026

User-selected source: data/clipper/2c8af11153a3/source/source.mp4.
Use Claude Code for preparation; Codex reviews and executes. No application
code, DB, policy or existing export changes. This is a 12-second framing
comparison, not a publishable edit or reconstruction of the unavailable Short.

## Evidence and scope

20 sparse source frames and 24 frames at 0.5s over [2692,2704) were decoded from
the original file. Source SHA256:
598a9b9fb774049979a4c2de1d938b1a351f9172cc95a50b5ca072c5515c012e.
Source metadata: 1920x1080, 60fps, AV1/AAC, 13402.906122s.
Evidence is in data/claude-xqc-reference/local-source-20260913/ and
probe-source-2692-2704/. The narrow proposal cuts the name/number at 2700.5s;
retain it only as a rejected comparison. It is not a successful framing.

## Three alternatives through the EXISTING common render_export path

A SOURCE_FIT: full 1920x1080 source, existing fullscreen_crop padded behavior.
B NARROW_REJECTED: game_top_face_bottom, face_pct .40; game x578,y128,w750,h800;
face x8,y476,w326,h232. Labels must record observed clipping at 2700.5s.
C WIDE_CONTEXT: game_top_face_bottom, face_pct .55; game x348,y14,w1314,h1052;
face x48,y476,w238,h232. This includes browser UI to retain the relevant source
content at its existing proportions. It excludes the leftmost source strip and
crops the inset horizontally. It does NOT preserve the whole source or prove
an ideal treatment. Browser clutter and enlargement are costs to inspect.

Same [2692,2704) window, 1080x1920/60fps, no dropped spans, watermark or added
captions. No global detector changes, no inferred identities. In-memory clip
and project only. Policies, scores and other unmeasured fields stay None.

## Correct preparation script before any render

Edit only data/claude-xqc-reference/render_local_reaction_probe.py and its
READY-PROBE.md. Aim for at most 280 lines. Relevant APIs:
server/workers/clipper_render_output.py,
server/services/clipper/{output_identity,render_input,ffmpeg_tools,layout_geom}.py.

The first prepared script is NOT accepted:
- _dir_fingerprint hashed names/sizes, not file contents; same-size corruption
  was invisible. Record actual SHA256 per file and count for selected project's
  exports before/after; missing/empty inventory must be explicit, not a pass.
  The selected project's exports directory is empty. Codex expanded the guard
  to the actual 390 files under data/clipper/*/exports/* instead.
- It records a fingerprint without recomputing it; recompute using the DECLARED
  v2 schema and compare. Call output_identity.matches against the MP4, not just
  read output_identity back. Correction to the initial Codex review: identity
  DOES carry has_audio in the current implementation. Its absence was wrongly
  asserted; the independent decode/metadata check is still required.
- Complete decode errors do not affect ok. Use ffmpeg -v error -xerror -i ...
  -f null -, require success, and verify video/audio metadata and 12s duration.
  Failed/missing evidence must reach a nonzero process result and report.
- It invents no_second_camera for a source that visibly has a webcam + content.
  Set layout_policy, edit_profile, and caption_policy None. Plan metadata says
  no ASS is a probe parameter; verify actual caption_filter false from the
  returned render_record. Do not call edit_profiles.resolve/DEFAULT_MODE just
  to fill fields that were never measured.
- SOURCE_FIT geometry says 608 where int(607.5) gives 607, then hardcodes zero
  distortion. Report theoretical uniform scale only there, with decoded inner
  content size unmeasured. Use actual layout_geom._bands for B/C band dimensions
  and report raw X/Y scale and their deviation without rounding before division.
- Use Path.is_relative_to on resolved output paths, not string startswith.

Actual source digest must match before render. Unique output directory under
data/claude-xqc-reference; refuse overwrite. Parent root is parents[2]. Set data
directory only in child process; no init_db or DB calls. Common render_export
executes and writes sidecars v2; do not implement a second sidecar writer.

Prepare only, do not execute or claim tests passed. Codex will execute, inspect
actual output frames and verify export inventory, identity, schema and decoding.
