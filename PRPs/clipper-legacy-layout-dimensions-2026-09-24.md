# Legacy static layouts: reject unknown coordinate dimensions

Status: authorized to implement by user ('incepe'). Codex owns planning/verdict;
Claude A owns implementation, Claude B owns independent tests and review.
Read CLAUDE.md. Baseline HEAD29a4441; preserve unrelated dirty files and all exports.
Parent context: PRPs/clipper-framing-next-steps-2026-09-24.md.

## Reproduced defect

`workers/clipper_render_plan.py::_plan_fits` documents that bounds do not prove
coordinate identity, yet accepts legacy plans without src_w/src_h using bounds.
The actual layout in `data/clipper/slice4h00test/exports/02dea6f0a9e9.json` passes
for 1920x1080 while its rectangles were authored for 854x480. Adding the true
854x480 dimensions makes the SAME predicate return False. `3e42c5a399c2` is
also affected. Claude manually replaced dea939; that was a data repair.
The rendered source is project.video_path / sidecar source.path =
`data/testclip/slice4h_hd.mp4`, NOT the 854x480 source.mp4 inside project storage.

## Minimal contract for this batch

1. A stored automatic static plan is reusable only with BOTH explicit valid
   integer pixel dimensions matching the dimensions supplied to the planner.
   Missing/partial, zero/negative, bool, strings, fractional, NaN/inf are invalid;
   reject cleanly, do not coerce to an apparently matching integer or raise by int().
2. Remove legacy bounds-as-identity fallback. `_layout_plan` already has a
   replan path: use it for unverifiable automatic legacy plans. Build from current
   observations with their explicit proxy dimensions. NEVER multiply old rectangles
   by guessed 2.25, add new dimensions onto an old plan, or restamp old sidecars.
   Preserve diagnostics explaining why the old plan could not be reused.
3. Valid matching plans remain unchanged. Explicit reaction layouts retain their
   existing binding validation and fail loudly if stale; no automatic substitution
   of a user's bound manual composition.
4. No source replacement, DB migration/write, caption policy change, detector change,
   clock fix, or corpus rerender. Existing exports stay intact. Probe new outputs in
   `data/claude-legacy-layout/` only, via the shared shipping rendering path.
5. Scope is legacy dimension trust, not comprehensive source identity. Determine
   independently whether project dimensions match the actual render source for the
   real probes. If not, refuse the probe and report a separate defect. Do not expand
   this batch into generic source binding without Codex agreement.

## Ownership

Claude A (implementation): `server/workers/clipper_render_plan.py`, existing
`server/tests/test_clipper_analysis.py` legacy expectation ONLY, and documentation
entries in both CURRENT handovers. Update src/helpers only if strictly necessary;
modified files <=500 lines. Preserve measured comments; update obsolete explanation.
Read `services/clipper/layout.py` (new plans already record dimensions),
`reaction_edit.py` validation only, and relevant existing tests. No broad rereads.
Write compact implementation result to `data/claude-legacy-layout/IMPLEMENTATION.md`.

Claude B (independent): `server/tests/test_clipper_legacy_layout_dimensions.py`
and private diagnostics/probes under `data/claude-legacy-layout/review/` only.
Do NOT edit A's files. Start by demonstrating the old bug and writing tests from
this contract; review implementation after A finishes. No test stubs that simply
return the desired plan in place of the real planner under test.

## Required test cases

| Input / operation | Expected |
|---|---|
| legacy 854x480 rectangles inside 1920x1080, no dimension fields | not reusable |
| explicit 854x480 on 1920x1080 | not reusable |
| explicit matching dimensions, ordinary plan | reusable, unchanged |
| missing one dimension; invalid values above | False without exception |
| invalid target dimensions | False without exception |
| `_layout_plan` receives old actual-shaped legacy plan + current observations | real replan, new dimensions and different appropriate geometry |
| same valid plan through `_layout_plan` | same stored plan |
| bound reaction composition | existing validation preserved; stale source rejects |
| preview/export both call common decision | neither bypasses the stricter check |

Use deterministic fixtures for the unit/integration suite; no dependency on a
user's installed corpus in unit tests. The actual sidecars are extra private probes.
Tests must fail on old code for the legacy acceptance, not merely assert a warning.

## Evidence and final checks

- A: focused tests; B: independent focused tests plus the relevant regression set.
- B: read-only audit actual source/plan dimensions for the eight slice4h exports;
  acknowledge two still-bad MP4s do not repair themselves when code changes.
- B: one private paired render for 02dea6f0a9e9 if source and observations are usable;
  show frames before/after using common renderer. Preserve/manual repaired dea939 as
  a control, not as a file to overwrite. Report output identity, effective plan,
  caption treatment, full decode and limitations. No claims that all framing is fixed.
- Exactly one final full suite from server with CLIPFORGE_DATA_DIR unset:
  `.venv/Scripts/python.exe -m pytest -q --tb=short
  --deselect=tests/test_tiktok_transform.py::test_list_endpoint_ok
  --deselect=tests/test_tiktok_transform.py::test_create_rejects_invalid_url`.
  Save actual exit + summary under the private folder. Await completion; do not end
  a Claude CLI session with its test job left in the background.
- Compare against a fresh inventory at batch start. Historical390 unchanged is no
  longer today's state: user-authorized manual work added files/replaced dea939.
- No commit until Codex accepts the narrow batch; then exact owned paths only.

## Economy and recovery

Two NEW short Claude sessions; never resume the 334k-token historical session.
Use Sonnet for the bounded implementation; escalate only on a concrete blocker.
Independent reviewer can use Opus with low effort and a small turn budget for
source/probe judgment. Keep output compact; read only specified files. Save prompt,
session id, report and unfinished work locally so quota exhaustion loses no context.
Codex checks concise reports and counterexamples, not every routine tool output.

## First review and bounded follow-up

A implemented the strict dimension gate; 203 focused tests passed. B independently
wrote 38 tests, passed them, reproduced the old failures, and confirmed real HD
replanning of both defective plans. Bound reaction controls remain valid.
Full suite: 2439 passed, 1 failed, 2 deselected. Failure is the old live-corpus test
in `test_clipper_fingerprint_v2.py`: it assumes every export is unlabelled v1.
The earlier authorized manual exports are v2. Their presence is not corruption.

A follow-up ownership extends ONLY to that test: retain v1 limitation/assumed-legacy
checks, validate each explicit schema by its own contract, and check verification
does not modify input bytes. Do not delete/skip the test, change production
fingerprint rules, or relabel/overwrite corpus records. Add deterministic mixed-schema
coverage if needed so a v2 mismatch cannot be rescued by v1. Also correct the warning
that currently guesses a source replacement when plan dimensions are simply absent.
Save `followup.done` after focused validation.

B continues the private paired render and final full suite after A's follow-up.
Inventory the whole current exports tree before/after the probe, not just one project.
The 70ca/9d2a/manual dea939 edits predate this batch and were authorized; preserve them.

## Closure (coordinator: Claude Code, continuing for Codex at the user's request)

- Review found `clipper_render_plan.py` at 506 lines (>500). A new short CLI session
  (`claude.exe -p`, Sonnet, no MCP, turn and cost caps; `data/claude-legacy-layout/implementer-500.*`)
  condensed ONLY the new `_valid_pixel_dim` docstring → 499 lines; 202 focused tests passed.
- The coordinator verified the full diff, the 38 independent tests (real planner, no stubs), the static
  pair PNGs, then ran the single final suite: **2441 passed, 2 deselected, exit 0**
  (`data/claude-legacy-layout/review/final-suite.txt`). Exports: 395 before/after, 0 changed.
- Verdict: narrow batch ACCEPTED (`data/claude-legacy-layout/VERDICT.md`). Commit only with the user's
  approval, exact paths: `server/workers/clipper_render_plan.py`, `server/tests/test_clipper_analysis.py`,
  `server/tests/test_clipper_fingerprint_v2.py`, `server/tests/test_clipper_legacy_layout_dimensions.py`,
  this PRP.
- Still bad on disk: 02dea6f0a9e9, 3e42c5a399c2 (need a re-export; a reaction framing identical to
  dea939's is prepared and validated, pending the user's approval to replace the existing exports).
