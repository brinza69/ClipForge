# pilotf81b — what is actually in the frame, all 15 windows

**Inspected 2026-09-05 by the agent**, through `scripts/dump_caption_frames.py`:
35 contact sheets, ~347 sampled frames, every clip in the project. Codex asked
for this before any declaration was applied beyond the two clips already looked
at, and the reason is in the result: **the declaration I was about to make would
have been wrong on two of the fifteen.**

## The question

`layout_policy` asks whether the source has a SECOND CAMERA — a composite, a
facecam over gameplay, a second guest window — because `dynamic_cameras.camera_rects`
builds `game` and `game_tight` from geometry alone ("everything to the right of
the facecam, minus the chat strip") and never asks whether anything is there.

## What is there

| clip | what the frames show |
|---|---|
| `30d7c6d4eae5` | one camera, one person, street |
| `5331ff266a9f` | one camera |
| `6053a598cf06` | one camera |
| `63469342ee88` | one camera |
| `7fe211b7e3bf` | one camera |
| `95e4b5b241a8` | one camera |
| `976e2a8059a3` | one camera |
| `e02a22f6d6ff` | one camera |
| `e627517e88a2` | one camera |
| `ecdcea8863c1` | one camera |
| `f2c0424c76bc` | one camera |
| `b23c14c41495` | one camera, and the speaker holds an Apple Watch up to the lens from ~228.8 s — one camera, two targets |
| `52afdf25163d` | one camera, and **shot 19 (t≈276.0 s) is a full-frame close-up of the watch on a table with no person in it** — a cutaway the creator edited into the source |
| **`54a7e6d31dc8`** | **a picture-in-picture INSERT, top right, two photographs of another person.** Absent in shots 0–3, present from shot 4 (t≈1212.7 s) to the end |
| **`de3ce372ba3b`** | **the same insert, throughout** — its window, 1248–1264 s, sits inside the inserted stretch |

## Three things this settles

**A declaration of "no second camera" for this PROJECT would be false.**
`54a7e6d31dc8` and `de3ce372ba3b` carry a real second region, in the top right,
which is roughly where `camera_rects` points. On those two clips the geometric
second camera may well be framing something worth framing, and the 14.9 s of
"off-subject" measured on `54a7e6d31dc8` is not obviously a defect at all.

**The second region is TIME-VARYING inside a clip.** `54a7e6d31dc8` has none for
its first four shots and one for the remaining thirty-three. A per-project
switch cannot express that, and a per-clip one cannot either.

**The source has its own edits.** `52afdf25163d` cuts to a close-up of the watch
with no person in frame. A rule that treats "the delivered window excludes the
subject" as a defect would flag a shot the creator deliberately made.

## What it does not settle

Whether the insert should be cut to. It is a second region; whether the grammar
`camera_rects` applies to it — a fixed rectangle beside the face, chased by
whatever moved — is the right treatment for a still photograph in the corner is
a different question nobody has asked yet.

And the coverage is sampled, not exhaustive: one frame per shot on thirteen
clips and two per shot on the two probe clips. An insert that appears and
disappears inside a single shot would not be seen.
