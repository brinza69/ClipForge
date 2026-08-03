"""
ClipForge — AI Stream Clipper: baseline learn-to-rank model.

A logistic regression over the frozen candidate feature vector, trained by plain
gradient descent in numpy. No scikit-learn: the whole point of the baseline is
that it ships with the dependencies already on the rig, trains in milliseconds
on a few hundred rows, and serialises to a JSON file a human can read.

The model is advisory. `should_use_learned` is the gate: the learned ordering is
only used once there are enough labels AND it beats the heuristic ordering it
would replace on NDCG@5. Otherwise the heuristic weights stand — a model trained
on 12 clicks would be worse than the hand-tuned profiles, not better.

Pure module: no DB, no network. feedback.training_rows() feeds it.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

logger = logging.getLogger("clipforge.clipper.ranker")

MODEL_VERSION = "lr-1"

# Below this many labelled clips the learned ordering is never used, however
# good its offline metrics look — small samples overfit spectacularly.
MIN_TRAINING_EXAMPLES = 40

# Frozen and versioned: bump MODEL_VERSION if this changes, because a saved
# model's stored feature_order is what predict() replays, not this tuple.
# Mirrors the keys candidates.extract_features emits; unknown keys are ignored
# and missing ones read as 0.0, so the two can drift without an exception.
FEATURE_ORDER: tuple[str, ...] = (
    "duration",
    "position_ratio",
    "words_per_second",
    "word_count",
    "question_count",
    "exclamation_count",
    "sentence_count",
    "avg_word_confidence",
    "silence_ratio",
    "audio_rms_mean",
    "audio_rms_max",
    "audio_peak_count",
    "audio_energy_delta",
    "motion_mean",
    "motion_max",
    "scene_cut_count",
    "face_presence_ratio",
    "first_sentence_len",
    "emotion_word_ratio",
    "pronoun_start",
    "ends_on_sentence",
    "starts_on_sentence",
)

# A label at or above this counts as a positive for precision@K and AUC
# (exported = 1.0 and approved = 0.75 are positives, rejected = 0.0 is not).
POSITIVE_THRESHOLD = 0.5

_TOP_K = 5


# ── Feature vectorisation ────────────────────────────────────────────────────

def _as_float(value: Any) -> float:
    """Anything that is not a finite number becomes 0.0. Feature extraction runs
    over 3-second clips and silent audio, so NaN/inf reach here in practice and
    a single NaN would poison every weight in the model."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if not isinstance(value, (int, float)):
        return 0.0
    f = float(value)
    return f if math.isfinite(f) else 0.0


def _vectorize_order(features: dict | None, order: Sequence[str]) -> list[float]:
    src = features if isinstance(features, dict) else {}
    return [_as_float(src.get(name, 0.0)) for name in order]


def vectorize(features: dict) -> list[float]:
    """Feature dict -> vector in FEATURE_ORDER. Missing keys and non-finite
    values read as 0.0; unknown keys are dropped."""
    return _vectorize_order(features, FEATURE_ORDER)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # Split on sign to keep exp() from overflowing on large-magnitude logits.
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def _empty_model() -> dict:
    n_feat = len(FEATURE_ORDER)
    return {
        "version": MODEL_VERSION,
        "weights": [0.0] * n_feat,
        "bias": 0.0,
        "feature_order": list(FEATURE_ORDER),
        "mean": [0.0] * n_feat,
        "std": [1.0] * n_feat,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n": 0,
        "metrics": {
            "ndcg_at_5": 0.0,
            "precision_at_5": 0.0,
            "auc": 0.5,
            "n": 0,
            "baseline_ndcg_at_5": 0.0,
        },
    }


# ── Training ─────────────────────────────────────────────────────────────────

def train(
    rows: list[dict],
    *,
    epochs: int = 400,
    lr: float = 0.1,
    l2: float = 1e-3,
) -> dict:
    """Fit the baseline on [{"features","label",...}] rows.

    Deterministic: weights start at zero, no shuffling, no random init — the
    same training set must always produce the same ranker.json, otherwise
    "did the model change?" is unanswerable.

    Features are standardised and the mean/std are stored in the model so
    predict() replays the exact same transform.
    """
    usable = [r for r in rows or [] if isinstance(r, dict) and r.get("features") is not None]
    if not usable:
        logger.warning("clipper ranker: no usable training rows, returning empty model")
        return _empty_model()

    X = np.array([vectorize(r["features"]) for r in usable], dtype=float)
    y = np.array([_as_float(r.get("label")) for r in usable], dtype=float)
    y = np.clip(y, 0.0, 1.0)

    mean = X.mean(axis=0)
    std = X.std(axis=0)
    # A constant feature has zero variance; dividing by it yields inf/NaN, so it
    # is left un-scaled and its centred value is simply 0.
    std = np.where(np.isfinite(std) & (std > 1e-12), std, 1.0)
    Z = (X - mean) / std
    Z = np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)

    n, n_feat = Z.shape
    w = np.zeros(n_feat, dtype=float)
    b = 0.0
    for _ in range(max(0, int(epochs))):
        p = _sigmoid(Z @ w + b)
        err = (p - y) / n
        # L2 on the weights only — penalising the bias would fight the class
        # balance rather than the overfitting.
        w -= lr * (Z.T @ err + l2 * w)
        b -= lr * float(err.sum())

    model = {
        "version": MODEL_VERSION,
        "weights": [float(v) for v in w],
        "bias": float(b),
        "feature_order": list(FEATURE_ORDER),
        "mean": [float(v) for v in mean],
        "std": [float(v) for v in std],
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "n": int(n),
        "metrics": {},
    }
    # Train-set metrics, not held-out: with 40-200 rows a holdout split is
    # mostly noise. The guardrail that matters is the comparison against the
    # heuristic baseline on the same rows.
    model["metrics"] = evaluate(model, usable)
    return model


# ── Inference ────────────────────────────────────────────────────────────────

def predict(model: dict, features: dict) -> float:
    """Learned probability that this candidate is worth keeping, 0..1.

    Replays the model's OWN feature_order and scaling, so an older ranker.json
    keeps working after FEATURE_ORDER grows. Returns 0.5 (no opinion) for a
    malformed model rather than raising into the scoring pass.
    """
    if not isinstance(model, dict):
        return 0.5
    order = model.get("feature_order") or list(FEATURE_ORDER)
    weights = model.get("weights") or []
    if len(weights) != len(order):
        logger.warning("clipper ranker: model shape mismatch, falling back to 0.5")
        return 0.5

    mean = model.get("mean") or [0.0] * len(order)
    std = model.get("std") or [1.0] * len(order)
    if len(mean) != len(order) or len(std) != len(order):
        return 0.5

    x = _vectorize_order(features, order)
    z = _as_float(model.get("bias"))
    for value, mu, sigma, weight in zip(x, mean, std, weights):
        s = _as_float(sigma)
        if s <= 1e-12:
            s = 1.0
        z += ((value - _as_float(mu)) / s) * _as_float(weight)
    if not math.isfinite(z):
        return 0.5
    return float(1.0 / (1.0 + math.exp(-z))) if z >= -700 else 0.0


# ── Offline evaluation ───────────────────────────────────────────────────────

def _dcg(labels: Sequence[float]) -> float:
    return sum(float(rel) / math.log2(i + 2) for i, rel in enumerate(labels))


def _ndcg_at_k(ordered_labels: Sequence[float], k: int) -> float | None:
    """NDCG of one already-ordered group. None when the ideal DCG is 0 (no
    positive label at all), which would otherwise be a 0/0."""
    ideal = sorted(ordered_labels, reverse=True)[:k]
    idcg = _dcg(ideal)
    if idcg <= 0:
        return None
    return _dcg(list(ordered_labels)[:k]) / idcg


def _baseline_rank(row: dict, index: int) -> float:
    """Sort key for the ordering the learned model has to beat.

    Uses an explicit heuristic score if the caller attached one, then
    rank_position, and finally the incoming order — feedback.training_rows()
    returns rows in each project's rank_position order for exactly this reason.
    """
    if isinstance(row, dict):
        if row.get("baseline_score") is not None:
            return -_as_float(row["baseline_score"])
        if row.get("rank_position") is not None:
            return _as_float(row["rank_position"])
    return float(index)


def _auc(scores: Sequence[float], labels: Sequence[float]) -> float:
    """Pairwise AUC with ties at 0.5. Returns 0.5 (chance) when one class is
    missing — undefined, not perfect."""
    pos = [s for s, lab in zip(scores, labels) if lab >= POSITIVE_THRESHOLD]
    neg = [s for s, lab in zip(scores, labels) if lab < POSITIVE_THRESHOLD]
    if not pos or not neg:
        return 0.5
    wins = 0.0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1.0
            elif p == q:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def evaluate(model: dict, rows: list[dict]) -> dict:
    """{"ndcg_at_5","precision_at_5","auc","n"} plus "baseline_ndcg_at_5".

    NDCG and precision are computed per project and averaged: ranking a podcast
    clip against a gaming clip is meaningless, only the ordering within one
    source matters. Groups with a single row or a single distinct label are
    skipped — any ordering of those scores 1.0 and would inflate the average
    into promoting a model that learned nothing.
    """
    usable = [r for r in rows or [] if isinstance(r, dict)]
    metrics = {
        "ndcg_at_5": 0.0,
        "precision_at_5": 0.0,
        "auc": 0.5,
        "n": len(usable),
        "baseline_ndcg_at_5": 0.0,
    }
    if not usable:
        return metrics

    scored = [(r, predict(model, r.get("features") or {}), _as_float(r.get("label"))) for r in usable]
    metrics["auc"] = _auc([s for _r, s, _l in scored], [lab for _r, _s, lab in scored])

    groups: dict[Any, list[tuple[dict, float, float]]] = {}
    for row, score, label in scored:
        groups.setdefault(row.get("group"), []).append((row, score, label))

    ndcgs: list[float] = []
    precisions: list[float] = []
    baselines: list[float] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        labels = [lab for _r, _s, lab in members]
        if len(set(labels)) < 2:
            continue
        k = min(_TOP_K, len(members))

        # Stable tie-break on the incoming (heuristic) order so equal scores do
        # not make the result depend on dict iteration order.
        order = sorted(range(len(members)), key=lambda i: (-members[i][1], i))
        model_labels = [labels[i] for i in order]
        nd = _ndcg_at_k(model_labels, k)
        if nd is None:
            continue
        ndcgs.append(nd)
        precisions.append(
            sum(1 for lab in model_labels[:k] if lab >= POSITIVE_THRESHOLD) / k
        )

        base_order = sorted(
            range(len(members)), key=lambda i: (_baseline_rank(members[i][0], i), i)
        )
        base_nd = _ndcg_at_k([labels[i] for i in base_order], k)
        baselines.append(base_nd if base_nd is not None else 0.0)

    if ndcgs:
        metrics["ndcg_at_5"] = sum(ndcgs) / len(ndcgs)
        metrics["precision_at_5"] = sum(precisions) / len(precisions)
    if baselines:
        metrics["baseline_ndcg_at_5"] = sum(baselines) / len(baselines)
    return metrics


def should_use_learned(model: dict | None, rows_count: int) -> bool:
    """The promotion gate. Both conditions, no exceptions: enough labelled
    examples, and a better within-project NDCG@5 than the heuristic ordering it
    would replace. A tie keeps the heuristic — it is the known quantity."""
    if not isinstance(model, dict) or not model.get("weights"):
        return False
    if not isinstance(rows_count, int) or rows_count < MIN_TRAINING_EXAMPLES:
        return False
    metrics = model.get("metrics")
    if not isinstance(metrics, dict):
        return False
    learned = _as_float(metrics.get("ndcg_at_5"))
    baseline = _as_float(metrics.get("baseline_ndcg_at_5"))
    return learned > baseline


# ── Persistence ──────────────────────────────────────────────────────────────

def model_path() -> Path:
    from config import settings

    return settings.data_dir / "clipper" / "ranker.json"


def save_model(model: dict) -> Path:
    """Atomically write ranker.json. A half-written model file would be read by
    the next scoring run, so temp + replace, same as storage.py."""
    path = model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".json.tmp{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info(f"clipper ranker saved: {path} (n={model.get('n')})")
    return path


def load_model() -> dict | None:
    """The saved model, or None when absent or unusable. A corrupt file must
    degrade to the heuristic path, never take the scoring pass down."""
    path = model_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        logger.exception(f"corrupt clipper ranker model at {path}")
        return None
    if not isinstance(data, dict) or not data.get("weights") or not data.get("feature_order"):
        logger.warning(f"clipper ranker model at {path} is missing required keys")
        return None
    return data
