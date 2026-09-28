# Clipper face detector — batch B, 12 September 2026

## Goal and ownership

User requests continued implementation through Claude Code, Codex reviews and
tests. Continue useful batches until Claude's quota is exhausted, then persist
and stop until the user explicitly resumes. No quotas purchased or reset.
Batch A is accepted at `2c64703`; do not reapply its old feedback files.

This first sub-batch compares an actual replacement for Haar without changing
the shipping detector yet. Codex owns the labelled real-frame corpus and model
download. Claude owns the adapter, benchmark, and tests. No other agents.

Read CLAUDE.md; relevant code is server/services/clipper/face_detector.py,
dynamic_window.py, and test_clipper_face_detector.py. Avoid rereading historic
handovers. Preserve all unrelated working-tree changes. No DB writes/init_db,
rewriting exports, editing user policies or commits before Codex acceptance.
Use server/.venv/Scripts/python.exe. Every changed code file <=500 lines.

## B1 deliverable (stop and report when complete)

1. Independent `server/services/clipper/face_yunet.py` adapter using installed
   OpenCV 4.14 `cv2.FaceDetectorYN`. Do not alter face_detector.py, signals.py
   or the live caller in B1. CPU; BGR image input at native image size; no
   Haar fallback on YuNet error. Each instance owns its mutable detector.
   Expose detected/empty/detector_unavailable and the reason, xywh pixel boxes,
   per-box scores, model ID/hash and inference time. No face identity claim.
   Clamp boxes to image bounds, reject non-finite/nonpositive boxes; an invalid
   detector output is a refusal, never a demonstrated empty observation.
   Model missing, wrong hash, init/inference error remain explicit unavailable.
2. Pinned model: OpenCV Zoo `face_detection_yunet_2023mar.onnx` (not 2026may;
   installed engine is OpenCV 4.x). Expected SHA256:
   `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`, 232589 bytes.
   Codex supplies data/models/face_detection_yunet_2023mar.onnx and MIT license.
   No network download during inference. Do not install packages globally.
   Initial frozen thresholds: score .9, NMS .3, topK 5000. No tuning to labels.
3. `scripts/benchmark_face_detectors.py`: run Haar and YuNet on the exact same
   saved PNGs and compare to a manifest supplied by Codex. CLI takes --manifest,
   --model, --out and optional --split (dev/holdout). Hash-check every frame and
   its dimensions before inference. Report the manifest hash, each requested
   frame (including refusal), per-frame/per-source and total TP/FP/FN with
   one-to-one box matching at both IoU .3 and .5, latency and error counts.
   Use maximum-cardinality matching, not a greedy match that loses valid pairs.
   Empty manifest, unannotated frame, bad image/hash/dimensions, missing model
   or failed inference makes the RUN nonzero, even under JSON output. Never
   remove an unreadable frame from the denominator. Keep raw predictions.
   No winner/pass label based on more returned boxes alone.
4. Manifest schema (Codex writes it; Claude must not edit labels/images):
   {"schema":"clipper_face_benchmark_v1","frames":[
     {"id":"...","source":"...","split":"dev|holdout",
      "image":"ABSOLUTE PNG PATH","sha256":"...","width":480,"height":270,
      "annotation_state":"confirmed|uncertain","faces":[[x,y,w,h],...],
      "by":"codex_agent","note":"..."}]}
   confirmed + empty faces is a visually confirmed negative frame. uncertain
   is unevaluated and must be reported, not scored as a negative example.
5. Tests: state failures vs valid empty, bbox mapping/clipping/invalid output,
   model identity, one-to-one matching including a greedy counterexample,
   manifest refusals reaching main()'s exit status, no omission from totals.
   Do not add detector thresholds or a tracker in this sub-batch.
6. Update docs/clipper-map.md for new files. Save concise RESULT.md to
   data/claude-face-detection/batch-b/ with commands, measured results, paths
   and limitations. No commit. Codex will run independent probes before wiring
   any new detector into the pipeline.

## Primary references already checked by Codex

- https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
  (Git LFS pointer independently supplies the expected hash and size above.)
- https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html
- https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/LICENSE
  (MIT; retain license and attribution when distributing weights.)
