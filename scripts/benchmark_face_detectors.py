#!/usr/bin/env python3
"""Benchmark Haar vs YuNet face detectors on a labelled PNG frame manifest.

CLI: --manifest, --model, --out, optional --split (dev|holdout).

Exit codes:
  0 — clean: all frames evaluated, no errors, no uncertain labels
  1 — real errors: hash mismatch, bad annotation, inference failure, etc.
  2 — incomplete coverage only: no errors but some frames uncertain/refused

Uncertain frames (annotation_state=uncertain) are image-verified and run
through both detectors; GT scoring is skipped and their predictions are
preserved as raw observations.  A missing image or inference failure on an
uncertain frame is an error (exit 1), not just incomplete coverage (exit 2).
Refused frames (missing/invalid annotation, null/bad GT, invalid field types)
are not scored.  gt_count is set only for confirmed+validated frames; it is
null for refused, uncertain, and error frames whose GT was never confirmed.
No winner or pass label is emitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "server"))

from services.clipper.face_detector import detect_faces, face_cascades  # noqa: E402
from services.clipper.face_yunet import (                                 # noqa: E402
    YuNetDetector,
    MODEL_FILENAME, MODEL_SHA256,
    PROFILES,
)
from benchmark_report import aggregate, aggregate_by_source, frame_counts, write_stub  # noqa: E402

THRESHOLDS = (0.3, 0.5)
VALID_ANNOTATION_STATES = {"confirmed", "uncertain"}
_STATE_UNAVAIL = "detector_unavailable"
_EXPECTED_CASCADE_COUNT = 2   # frontalface_alt2 + profileface
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


# ── geometry ──────────────────────────────────────────────────────────────────

def iou_box(a: list, b: list) -> float:
    """Intersection-over-union for two [x, y, w, h] boxes."""
    ax, ay, aw, ah = a[0], a[1], a[2], a[3]
    bx, by, bw, bh = b[0], b[1], b[2], b[3]
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    if inter == 0.0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def max_cardinality_matching(
    n_pred: int,
    n_gt: int,
    iou_matrix: list[list[float]],
    thresh: float,
) -> list[tuple[int, int]]:
    """Maximum-cardinality one-to-one bipartite matching via augmenting paths.

    A greedy approach can fail when one prediction overlaps multiple GT boxes
    and claims one, leaving a second prediction with only that GT available.
    Example: p0 overlaps g0 (IoU 0.5) and g1 (IoU 0.4); p1 overlaps g0 only
    (IoU 0.5).  Greedy assigns p0→g0; p1 is unmatched.  This finds p0→g1,
    p1→g0 — two matched pairs instead of one.
    """
    adj: dict[int, list[int]] = {
        p: [g for g in range(n_gt) if iou_matrix[p][g] >= thresh]
        for p in range(n_pred)
    }
    match_gt = [-1] * n_gt
    match_pred = [-1] * n_pred

    def _augment(p: int, seen: set[int]) -> bool:
        for g in adj[p]:
            if g in seen:
                continue
            seen.add(g)
            if match_gt[g] == -1 or _augment(match_gt[g], seen):
                match_gt[g] = p
                match_pred[p] = g
                return True
        return False

    for p in range(n_pred):
        _augment(p, set())
    return [(p, g) for p, g in enumerate(match_pred) if g != -1]


def score_against_gt(
    preds: list[list], gts: list[list], thresh: float,
) -> dict[str, Any]:
    n_p, n_g = len(preds), len(gts)
    matrix = [[iou_box(p, g) for g in gts] for p in preds]
    pairs = max_cardinality_matching(n_p, n_g, matrix, thresh)
    tp = len(pairs)
    return {"tp": tp, "fp": n_p - tp, "fn": n_g - tp,
            "pairs": [(int(p), int(g)) for p, g in pairs]}


# ── hash helpers ──────────────────────────────────────────────────────────────

def sha256_path(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_hex64(val: Any) -> bool:
    return isinstance(val, str) and len(val) == 64 and all(c in _HEX_CHARS for c in val)


# ── GT box validation ─────────────────────────────────────────────────────────

def _validate_gt_boxes(boxes: Any, width: int, height: int) -> str | None:
    """Return error string if any GT box is invalid, None if all OK."""
    if not isinstance(boxes, list):
        return "faces must be a list"
    for i, b in enumerate(boxes):
        if not (isinstance(b, (list, tuple)) and len(b) == 4):
            return f"box[{i}] must be [x,y,w,h], got {b!r}"
        try:
            x, y, w, h = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        except (TypeError, ValueError):
            return f"box[{i}] non-numeric"
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            return f"box[{i}] non-finite"
        if w <= 0 or h <= 0:
            return f"box[{i}] non-positive size w={w} h={h}"
        if x < 0 or y < 0 or x + w > width or y + h > height:
            return f"box[{i}] out of bounds for {width}x{height}"
    return None


# ── detectors ─────────────────────────────────────────────────────────────────

def run_haar(bgr: Any) -> dict[str, Any]:
    """Run the Haar cascade detector on a BGR image.

    Requires BOTH expected cascades (frontalface_alt2 + profileface).
    A non-empty but incomplete cascade set is detector_unavailable.
    """
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        return {"state": _STATE_UNAVAIL, "reason": "opencv_unavailable",
                "boxes": [], "inference_ms": None}
    cascades = face_cascades()
    if len(cascades) < _EXPECTED_CASCADE_COUNT:
        return {"state": _STATE_UNAVAIL, "reason": "cascades_unavailable",
                "boxes": [], "inference_ms": None}
    t0 = time.perf_counter()
    try:
        grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        boxes = detect_faces(grey)
        state = "detected" if boxes else "empty"
    except Exception as exc:
        return {"state": _STATE_UNAVAIL, "reason": str(exc),
                "boxes": [], "inference_ms": None}
    return {"state": state, "reason": None, "boxes": boxes,
            "inference_ms": (time.perf_counter() - t0) * 1000.0}


# ── per-frame processing ──────────────────────────────────────────────────────

def _stub(frame: dict) -> dict:
    """Base fields common to all result rows."""
    stub = {
        "id": frame.get("id"),
        "source": frame.get("source"),
        "split": frame.get("split"),
        "annotation_state": frame.get("annotation_state"),
        "faces_gt": frame.get("faces"),   # None if missing/null; aggregate handles this
        "image": frame.get("image"),
        "sha256_declared": frame.get("sha256"),
        "width": frame.get("width"),
        "height": frame.get("height"),
    }
    for k in ("frame_index", "decoded_t", "decoded_address"):
        if k in frame:
            stub[k] = frame[k]
    return stub


def _refused(frame: dict, reason: str, *, row_index: int = -1) -> dict:
    """Row for a frame that cannot be evaluated."""
    return {**_stub(frame), "refused": True, "refused_reason": reason,
            "gt_count": None, "sha256_measured": None, "error": None,
            "row_index": row_index}


def process_frame(frame: dict, yunet: YuNetDetector, *, row_index: int = -1) -> dict:
    """Hash-verify, dimension-check, load image, run both detectors.

    Both confirmed and uncertain frames go through image verification and
    detection.  Uncertain frames skip GT scoring (scores=null).  Image or
    inference failures on uncertain frames are errors (exit 1), not just
    incomplete coverage (exit 2).
    """
    if not isinstance(frame, dict):
        return {"id": None, "refused": True, "refused_reason": "non_dict_frame",
                "faces_gt": None, "gt_count": None, "sha256_measured": None,
                "error": None, "row_index": row_index}

    # Validate required string fields (nonempty str).
    for fld in ("id", "image"):
        val = frame.get(fld)
        if not isinstance(val, str) or not val:
            return _refused(frame, f"invalid_field: {fld}", row_index=row_index)

    # sha256 must be a 64-char hex string.
    if not _is_hex64(frame.get("sha256")):
        return _refused(frame, "invalid_sha256", row_index=row_index)

    # width and height: positive int, not bool (bool is subtype of int in Python).
    for fld in ("width", "height"):
        val = frame.get(fld)
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            return _refused(frame, f"invalid_field: {fld}", row_index=row_index)

    # Optional string fields must be str if present.
    for fld in ("source", "split"):
        if fld in frame and not isinstance(frame[fld], str):
            return _refused(frame, f"invalid_field: {fld}", row_index=row_index)

    ann = frame.get("annotation_state")
    if not isinstance(ann, str) or ann not in VALID_ANNOTATION_STATES:
        return _refused(frame, f"invalid_annotation_state: {ann!r}", row_index=row_index)

    if "faces" not in frame:
        return _refused(frame, "missing_faces_key", row_index=row_index)
    if frame["faces"] is None:
        return _refused(frame, "faces_is_null", row_index=row_index)
    if not isinstance(frame["faces"], list):
        return _refused(frame, f"faces_not_list: {type(frame['faces']).__name__}",
                        row_index=row_index)

    # gt_count: known only for confirmed+validated frames.
    gt_count: int | None = None
    if ann == "confirmed":
        gt_err = _validate_gt_boxes(frame["faces"], frame["width"], frame["height"])
        if gt_err:
            return _refused(frame, f"invalid_gt: {gt_err}", row_index=row_index)
        gt_count = len(frame["faces"])

    # Image verification and detection — required for both confirmed and uncertain.
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        return {**_stub(frame), "refused": False, "uncertain": ann == "uncertain",
                "gt_count": gt_count, "sha256_measured": None,
                "error": "opencv_unavailable", "row_index": row_index}

    img_path = frame["image"]
    try:
        sha_measured = sha256_path(img_path)
    except OSError as exc:
        return {**_stub(frame), "refused": False, "uncertain": ann == "uncertain",
                "gt_count": gt_count, "sha256_measured": None,
                "error": f"read_error: {exc}", "row_index": row_index}
    if sha_measured != frame["sha256"]:
        return {**_stub(frame), "refused": False, "uncertain": ann == "uncertain",
                "gt_count": gt_count, "sha256_measured": sha_measured,
                "error": f"hash_mismatch: {sha_measured[:12]}", "row_index": row_index}
    bgr = cv2.imread(img_path)
    if bgr is None:
        return {**_stub(frame), "refused": False, "uncertain": ann == "uncertain",
                "gt_count": gt_count, "sha256_measured": sha_measured,
                "error": "image_unreadable", "row_index": row_index}
    fh, fw = bgr.shape[:2]
    if fw != frame["width"] or fh != frame["height"]:
        return {**_stub(frame), "refused": False, "uncertain": ann == "uncertain",
                "gt_count": gt_count, "sha256_measured": sha_measured,
                "error": f"dimension_mismatch: got {fw}x{fh}", "row_index": row_index}

    yn = yunet.detect(bgr)
    haar = run_haar(bgr)
    gts: list = frame["faces"]
    is_uncertain = (ann == "uncertain")

    def _scores(boxes: list, state: str) -> dict | None:
        """Per-frame tp/fp/fn at each threshold; None when not computable."""
        if is_uncertain or state == _STATE_UNAVAIL:
            return None
        return {f"iou{int(t * 10)}": score_against_gt(boxes, gts, t)
                for t in THRESHOLDS}

    haar_scores = _scores(haar.get("boxes", []), haar["state"])
    yunet_scores = _scores(yn["boxes"], yn["state"])

    yunet_row: dict[str, Any] = {
        "state": yn["state"], "reason": yn.get("reason"),
        "boxes": yn["boxes"], "scores_per_box": yn.get("scores", []),
        "inference_ms": yn.get("inference_ms"),
        "scores": yunet_scores,
        # Model identity — always present regardless of mock state.
        "model_id": MODEL_FILENAME,
        "expected_model_hash": MODEL_SHA256,
        "model_hash": getattr(yunet, "_measured_hash", yn.get("model_hash")),
        "hash_verified": (getattr(yunet, "_measured_hash", None) == MODEL_SHA256),
        # Missing observed settings stay unknown, including in test doubles.
        "profile": yn.get("profile"),
        "score_thresh": yn.get("score_thresh"),
        "nms_thresh": yn.get("nms_thresh"),
        "top_k": yn.get("top_k"),
        "scale": yn.get("scale"),
        "input_size": yn.get("input_size"),
        "model_input_size": yn.get("model_input_size"),
        "interpolation": yn.get("interpolation"),
        "raw_boxes_model_space": yn.get("raw_boxes_model_space"),
    }
    if yn["state"] == _STATE_UNAVAIL:
        yunet_row["unscored_reason"] = yn.get("reason") or _STATE_UNAVAIL

    haar_row: dict[str, Any] = {
        "state": haar["state"], "reason": haar.get("reason"),
        "boxes": haar["boxes"], "inference_ms": haar.get("inference_ms"),
        "scores": haar_scores,
    }
    if haar["state"] == _STATE_UNAVAIL:
        haar_row["unscored_reason"] = haar.get("reason") or _STATE_UNAVAIL

    return {
        **_stub(frame),
        "sha256_measured": sha_measured,
        "refused": False,
        "uncertain": is_uncertain,
        "gt_count": gt_count,
        "haar": haar_row,
        "yunet": yunet_row,
        "error": None,
        "row_index": row_index,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Benchmark Haar and YuNet on a labelled PNG manifest.")
    ap.add_argument("--manifest", required=True, help="manifest JSON path")
    ap.add_argument("--model", required=True, help="YuNet ONNX model path")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--split", choices=["dev", "holdout"],
                    help="filter to one split")
    ap.add_argument("--profile", choices=list(PROFILES), default="native_v1",
                    help="YuNet detector profile (default: native_v1)")
    args = ap.parse_args(argv)

    exit_code = 0
    run_errors: list[str] = []

    def fail(msg: str) -> None:
        nonlocal exit_code
        run_errors.append(msg)
        print(f"ERROR: {msg}", file=sys.stderr)
        exit_code = 1

    # Hash manifest as raw bytes (avoids newline normalization); read text once.
    try:
        raw_bytes = Path(args.manifest).read_bytes()
        manifest_hash = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8")
    except OSError as exc:
        write_stub(args.out, "", args.model, args.split,
                   [f"manifest unreadable: {exc}"])
        print(f"ERROR: manifest unreadable: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        write_stub(args.out, manifest_hash, args.model, args.split,
                   [f"manifest parse error: {exc}"])
        print(f"ERROR: manifest parse error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(manifest, dict):
        write_stub(args.out, manifest_hash, args.model, args.split,
                   ["manifest is not a JSON object"])
        print("ERROR: manifest is not a JSON object", file=sys.stderr)
        sys.exit(1)
    if manifest.get("schema") != "clipper_face_benchmark_v1":
        write_stub(args.out, manifest_hash, args.model, args.split,
                   ["unknown manifest schema"])
        print("ERROR: unknown manifest schema", file=sys.stderr)
        sys.exit(1)
    frames_raw = manifest.get("frames")
    if not isinstance(frames_raw, list):
        write_stub(args.out, manifest_hash, args.model, args.split,
                   ["frames is not a list"])
        print("ERROR: frames is not a list", file=sys.stderr)
        sys.exit(1)
    frames: list = list(frames_raw)
    if not frames:
        write_stub(args.out, manifest_hash, args.model, args.split,
                   ["empty manifest"])
        print("ERROR: empty manifest", file=sys.stderr)
        sys.exit(1)
    if args.split:
        frames = [f for f in frames
                  if isinstance(f, dict) and f.get("split") == args.split]
        if not frames:
            write_stub(args.out, manifest_hash, args.model, args.split,
                       [f"no frames for split={args.split}"])
            print(f"ERROR: no frames for split={args.split}", file=sys.stderr)
            sys.exit(1)

    # Initialise YuNet (failure reported as error; processing continues).
    yunet = YuNetDetector(args.model, profile=args.profile)
    if yunet.init_error:
        reason, detail = yunet.init_error
        fail(f"YuNet init failed: {reason}: {detail}")

    # Process every frame — refused/uncertain/error frames all stay in results.
    results = [process_frame(f, yunet, row_index=i) for i, f in enumerate(frames)]

    # Propagate per-frame errors and detector failures to exit code.
    for r in results:
        if r.get("refused"):
            fail(f"frame {r.get('id')!r} (row {r.get('row_index', '?')}): "
                 f"refused: {r.get('refused_reason')}")
        elif r.get("error"):
            fail(f"frame {r.get('id')}: {r['error']}")
        else:
            for det in ("haar", "yunet"):
                d = r.get(det) or {}
                if d.get("state") == _STATE_UNAVAIL:
                    fail(f"frame {r.get('id')}: {det} unavailable: {d.get('reason')}")

    counts = frame_counts(results)

    # Exit 2 when no real errors but any uncertain/refused labels remain.
    if exit_code == 0 and counts["uncertain"] + counts["refused"] > 0:
        exit_code = 2

    out = {
        "manifest_hash": manifest_hash,
        "model": args.model,
        "split": args.split,
        "total_frames": len(results),
        "counts": counts,
        "errors": run_errors,
        "results": results,
        "totals": aggregate(results),
        "by_source": aggregate_by_source(results),
    }
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
