"""Aggregation and reporting helpers for benchmark_face_detectors.py.

Split from the main script to keep each file under 500 lines.
Imported by both the CLI and the test suite.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STATE_UNAVAIL = "detector_unavailable"
THRESHOLDS = (0.3, 0.5)


# ── latency ───────────────────────────────────────────────────────────────────

def _latency_stats(ms_list: list[float]) -> dict:
    if not ms_list:
        return {"mean": None, "min": None, "max": None}
    return {"mean": sum(ms_list) / len(ms_list),
            "min": min(ms_list), "max": max(ms_list)}


# ── aggregation ───────────────────────────────────────────────────────────────

def aggregate(results: list[dict]) -> dict[str, Any]:
    """TP/FP/FN totals at each IoU threshold with per-detector observation counts.

    GT tracking:
      unscored_gt_faces     — known confirmed GT in frames where the detector
                              could not score (image error OR unavailable).
      uncertain_gt_proposals — raw annotation length from uncertain frames
                              (untrusted; NOT measured GT).
    Refused frames are excluded entirely; their GT count is unknown.
    F1 = 0.0 when tp+fp+fn>0 but precision or recall is 0 (not null).
    Observation/image errors on uncertain frames are counted under errors.
    """
    out: dict[str, Any] = {}
    for det in ("haar", "yunet"):
        # ── threshold-independent counts (one pass) ───────────────────────────
        det_inferred = det_scored = det_unavail = det_errors = 0
        unscored_gt = uncertain_gt_props = 0

        for r in results:
            if r.get("refused"):
                continue
            gt_count = r.get("gt_count")
            if r.get("uncertain"):
                raw = r.get("faces_gt")
                if isinstance(raw, list):
                    uncertain_gt_props += len(raw)
                if r.get("error"):
                    det_errors += 1
                else:
                    d = r.get(det) or {}
                    if d.get("state") == _STATE_UNAVAIL:
                        det_errors += 1
                        det_unavail += 1
                    elif d.get("state"):
                        det_inferred += 1
                continue
            # Confirmed frame
            if r.get("error"):
                det_errors += 1
                if gt_count is not None:
                    unscored_gt += gt_count
                continue
            d = r.get(det) or {}
            if d.get("state") == _STATE_UNAVAIL:
                det_errors += 1
                det_unavail += 1
                if gt_count is not None:
                    unscored_gt += gt_count
                continue
            det_inferred += 1
            if gt_count is not None:
                det_scored += 1

        # ── per-threshold metrics (second pass) ───────────────────────────────
        for thresh in THRESHOLDS:
            key = f"{det}_iou{int(thresh * 10)}"
            tp = fp = fn = 0
            latencies: list[float] = []
            for r in results:
                if r.get("refused") or r.get("error"):
                    continue
                d = r.get(det) or {}
                if d.get("state") == _STATE_UNAVAIL:
                    continue
                if d.get("state"):
                    ms = d.get("inference_ms")
                    if ms is not None:
                        latencies.append(ms)
                thresh_key = f"iou{int(thresh * 10)}"
                frame_scores = (d.get("scores") or {}).get(thresh_key)
                if frame_scores is not None:
                    tp += frame_scores["tp"]
                    fp += frame_scores["fp"]
                    fn += frame_scores["fn"]

            total = tp + fp + fn
            prec = tp / (tp + fp) if tp + fp else (None if total == 0 else 0.0)
            rec = tp / (tp + fn) if tp + fn else (None if total == 0 else 0.0)
            if prec is None or rec is None:
                f1 = None
            elif prec + rec == 0:
                f1 = 0.0
            else:
                f1 = 2 * prec * rec / (prec + rec)

            out[key] = {
                "tp": tp, "fp": fp, "fn": fn,
                "precision": prec, "recall": rec, "f1": f1,
                "inferred": det_inferred,
                "scored": det_scored,
                "unavailable": det_unavail,
                "errors": det_errors,
                "unscored_gt_faces": unscored_gt,
                "uncertain_gt_proposals": uncertain_gt_props,
                "latency_ms": _latency_stats(latencies),
            }
    return out


def aggregate_by_source(results: list[dict]) -> dict[str, dict]:
    """Per-source aggregate including the same frame counts as the global report."""
    by: dict[str, list[dict]] = {}
    for r in results:
        raw_source = r.get("source")
        src = raw_source if isinstance(raw_source, str) and raw_source else "__unknown__"
        by.setdefault(src, []).append(r)
    return {src: {"counts": frame_counts(rows), **aggregate(rows)}
            for src, rows in by.items()}


# ── frame counts ──────────────────────────────────────────────────────────────

def frame_counts(results: list[dict]) -> dict[str, int]:
    """Frame-level observation counts.

    inferred  — detector ran successfully (state in {detected, empty}).
                Includes uncertain frames that ran; not limited to confirmed.
    scored    — detector ran AND frame has confirmed validated GT (gt_count not null).
    paired_scored — both haar and yunet scored on the same frame.
    unknown_gt_frames — frames where gt_count is null (refused/uncertain/null faces).
    """
    counts: dict[str, int] = {
        "requested": len(results),
        "confirmed": 0, "uncertain": 0, "refused": 0,
        "image_errors": 0, "unknown_gt_frames": 0, "known_gt_faces": 0,
        "haar_inferred": 0, "haar_scored": 0, "haar_unavailable": 0,
        "yunet_inferred": 0, "yunet_scored": 0, "yunet_unavailable": 0,
        "paired_scored": 0,
    }
    for r in results:
        if r.get("refused"):
            counts["refused"] += 1
            counts["unknown_gt_frames"] += 1
            continue
        gt_count = r.get("gt_count")
        if gt_count is not None:
            counts["known_gt_faces"] += gt_count
        if r.get("uncertain"):
            counts["uncertain"] += 1
            counts["unknown_gt_frames"] += 1
        else:
            counts["confirmed"] += 1
            if gt_count is None:
                counts["unknown_gt_frames"] += 1
        if r.get("error"):
            counts["image_errors"] += 1
            continue
        haar_sc = yunet_sc = False
        for det, inferred_k, scored_k, unavail_k in (
            ("haar", "haar_inferred", "haar_scored", "haar_unavailable"),
            ("yunet", "yunet_inferred", "yunet_scored", "yunet_unavailable"),
        ):
            state = (r.get(det) or {}).get("state")
            if state == _STATE_UNAVAIL:
                counts[unavail_k] += 1
            elif state is not None:
                counts[inferred_k] += 1
                if gt_count is not None:
                    counts[scored_k] += 1
                    if det == "haar":
                        haar_sc = True
                    else:
                        yunet_sc = True
        if haar_sc and yunet_sc:
            counts["paired_scored"] += 1
    return counts


# ── stub output ───────────────────────────────────────────────────────────────

_EMPTY_COUNTS: dict[str, int] = {
    "requested": 0, "confirmed": 0, "uncertain": 0, "refused": 0,
    "image_errors": 0, "unknown_gt_frames": 0, "known_gt_faces": 0,
    "haar_inferred": 0, "haar_scored": 0, "haar_unavailable": 0,
    "yunet_inferred": 0, "yunet_scored": 0, "yunet_unavailable": 0,
    "paired_scored": 0,
}


def write_stub(out_path: str | None, manifest_hash: str, model: str,
               split: str | None, errors: list[str]) -> None:
    """Write a minimal output JSON when we exit before processing frames."""
    if not out_path:
        return
    try:
        stub = {
            "manifest_hash": manifest_hash, "model": model, "split": split,
            "total_frames": 0, "counts": dict(_EMPTY_COUNTS),
            "errors": errors, "results": [], "totals": {}, "by_source": {},
        }
        Path(out_path).write_text(json.dumps(stub, indent=2), encoding="utf-8")
    except OSError:
        pass
