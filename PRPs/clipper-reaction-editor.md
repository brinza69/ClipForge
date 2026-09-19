# Reaction framing in the ordinary editor/export — 13 September 2026

Continue the user-authorized work with Claude Code. Prior batch 635004d has an
explicit plan_reaction_layout builder and real 12s proof, but no ordinary editor
consumer. Do not generalize the probe rectangles to other clips or the VOD.

## Product scope

Add manual per-clip selection on the ORIGINAL source frame: watched content on
top, reaction below. User draws both regions and saves. The saved choice binds
to the source identity and inspected clip window, and overrides dynamic edit
for this clip. Normal preview/export share _decide_render. Default clips retain
their behavior. No global layout classifier, source-caption suppression change,
automatic publishing, DB schema, or detector changes.

Codex owns frontend + docs + real-media independent verification. Claude owns
backend code/tests listed below. Read CLAUDE.md, this PRP and relevant targeted
sections only. No commits, other tools/subagents, external downloads or real DB
writes. Tests must UNSET CLIPFORGE_DATA_DIR. Use server/.venv/Scripts/python.exe.
Keep each code file <=500 lines; clipper_clips.py already has 499, so do not add
to it. Existing unrelated working-tree changes belong to the user.

## Backend API contract (implement exactly so Codex can build frontend)

New router server/routers/clipper_reaction.py, mounted from main.py alongside
clipper_clips. Prefix /api/clipper. Use existing DB session, clip/project load,
feedback and serialize.invalidate_render patterns. All mutations must reject
an exporting clip BEFORE committing. Invalidation clears old render references
without deleting files. Return {clip: clip_to_dict(clip)} after PUT/DELETE.

GET /clips/{id}/reaction-source?t=0.5
- t is seconds relative to current SOURCE window, not trimmed export clock.
- Read ORIGINAL project.video_path; no arbitrary path from request. Refuse
  missing source, nonfinite/negative/outside clip times. Decode via PyAV, seek
  before target and return first decoded frame at/after it, retaining actual
  PTS/time_base. Do not fabricate a decoded frame index from requested time.
- Return image/png, at most 960px wide with aspect maintained, Cache-Control
  no-store. Headers X-Source-Width, X-Source-Height (original decoded dimensions),
  X-Source-Time (actual decoded source seconds), X-Source-Version, X-Clip-Start,
  X-Clip-End. Source version is an opaque digest of resolved source path, size,
  mtime_ns and source dimensions, explicitly a replacement guard, NOT a file
  content hash or proof of full visual coverage. Use actual file/frame dimensions
  for this response and reject inconsistency with project dimensions on save.
- Decode/PNG work off event loop. Bound reading to target and current clip;
  failures return a clear non-success response, never an empty valid image.

PUT /clips/{id}/reaction-layout
JSON {content_rect:{x,y,w,h}, face_rect:{x,y,w,h}, source_version:string,
      source_start:number, source_end:number, src_w:int, src_h:int}
- Rects are even integer original SOURCE pixels. Canonical builder validates.
- Reject stale source version, wrong source dimensions, or source_start/end
  unequal to CURRENT saved clip boundaries. No silent fallback or restamping.
- For this UI, face_pct is fixed at .40. The face selection UI locks aspect to
  1080/768. Refuse |face.w - face.h * 1080/768| > 2 source px so arbitrary
  rectangles cannot stretch a face. Do not change face crop implicitly.
- Build plan with plan_reaction_layout, then attach reaction_binding with
  schema 'clipper_reaction_binding_v1', source_version, source_start, source_end,
  src_w, src_h, by:'human'. This is a user crop selection over a declared
  interval, not a measurement of continuous coverage or a detector result.
- Save into existing clip.layout_plan. Do not add a second stored settings field.
- Preserve existing caption policy and manual caption placement. For automatic
  caption placement, resolve position using NEW plan safe_zones so captions do
  not keep their old position across the bottom reaction band. Persist only
  the resolved caption position if appropriate, retaining words/style. Inspect
  captions.resolve_position; do not create a second caption placement algorithm.
- Record manual layout feedback and invalidate output exactly as normal PATCH.

DELETE /clips/{id}/reaction-layout
- Return to automatic framing by clearing this REACTION plan, invalidate output
  and log manual feedback. If no reaction plan exists, no destructive mutation.
- Do not change project settings or delete files.

## Binding in the ordinary worker

New service server/services/clipper/reaction_edit.py (or two small files if a
real >500-line seam is needed) owns binding/source guard/validation functions.
- _layout_plan in workers/clipper_render_plan.py must detect an explicit fit
  plan before its legacy 'plan fits? else regenerate' branch. Validate binding
  against current file guard, declared dimensions and current clip interval.
- A shorter clip INSIDE the saved interval remains valid; expanding outside,
  another source/version, missing binding, unknown schema or invalid geometry
  must raise an actionable error, not quietly choose a different crop.
- _decide_render must skip _dynamic_plan when validated game_content_fit=True.
  This explicit per-clip edit is what controls the render, even if project has
  dynamic_edit=True. No new global switch. ASS timing/remap remain unchanged.
- Existing unbound local probe calls directly to render_export remain valid;
  binding is required in the ordinary editor/worker path, not the pure builder.
- Take care with _caption_faces: dyn=None cannot establish observed local
  faces; do not let it undo a new static keep-out placement.

## Required backend tests (new test_clipper_reaction_editor.py, <=500 lines)

Exercise API success/save/read/clear, malformed/odd rectangles and bad aspect,
stale frame/source/dimensions/window, busy export refusal, and no mutation on
refusal. Validate an existing bound plan after subset trim; reject expansion
and source change through ordinary _decide_render. Unbound/unknown fit plan
must not fall back. Legacy dynamic planning must remain called for normal clips.
Source endpoint must return actually decoded time and original dimensions with
scaled PNG. Use disposable generated media/DB. Include an ordinary worker
render/preview proving dynamic override and saved v2 sidecar retains binding,
not merely a standalone builder invocation. Reuse common-path fixtures where
appropriate. Verify automatic caption placement respects reaction band and
manual caption position stays user-owned. No real-project DB/init_db calls.

Run focused tests and report exact commands/results in
data/claude-xqc-reference/RESULT-REACTION-EDITOR.md. Codex will review, run full
suite (excluding only two preexisting TikTok 404 tests) and inspect real output.

## Delivery record — 19 September 2026

Implemented by Claude Code and Codex. Independent review corrected the source
clock, frozen source guard, malformed-plan acceptance, silently failed caption
placement and the editor's stale saved-object reference. The normal export was
exercised through a private database and HTTP selection API on the actual local
source. Details and remaining visual limits:
[`clipper-reaction-editor-2026-09-19.md`](../docs/refs/clipper-reaction-editor-2026-09-19.md).
