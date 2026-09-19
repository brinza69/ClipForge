# Reaction captions in the existing blur gap — 19 September 2026

Continue the authorized work with Claude Code. The ordinary reaction export is
implemented at 341174e. Its automatic caption avoids the reaction band but
overlaps writing in the watched foreground (real diagnostic at source 2700.5s).
Fix this placement without changing the selected regions or caption policy.

## Scope and ownership

Claude owns a small shared service `server/services/clipper/reaction_captions.py`,
its use in `server/routers/clipper_reaction.py` and
`server/workers/clipper_render_plan.py`, and focused backend tests. Codex owns
independent real-media verification and documentation. No frontend, migrations,
detectors, project-setting changes, other agents or commits by Claude. Keep each
file <=500 lines. Unset CLIPFORGE_DATA_DIR for tests; use server/.venv Python.

## Placement contract

- Apply only to explicit game_top_face_bottom plans with game_content_fit=True.
- Reserve the fitted foreground OUTPUT rectangle using the renderer's existing
  layout_geom._bands and _game_fit_box. Add it to a temporary copy of keep-outs,
  then call captions.resolve_position. Do not change stored/canonical safe_zones:
  existing reaction bindings validate them, and old selections must remain valid.
- Verify the returned position against the shared caption envelope and temporary
  keep-outs. The generic resolver's least-overlap fallback is NOT acceptable for
  this mode: when no clear position exists, raise an actionable error asking for
  manual caption placement or a different framing. No silent suppression, crop
  changes, or fallback onto the foreground. Both PUT and ordinary preview/export
  must share the helper. A failed PUT changes nothing in the DB.
- Position remains fixed over the clip. Resolve again for export so regenerating
  captions or changing a preset cannot leave the earlier placement in use.
- Suppressed captions, manual y_pct_manual captions, missing plans and empty
  caption text bypass this helper. Do not judge a suppressed/empty layer. Use
  caption_plan_to_overlays and existing remap_overlays for event presence when
  drop spans are available, rather than a new interpretation of chunk times.
- This is the existing approximate two-line envelope, NOT measured glyph bounds
  or an OCR/readability gate. Limit auto-placement to its supported envelope:
  effective font <=72px, scale <=1, no entry_pop, outline <=5, shadow <=3,
  at most two explicitly broken lines of <=22 characters each. Refuse unsupported
  edited text/style with an actionable manual-placement message; don't truncate
  text or change its style. Resolve actual merged preset+inline style. These
  limits are conservative operational scope, not a proof for arbitrary fonts.
- Caption policy is resolved before this decision. Manual captions remain exactly
  user-owned. Existing dynamic and legacy static layouts retain their behavior.
- Keep the current ASS writer/remapping and sidecar v2 pipeline unchanged: the
  existing resolved caption_y already records the position actually burned.

## Evidence and tests

For content {x:348,y:128,w:1314,h:798}, reaction {x:8,y:476,w:326,h:232},
source 1920x1080 and face_pct=.40, delivered foreground is y=248..904 and
reaction starts at 1152. The lower blur gap fits the shared 192px envelope.
Test this case, a portrait/no-gap case, one/two-line captions, all bypasses,
unsupported edited style, no mutation of the stored plan/safe_zones, PUT refusal
without mutation, and identical placement at save versus _decide_render.
Exercise suppression in the worker, not only the helper. Adjust prior resolver
failure tests with empty chunks to contain actual text if testing resolution.
Do not weaken exceptions/claims to pass tests. No try/except-pass assertions.

Codex will render ordinary preview and MP4 with actual captions on the local
source in a private DB/directory, compare actual caption pixels against both
reserved regions, verify sidecar and decode, and check existing exports unchanged.
Then full backend suite excluding the two documented TikTok 404 tests.

Report focused tests and limitations to
data/claude-xqc-reference/RESULT-REACTION-CAPTION-GAP.md. Do not claim arbitrary
caption readability, OCR accuracy, or an improved editorial selection.

## Delivery record — 20 September 2026

Claude implemented the helper and both consumers. Independent regressions required
moving the policy decision before placement, resolving the actual preset style,
checking surviving events only, and filtering original-clock events before silence
remapping. The save API also supplies the original clip duration. The real ordinary
export rerun passes all 28 artifact checks; results and scope are recorded in
`docs/refs/clipper-reaction-caption-gap-2026-09-19.md`.
