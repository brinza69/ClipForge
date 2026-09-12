# Face detector B — B1 accepted, B2 probe pending

Codex supervises Claude Code. Batch A remains accepted at `2c64703`.
This document records accepted B1 instrumentation and subsequent measurements.
The new detector is not wired into live analysis or export.

## B1 review

The initial Claude adapter/benchmark passed its tests but Codex reproduced:

- A detector inference failure returned exit 0 from benchmark `main()`.
- One unannotated requested frame produced `total_frames: 0`.
- An invalid annotation state was silently scored.
- Missing Haar cascades were reported as a successful empty detection.
- Rounding a positive 0.3px box produced a detected `[1,1,0,0]` box.
- A missing model carried the expected hash as if it were measured identity.

Reproduction scripts are in `data/claude-face-detection/batch-b/`; the initial
console findings are recorded in `feedback-01.txt` and the session logs.
The fixed-name `review-01/summary.json` and `review-02/summary.json` were
overwritten by subsequent reruns: they are not preserved before-fix reports.
New independent runs use unique artifact directories.
Further review found uncertain frames counted as evaluated and inference
errors hidden in aggregation; `feedback-02.txt` and `feedback-03.txt` specify
the corrections. Codex's final small corrections preserve unavailable counts
for uncertain frames, refuse non-string annotation states, and safely group
refused rows whose source is not a string. A known-GT subtotal now accompanies
the unknown-frame count.

The final independent run is `b1-final-1936/benchmark-native.json`: 40 requested,
39 confirmed and paired-scored, one uncertain, 61 known GT faces, no detector
or image errors. Exit 2 denotes that uncertain annotation. All 40 prediction
rows match the saved independent native API output after endpoint conversion;
image and model hashes match. Seven entrypoint failure/coverage scenarios pass
in `review-03-20260912-193602-994316/`. Maximum-cardinality matching also agrees
with independent exhaustive enumeration on 689 binary adjacency graphs.

Full suite: 2053 passed, two known TikTok 404 failures, exit 1. The attempted
forward-slash deselection did not match this Windows run's node IDs; those
two tests actually ran. No new failure. All six code files are <=500 lines.
The read-only export inventory confirms 390/390 files unchanged.

## Frozen observations

Codex visually inspected decoded PNGs and replayed saved rectangles before
running either detector. Original frames and labels are immutable in this run.
The rectangles are coarse visible-face localization, not certified full-head
containment. Hair/beard extent and partial occlusion affect IoU substantially.

The initial set contains 40 frames, 39 confirmed frames with 61 face boxes,
and one uncertain frame where the foreground watch obscures most of the face.
The uncertain frame remains reported, not a confirmed negative.
Manifest SHA256:
`cdc7bcda0358620574c1793fd3443e6b84a96eb7fbdb0b1c5de28faf3d0fd773`.
Path: `data/claude-face-detection/batch-b/dataset/manifest-frozen-v1.json`.

There are four source groups: co-stream, street speaker, two people indoors,
and podcast. The windows named Speed and Minecraft show the same co-stream
and are grouped together. The filename prefix gaming actually shows a podcast.
These are inspected pixels, not trusted project-name categories.

No confirmed wholly negative frames are in this set. Precision on scenes
without any face and generalization to independent sources remain unmeasured.

## Native-size candidate is insufficient

Official OpenCV Zoo 2023mar YuNet model, CPU, score .9, NMS .3, topK 5000;
480x270 BGR input. Model SHA256:
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
232589 bytes; MIT license retained in `data/models/`.
OpenCV 4.14 native API was called independently of the new adapter.
Its first return is retval (1 even on a blank image), not face count.

| 39 confirmed frames, 61 labels | TP | FP | FN |
|---|---:|---:|---:|
| Haar, IoU .3 | 29 | 12 | 32 |
| YuNet native .9, IoU .3 | 20 | 0 | 41 |
| Haar, IoU .5 | 27 | 14 | 34 |
| YuNet native .9, IoU .5 | 14 | 6 | 47 |

This candidate misses more small faces and is not a drop-in improvement.
Raw outputs: `native-predictions-frozen-v1.json`; exhaustive independent
matching totals: `native-scores-frozen-v1.json` in the batch directory.

## Development selection and fresh confirmation

On development frames only, Codex compared native and 2x linear-interpolated
inputs at scores .6, .75, .9, with the same model/NMS. Interpolation does not
recover source detail. The candidate fixed before fresh-frame labelling was
2x input, score .75: 25 TP, 1 FP, 4 FN at IoU .3 on development labels.
Development figures select a candidate; they do not confirm it.

20 new frames were then sampled, visually labelled and replayed, disjoint by
decoded frame address from the initial 40. They are new moments from the same
four source groups, not an independent-source evaluation. All 20 are labelled,
33 face boxes. Replay found an initially omitted face at the left source edge
of `two_people-0`; it was added before seeing candidate predictions.
Fresh manifest SHA256:
`7a5557296826bdab7608fb1b087ae7cfa48db657a27125816f4fad90a5715075`.
Path: `data/claude-face-detection/batch-b/fresh/manifest-frozen-v1.json`.

| Fresh 20 frames, 33 labels | TP | FP | FN |
|---|---:|---:|---:|
| Haar, IoU .3 | 15 | 8 | 18 |
| YuNet 2x .75, IoU .3 | 27 | 0 | 6 |
| Haar, IoU .5 | 14 | 9 | 19 |
| YuNet 2x .75, IoU .5 | 11 | 16 | 22 |

At IoU .3, co-stream alone improves from 5/16 to 14/16 matched faces. At IoU
.5 it matches none of those 16 coarse boxes: returned boxes cover a smaller
facial region than the labels, which include more head extent. The labels
are not retroactively changed. Better detection counts do not establish better
crop geometry. In particular, directly using the smaller box widths to choose
zoom or claiming head/caption containment would need a separate rendered probe.

Measured mean inference in this one sequential CPU run: Haar 94.3ms, YuNet
24.7ms (YuNet excludes the preceding resize; not an end-to-end speed claim).
Raw outputs, exhaustive one-to-one scores, and per-source figures:
`data/claude-face-detection/batch-b/fresh-confirmation-v1.json`.

## Next acceptance work

Implement the
explicit 2x/.75 candidate with provenance and correctly mapped coordinates,
test it against the frozen raw native outputs, and inspect downstream framing
before changing a live caller. Tracking/subject identity is not supplied by
either detector. Existing corpus exports and stored sidecars stay untouched.
