# Reaction framing in the ordinary editor and export

User-authorized continuation with Claude Code (backend) and Codex (frontend,
independent regressions, real-media proof and review). PRP:
[`clipper-reaction-editor.md`](../../PRPs/clipper-reaction-editor.md).

## What the user can do

Open a clip's **Edit clip → Încadrare pentru reacții**. Read a source frame,
draw the watched content and reaction regions, inspect the same rectangles at
other times, then **Aplică încadrarea**. The ordinary still preview and export
honor that saved choice ahead of dynamic planning. **Revino la automat** clears
the explicit edit. Other clips keep their existing behavior.

The reaction selection has fixed 1080:768 proportions (40% of output height).
The content preserves aspect within its upper panel, with blurred fill from
that same selected content. Both rectangles are stored in even source pixels;
the displayed overlays and submitted rectangles use the same coordinates.

This is a manual, fixed crop for one clip. It does not discover the correct
regions or prove that a moving subject stays inside them for the whole clip.
User inspection of several times is still necessary. No project-wide crop or
caption suppression decision is inferred from the local proof.

## Binding and failure behavior

`reaction_binding` records source dimensions, a replacement guard, and the saved
source interval. The guard covers path, file size, modification time and
dimensions; it is **not** a content digest. A subset trim remains valid, but a
source replacement or interval expansion refuses the old crop. It does not
quietly generate a different edit. Clearing the edit also recovers an unbound
or malformed reaction plan. Existing output files are not deleted when their
database references are invalidated.

The source-frame endpoint accepts seconds relative to the clip, reads the
original media and returns decoded source time. This differs intentionally from
the ordinary preview endpoint's delivered timeline after pause removal.

Automatic caption placement uses reaction keep-outs; manually placed captions
remain manual. The existing source-caption policy is unchanged. None of these
checks measures the legibility of source-burned text or the quality of the cut.

## Real media proof

Artifacts: `data/claude-xqc-reference/ordinary_20260919_121637_288451/`.

This proof created a **private new database** under that directory. Its source
path points read-only at the user-provided
`data/clipper/2c8af11153a3/source/source.mp4`; the user's database was not opened
for writes. The sequence was HTTP source frame → HTTP save → database reload →
ordinary `handle_export` → ordinary `/preview-frame`.

- Window: source **2692–2704 s**; source frame request +0.25 s was checked on
  decoded time. Project dynamic editing was enabled; the explicit edit won.
- MP4: **1080×1920, 60 fps, 12 s, audio present**, full decode succeeds.
- File SHA-256: `9c8e61a35456c9a077d38aec4fdeef9ebdaf3adca718c3b453f5922273350c1c`.
  It is byte-identical to the previously inspected D proof. This establishes
  that the ordinary path delivered that same file, not a new visual verdict.
- Saved API plan and binding reach the sidecar unchanged; v2 fingerprint and
  independently re-read output identity match. No added caption filter.
- The ordinary still at +8.75 s was visually inspected; foreground text and
  reaction match D. Its mean absolute RGB difference from decoded MP4 is
  **0.751/255**, a correspondence diagnostic, not an editorial quality score.
- Expanding the saved window to 2691 s makes ordinary preview refuse the old
  crop. The test deliberately exercised this failure after the export.
- **390/390 existing export files** have identical before/after SHA-256 values.

A second diagnostic used a fresh one-second clip in the same private database,
with a deliberately old stored caption height of 0.75. Ordinary preview moved
the test text; differencing the actual PNGs found its ink at x=375–706,
y=640–702, entirely above the reaction panel (starts at y=1152). The image was
inspected: the face is clear, but the test text overlaps writing within the
watched content. **Avoiding the reaction panel does not prove that content text
is unobstructed.** `caption-auto.png` and `caption-verification.json` preserve
that limitation; manual caption positioning remains available.

The actual file is under
`runtime/clipper/ordinary-probe/exports/ordinary-reaction.mp4` in that proof
directory. `verification.json` contains checks and full before/after inventories.

## Limits of validation

The original D limitations still apply: the chosen content region omits 108
source pixels at the left of the watched video, blur consumes vertical space,
and a small source inset ornament remains. See the
[local proof](clipper-reaction-local-2026-09-13.md). Audio has not been listened
to. The linked reference Short has still not been watched; this feature is not
claimed to reproduce its temporal editing.

Final validation: **2,201 Python tests pass, 2 preexisting TikTok 404 tests
deselected** (266.05 s). This includes 63 new reaction-editor tests. Source-pixel
geometry and metadata refusal have **3 passing Node tests**. Frontend TypeScript,
targeted lint and production build pass. Logs are under
`data/claude-xqc-reference/reaction-editor-{full-tests,build-final}.log`.
The editor UI has not been visually exercised
in a browser in this batch. The real-image inspection above is of the backend
preview, not a claim about browser pointer interaction.
