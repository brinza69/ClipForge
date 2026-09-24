# Reaction editor: say which content height leaves room for captions

Status: small follow-up from real use on 2026-09-24 (`data/claude-reaction-editor/RESULT.md`).
Coordinator: Claude Code. Baseline HEAD `43aaff0`. Preserve unrelated dirty files and all exports.

## Problem, observed
Saving a reaction framing is refused (422 `caption_placement_failed`) when the fitted content leaves
no blur gap for the automatic caption. The refusal is correct, but it only says "choose a different
framing". On 70ca, the working height had to be found by hand, by calling the resolver offline:
a 1160×804 content box (1.443:1) was refused and 1158×782 (1.481:1) was accepted. The threshold
depends on the caption search grid, so no aspect ratio can be quoted from memory.

## Contract
1. `reaction_captions.resolve_reaction_caption_y` raises `NoCaptionGap`, a subclass of `ValueError`,
   for the no-clear-slot case only. Style and text envelope refusals stay plain `ValueError`. Every
   existing `except ValueError` keeps working.
2. New `caption_ready_height(content_rect, face_rect, src_w, src_h, caption_plan, *, face_pct,
   clip_duration)` returns the largest even content height, at the same x, y and width, for which the
   SAME builder (`plan_reaction_layout`) and the SAME resolver find a slot, or None. Binary search:
   shrinking the content only moves its bottom edge up and widens the gap under it, so the answer is
   monotonic. No formula and no hard-coded aspect ratio.
3. PUT `/clips/{id}/reaction-layout`: on `NoCaptionGap`, the 422 message gains one sentence with that
   height, and the detail gains `max_content_height` (int or null). Nothing is written, as before.
   The frontend already shows the message, so it needs no change.
4. No change to the placement decision itself, the geometry, stored plans, the export path, or captions.

## Tests (`server/tests/test_clipper_reaction_caption_hint.py`)
| Case | Expected |
|---|---|
| near-square content at 1920×1080, real bold_impact plan with text | `NoCaptionGap`, and `isinstance(e, ValueError)` |
| `caption_ready_height` result h | resolver passes at h and raises `NoCaptionGap` at h+2 (exact boundary) |
| style outside the envelope (font 90 px) | plain `ValueError`, not `NoCaptionGap` |
| HTTP PUT with the refused box | 422, `max_content_height` present, message contains the number, no DB change |
| HTTP PUT again with h = the hint | 200 |
| manual caption (`y_pct_manual`) | PUT unaffected (bypass unchanged) |

Files: `services/clipper/reaction_captions.py`, `routers/clipper_reaction.py`, the new test. Each
stays ≤500 lines. Focused tests + one full suite. Commit only these files and this PRP.
