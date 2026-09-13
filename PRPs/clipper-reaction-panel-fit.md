# Reaction panel fit — 13 September 2026

User asks Codex to continue using Claude Code, with Codex independently testing.
Read CLAUDE.md first. This batch implements an explicit, experimental static
reaction plan and renders a real probe. It does not infer the editing style of
the inaccessible Short T1jsllUvrHA, change automatic defaults, or label people.

## Measured problem

Prior three-way probe is documented in PRPs/clipper-local-reaction-probe.md.
Actual outputs: data/claude-xqc-reference/probe_20260913_105204_483935/.
B clips the word/number at output 8.5s (source 2700.5s). C keeps observed text
but expands vertically into browser chrome/title/taskbar. A tiny full-source
baseline also retains that UI. This is a framing trial, not an editorial cut.

## Implementation contract (Claude owns code/tests only)

1. Add a small explicit builder in server/services/clipper/reaction_layout.py,
   e.g. plan_reaction_layout(content_rect, face_rect, src_w, src_h,
   face_pct=.40, ...). Rectangles are manually observed SOURCE pixels. Require
   valid integer/even, positive, bounded rectangles; do not silently grow,
   trim or infer them. No detector/provenance claims. Emit an ordinary static
   game_top_face_bottom plan with game_content_fit=True and an explicit note
   that regions are caller supplied. Preserve both source rectangles exactly.
   The face rectangle already has nearly the destination band's aspect; keep
   existing face-lane behavior. Do not promise source content outside these
   declared rectangles is preserved.
2. Extend layout_geom.py ONLY for the explicit game_content_fit=True mode on
   stacked layouts. Fit the whole game_rect proportionally inside its band;
   center it over a blurred, aspect-preserving fill of THAT SAME rectangle.
   No browser outside game_rect may enter either foreground or background.
   Never stretch the foreground to the band. Compute explicit even foreground
   dimensions and aligned offsets once; use the same geometry for ffmpeg and
   caption keep-out mapping. Correction after reading even(): it first rounds
   to an integer and then rounds odd values down, so the deviation can be up to
   1.5 output pixels. Document it rather than claiming exact aspect equality.
   Default/absent/False behavior must remain unchanged. Invalid flag types or
   unsupported layout combinations must not silently ignore an enabled mode.
3. _safe_zones must map chat/HUD through the fitted FOREGROUND box, not the whole
   game band. Face band keep-out remains conservative and unchanged. Builder
   must compute correct safe_zones with the new mode. No changes to unrelated
   fullscreen mapping or dynamic renderer.
4. The common workers.clipper_render_output.render_export path must accept the
   emitted plan without a new sidecar writer or separate encode path. Existing
   v2 fingerprint covers layout_plan including the flag. Update static render
   version if appropriate, preserving old record semantics. Do not touch
   layout.py (already 504 lines) or refactor other layout branches.
5. Add focused tests in server/tests/test_clipper_reaction_layout.py: bounds
   refusals; true versus false/absent; aspect and explicit foreground geometry;
   numerical HUD mapping (independently calculated expected coordinates);
   normal render command/sidecar pass-through and fingerprint sensitivity.
   Include at least one actual tiny ffmpeg render test with colored edge
   markers and a square/circle, decode pixels to demonstrate full foreground
   preservation and proportions. Do not just assert on strings/self-output.
   Reuse existing common-path test patterns; no real DB/init_db calls.

## Scope and ownership

Claude may edit layout_geom.py, render.py (version only), create reaction_layout.py
and test_clipper_reaction_layout.py. At most 500 lines per file. No commits,
map/handovers or other edits: Codex owns documentation and independent probes.
Keep measured comments intact. No added UI settings or automatic application to
a whole VOD; actual local probe is the first consumer. No schema migration.

Tests: server/.venv/Scripts/python.exe -m pytest tests/test_clipper_reaction_layout.py
from server, with CLIPFORGE_DATA_DIR UNSET. Never run init_db on production data.
Claude can prepare code/tests and report exact tests it ran. Codex will run
focused + full suite (excluding ONLY the two pre-existing TikTok 404 tests)
and inspect actual media. Do not claim visual verification from a filtergraph.

## Codex's real probe after review

Same local source SHA256 598a9b9fb774049979a4c2de1d938b1a351f9172cc95a50b5ca072c5515c012e,
[2692,2704), 1080x1920/60fps; content {x:348,y:128,w:1314,h:798},
face {x:8,y:476,w:326,h:232}, face_pct=.40. The content rectangle excludes
the source's leftmost 108px of the watched video's visible width to avoid its
embedded camera; it is NOT the entire watched video. This limitation must be
visible in the report. Inspect the 24 sampled source frames and fresh output
times for losses, source cuts, text and browser UI. Blur is a proposal, not a
fact observed in the reference Short.

Use render_export, unique directory under data/claude-xqc-reference; policies
None, no ASS/watermark/drops, no DB writes. Preserve all 390 existing export
files by actual before/after SHA256. Full decode, v2 fingerprint recomputation,
output_identity.matches, actual caption_filter false, duration/audio checks.
Keep previous probes unchanged. Report size/readability cost, not just success.
