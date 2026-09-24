# Double captions: let a person decide per CLIP that the source already carries subtitles

Status: requested by the user ("abordează subtitrarea dublă"), 2026-09-24. Coordinator: Claude Code
(account 1) — plan, frontend, review, suite, docs. Worker: Claude Code on account 2
(`CLAUDE_CONFIG_DIR=C:\Users\vlado\.claude-cont2`) — backend + tests. Baseline HEAD `43aaff0`.
Preserve unrelated dirty files (incl. the uncommitted caption-hint batch) and all exports.

## Problem, observed
- `caption_policy.decide` burns ClipForge's own caption layer unless the PROJECT setting
  `source_has_burned_captions` is True. That setting has no UI control at all.
- On reaction / co-stream material the watched video carries burned subtitles in SOME clips only
  (9d2a5deeda58, project pilot2c8a: Moist watching a YouTube video that has its own burned
  subtitles). A per-project answer is wrong for the other clips either way. 9d2a was exported
  with no caption plan, deliberately; building one — the normal path — would have burned a second
  layer over the source's, and nothing in the UI could have stopped it.
- The detector (`source_captions`, `calibrated: false`) must not decide — see `caption_policy.py`:
  a wrongly suppressed layer is an invisible, expensive failure. This batch keeps that rule.
- Found while planning: PATCH `/projects/{id}/settings` re-scores the whole project whenever
  `clipper_settings` changes, so a project-level toggle would launch a rescore. Out of scope here.

## Contract
1. New nullable clip column `source_has_burned_captions` (BOOLEAN): `models.py` ClipModel AND the
   `_clip_migrations` list in `database.py` (CLAUDE.md rule 3) AND `_REQUIRED_MIGRATED_COLUMNS
   ["clips"]` (so a failed migration is a startup failure, like every other clip column). Do not
   bump `SCHEMA_VERSION` unless a test demands it. True = a person declared that this
   clip's source already shows burned subtitles → suppress our layer; False = declared none → burn;
   NULL = follow the project. A dedicated column, not a key inside `caption_plan`, because a
   caption rebuild replaces `caption_plan` and would silently resurrect the duplicate layer.
2. `caption_policy.decide(setting=None, detector=None, clip_setting=None)`: a clip answer (True /
   False, real bools only) wins over the project's, `decided_by: human`, with new `why` values
   `a_person_declared_this_clip_already_carries_captions` / `a_person_declared_this_clip_carries_none`
   and `"scope": "clip"`. **When `clip_setting` is None the returned dict is byte-identical to
   today's** — no new key, so stored sidecars and fingerprints do not churn.
3. Pass the clip's value at both decision sites: `workers/clipper_render_plan.py::_decide_render`
   (that file is at 499 lines — keep it ≤500) and `routers/clipper_reaction.py` PUT (`_burn`), so a
   clip whose own layer is suppressed is no longer refused for lacking a caption gap.
   `publish_corpus` reads the recorded decision and needs no change.
4. New endpoint `PUT /api/clipper/clips/{clip_id}/caption-source`, body
   `{"source_has_burned_captions": true|false|null}` — a new small router
   `routers/clipper_caption_source.py`, registered in `main.py` beside the other clipper routers.
   Strict: only JSON booleans or null (422 for 1, "true", missing key); 404 unknown clip; 409
   `export_in_progress` while the clip is exporting (same code/message as the clip PATCH); on a
   real change `invalidate_render(clip)` + a `caption_changed` feedback event, `origin=
   ORIGIN_MANUAL`, payload `{"field": "source_has_burned_captions", "old": …, "new": …}` (the
   PATCH's shape); unchanged value = no write, no invalidation, no event. Errors use the
   `{"error", "message"}` detail shape the other clipper routers use. Returns
   `{"clip": clip_to_dict(clip)}`.
5. `clip_to_dict` exposes `source_has_burned_captions` as true / false / null — NOT through
   `_as_bool`, which turns None into False and would erase "nobody has said".
6. Frontend (coordinator): `ClipperClip.source_has_burned_captions`; a three-way select in the clip
   editor next to the caption preset — "Urmează proiectul" / "Arde subtitrarea ClipForge" /
   "Nu arde — sursa are deja subtitrare" — saved through the new endpoint.

## Out of scope
Detector-driven decisions; the project-level UI (needs rescore-free settings first); caption
geometry; re-rendering any export; any DB write outside the endpoint.

## Tests (worker) — `server/tests/test_clipper_clip_caption_source.py`
| Case | Expected |
|---|---|
| decide(): clip True/False × project True/False/None | clip wins, `decided_by` human, `scope` clip |
| decide(): clip None, every project value, with/without detector | dict identical to the pre-change function (compare against a frozen copy of today's outputs) |
| decide(): clip_setting 1 / "true" | treated as None (not a bool) — never a decision |
| PUT caption-source true → false → null | 200 each, value stored, render invalidated only on change |
| PUT with 1, "true", {} | 422, nothing written |
| PUT while exporting | 409, nothing written |
| `_decide_render` for a clip with True and a default project | caption policy action `suppress`, scope clip |
| reaction PUT on a suppressed clip with a near-square content box | 200 (no caption-gap refusal) |
| migration | column present after `init_db` on a scratch DB (never the production DB) |
Plus the relevant existing suites (caption policy, reaction editor, dynamic export, fingerprint).

## Finish
Coordinator: review the diff, focused + ONE full suite (CLIPFORGE_DATA_DIR unset, the 2 TikTok
deselects), UI check on a real clip with a revert, handover entries, clipper map rows for the new
files. Commit only with the user's approval, exact paths.

## Closure (coordinator, 2026-09-24)
Built as contracted, with two changes forced by the independent review:
- **Implementer:** Claude Code on account 2 (Sonnet 5), 67 turns — report
  `data/claude-caption-source/worker-backend.md`. Frontend (select, type, the frame-preview note
  "Added captions are off for this clip") by the coordinator.
- **Independent review:** Claude Code on account 2 (Opus 5.5), 50 turns — report
  `data/claude-caption-source/review-opus.md`. Findings and what was done:
  1. The coordinator had swapped the implementer's `getattr(clip, "source_has_burned_captions",
     None)` for a bare attribute read; 9 existing tests build duck-typed clips without the field
     and crashed. Reverted to `getattr` (production clips are ORM rows and always carry it).
  2. **Fixed:** turning the layer ON (clip False, or null over a burning project) over a reaction
     framing saved while the layer was off, with no caption slot, returned 200 and then failed
     every preview and export. The endpoint now runs the reaction PUT's own check: 422
     `caption_placement_failed` with `max_content_height` (this uses `caption_ready_height` from
     the caption-hint batch, so that batch must be committed first), and an accepted change stores
     the resolved `y_pct` as the reaction PUT does.
  3. **Known gap, not fixed:** a rescore (`_write_clips`) deletes every non-exported clip and
     re-inserts the moments as new rows, so the declaration is lost — as trims, captions and reaction
     framings already are. Pinned by `test_review_a_rescore_keeps_the_declaration`
     (`xfail(strict=True)`); carrying per-clip human edits across a rescore is its own batch.
  4. Plausible, shared with the clip PATCH, not changed: a read-then-write race with the export
     claim. 5. UX: "Urmează proiectul" does not name what the project resolves to; the frame
     preview's "captions are off" note is the visible cue.
- The weak migration test (create_all builds the column on an empty file, so it could not fail)
  was replaced by the reviewer's ALTER-path and failed-ALTER tests.
- UI check in the real app on `1a8a31dc9215` (slice4h00test), reverted to NULL afterwards:
  `data/claude-caption-source/ui-check/UI-CHECK.md`.
