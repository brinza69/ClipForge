# A rescore must not throw away a clip a person has worked on

Status: requested by the user 2026-09-24 ("dă lotul următor celeilalte sesiuni"). Coordinator:
Claude Code (account 1) — plan, review, full suite, docs, commit on approval. Implementer: Claude
Code on account 2 (`CLAUDE_CONFIG_DIR=C:\Users\vlado\.claude-cont2`). Baseline HEAD `37537b4`.
Unrelated dirty files (e.g. `server/services/clipper/dynamic_edit.py`,
`server/tests/test_clipper_dynamic_export.py`) belong to another session: never touch them.

## Problem, observed
- `workers/clipper_finalize.py::_write_clips` (the persist step of every analysis/rescore, called
  from `workers/clipper_build.py`) deletes EVERY clip of the project whose status is not `exported`
  and inserts the new candidates as fresh rows. Whatever a person did to a non-exported clip is
  gone: trims, headline edits, caption preset / manual caption height, a hand-drawn reaction framing
  (`layout_plan.reaction_binding.by == "human"`), the per-clip `source_has_burned_captions`
  declaration, approvals and rejections. Nothing reports it.
- It is made worse by `invalidate_render`: EVERY edit of an exported clip (trim, caption, layout,
  caption-source) demotes it `exported → approved`, so the edit itself makes the clip deletable.
- A rescore is easy to trigger: any change of `clipper_settings` through
  PATCH `/projects/{id}/settings` enqueues one.
- Pinned today by `test_review_a_rescore_keeps_the_declaration` in
  `server/tests/test_clipper_clip_caption_source.py` (`xfail(strict=True)`): exported clip → PUT
  caption-source → approved → `_write_clips` → the row is replaced, the declaration is NULL, and
  the duplicate caption layer comes back. Review: `data/claude-caption-source/review-opus.md` §3.

## Contract
1. **Which clips survive a rescore.** Kept = status `exported` (as today) OR the clip has at least
   one row in `clip_feedback` with origin `manual` or NULL (see below) and `event_type` in
   `HUMAN_WORK_EVENTS = (approved, rejected, start_changed, end_changed, crop_changed,
   layout_changed, caption_changed, headline_changed, score_overridden)`.
   Not counted: `generated`, `previewed`, `deleted`, `exported`, `posted`,
   `performance_recorded`, `reviewed` (not an edit of the clip, or already covered by the
   `exported` status), and any event whose origin is `auto` or `system` — a machine's action is
   not a person's work (`services/clipper/feedback.py` explains why only MANUAL is a verdict).
   **Origin NULL counts as a person's.** 69 of the 485 rows in the real DB predate the `origin`
   column (61 exported, 5 previewed, 2 headline_changed, 1 caption_changed). An origin nobody can
   read must not decide that a person's edit is deletable: a clip kept by mistake stays visible on
   the board, an edit deleted by mistake is gone with no trace. Test both directions.
   Rejected clips are kept on purpose: a rescore that re-proposes a moment a person rejected makes
   the rejection disappear. Record this choice in the code comment; it is a one-line change to
   reverse if the user decides otherwise.
2. **One predicate, two places.** The pre-dedupe filter in `clipper_build.py` (`_exported_spans`
   → `drop_moments_already_exported`, ~line 346) and `_write_clips`'s keep set MUST use the same
   function. The comment at clipper_build.py:340 records why: filtering at write time only made
   dedupe elect leaders that were then dropped — 8 winners became 3. Prefer keeping the existing
   function names and updating their docstrings over renaming; if you rename, update every
   reference, including tests.
3. **A kept clip is kept whole** — same id, every column untouched (including status,
   `selection_run_id`, `rank_position`, `layout_plan`, `caption_plan`, `source_has_burned_captions`),
   and its moment is not re-proposed (the existing overlap rule, `settings.clipper_overlap_threshold`).
4. **Find and state every reader that assumed "kept rows are exported".** Before editing, grep for
   readers of `selection_run_id`, `rank_position`, `is_alternative`, `shadow_rank`, the board
   count / winners, auto-export and the blind review (start with `tests/test_clipper_run_identity.py`,
   `tests/test_clipper_auto_export.py`, `tests/test_clipper_selection.py`,
   `tests/test_clipper_analysis.py`, `services/clipper/selection.py`, `workers/clipper_build.py`).
   For each: does a kept `candidate`/`approved`/`rejected` row from an OLDER run break it? Fix what
   breaks inside this batch only if the fix is small and obviously right; otherwise stop and report.
5. The query must be one statement per project (a subquery or join on `clip_feedback`), not a loop
   per clip. Match on the clip ids of THIS project, not on `clip_feedback.project_id`: 0 of 485
   rows have it NULL today, but the column and `feedback.record()` allow it.

## Measured on the real DB (read-only, 2026-09-24)
6,639 candidate and 107 exported clips; no `approved`/`rejected` rows. Exactly ONE non-exported clip
carries a person's work today: `1a8a31dc9215` (slice4h00test), from the caption-source UI check
(3 × manual `caption_changed`, net state unchanged). So on today's boards the rule changes nothing
except that clip; it exists to stop the loss from happening next time.

## Out of scope
Carrying edits onto fresh rows; the settings PATCH that triggers the rescore; any UI change;
deleting or rewriting feedback rows; touching exports or data/.

## Tests — a new file `server/tests/test_clipper_rescore_keeps_edits.py`
| Case | Expected |
|---|---|
| candidate with a manual `layout_changed` event (and a hand-made layout_plan, caption_plan, trimmed start/end, headline) | survives `_write_clips` with the same id and every one of those fields equal |
| candidate with a manual `rejected` event / `approved` event | kept |
| candidate whose only events are `generated`/`previewed`/`reviewed` (manual) or any event with origin `auto`/`system` | replaced, exactly as today |
| candidate with a `headline_changed` event whose origin is NULL (legacy row) | kept |
| candidate with a `previewed` event whose origin is NULL | replaced |
| untouched candidate | replaced, exactly as today |
| exported clip | kept, as today |
| a fresh candidate overlapping a kept touched clip | not inserted; one that does not overlap is inserted |
| the pre-dedupe spans (the function clipper_build.py calls) | include the touched clip's span — same predicate as `_write_clips` |
| feedback row with `project_id` NULL for a clip of this project | still counts |
| two projects | a touched clip in project B does not keep anything in project A |
And in `test_clipper_clip_caption_source.py`: remove the `xfail` marker from
`test_review_a_rescore_keeps_the_declaration` — it must now pass as a plain test (do not change its
body). Run the relevant existing suites (`test_clipper_run_identity.py`, `test_clipper_auto_export.py`,
`test_clipper_selection.py`, `test_clipper_analysis.py`, `test_clipper_clip_caption_source.py`).

## Finish
Coordinator: review, ONE full suite (CLIPFORGE_DATA_DIR unset, the 2 TikTok deselects), map and
handover entries, commit only with the user's approval, exact paths.

## Closure (coordinator, 2026-09-24)
- **Implementer:** Claude Code on account 2 (Opus 5.5), 32 turns — report
  `data/claude-rescore-edits/worker.md`. `workers/clipper_finalize.py`: `HUMAN_WORK_EVENTS`,
  `_kept_clips` (the one predicate), used by `_exported_spans` (pre-dedupe) and `_write_clips`;
  `clipper_build.py` unchanged (its pre-dedupe call already goes through `_exported_spans`).
  New `tests/test_clipper_rescore_keeps_edits.py`; the strict xfail in
  `test_clipper_clip_caption_source.py` removed — the test passes unchanged.
- **Contract point 4 — the one reader that needed a decision:** `_auto_export` ranks
  `candidate` rows by score, and a kept hand-edited candidate carries its OLD run's score, so it
  could take an unattended render slot from this run's picks. Coordinator's call: `_auto_export`
  skips clips a person worked on (`_touched_clip_ids`, shared with `_kept_clips`). That is exactly
  the behaviour before this batch — then no such clip could be on the board at that point — and it
  needs no signature change. Test `test_a_kept_hand_edited_clip_takes_no_unattended_render_slot`,
  mutation-checked: it fails with the filter removed.
- Unchanged in kind, recorded: the blind review answers 409 `board_contains_different_selection_runs`
  after a rescore of a board holding a kept ranked clip, exactly as with a kept export; the board
  shows kept clips with their old rank badge, as with exports.
- **Suite: 2502 passed, 2 deselected, exit 0** (`data/claude-rescore-edits/full-suite.txt`).
