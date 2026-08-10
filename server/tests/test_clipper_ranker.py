"""
Tests for the AI Stream Clipper baseline ranker.

Pure numpy + filesystem: no DB, no ffmpeg, no network. feedback.py is the only
clipper module that touches SQL and is deliberately not exercised here.
"""

import math

import pytest


@pytest.fixture
def ranker():
    from services.clipper import ranker as mod

    return mod


# ── vectorize ────────────────────────────────────────────────────────────────

def test_vectorize_returns_one_value_per_feature_in_order(ranker):
    vec = ranker.vectorize({name: 1.0 for name in ranker.FEATURE_ORDER})
    assert len(vec) == len(ranker.FEATURE_ORDER)
    assert all(v == 1.0 for v in vec)


def test_vectorize_fills_missing_keys_with_zero(ranker):
    vec = ranker.vectorize({"duration": 12.5})
    idx = ranker.FEATURE_ORDER.index("duration")
    assert vec[idx] == 12.5
    assert sum(1 for v in vec if v == 0.0) == len(ranker.FEATURE_ORDER) - 1


def test_vectorize_neutralises_nan_inf_and_junk(ranker):
    vec = ranker.vectorize(
        {
            "duration": float("nan"),
            "motion_mean": float("inf"),
            "word_count": None,
            "silence_ratio": "loud",
            "pronoun_start": True,
            "unknown_feature": 99.0,
        }
    )
    assert len(vec) == len(ranker.FEATURE_ORDER)
    assert all(math.isfinite(v) for v in vec)
    for name in ("duration", "motion_mean", "word_count", "silence_ratio"):
        assert vec[ranker.FEATURE_ORDER.index(name)] == 0.0
    # Booleans are legitimate features (ends_on_sentence etc.), not junk.
    assert vec[ranker.FEATURE_ORDER.index("pronoun_start")] == 1.0


def test_vectorize_handles_an_empty_dict(ranker):
    assert ranker.vectorize({}) == [0.0] * len(ranker.FEATURE_ORDER)


# ── train / predict ──────────────────────────────────────────────────────────

def _separable_rows(n_per_class: int = 20) -> list[dict]:
    """Two clean classes on audio_rms_mean, with a second uninformative feature
    so training has something to shrink."""
    rows = []
    for i in range(n_per_class):
        rows.append(
            {
                "clip_id": f"pos{i}",
                "features": {"audio_rms_mean": 1.0, "duration": 30.0 + i},
                "label": 1.0,
                "group": "proj1",
            }
        )
        rows.append(
            {
                "clip_id": f"neg{i}",
                "features": {"audio_rms_mean": 0.0, "duration": 30.0 + i},
                "label": 0.0,
                "group": "proj1",
            }
        )
    return rows


def test_train_separates_the_two_classes(ranker):
    model = ranker.train(_separable_rows())
    assert model["version"] == ranker.MODEL_VERSION
    assert model["n"] == 40
    assert len(model["weights"]) == len(ranker.FEATURE_ORDER)

    hot = ranker.predict(model, {"audio_rms_mean": 1.0, "duration": 30.0})
    cold = ranker.predict(model, {"audio_rms_mean": 0.0, "duration": 30.0})
    assert 0.0 <= cold < 0.5 < hot <= 1.0
    assert hot - cold > 0.3


def test_train_is_deterministic(ranker):
    rows = _separable_rows()
    a = ranker.train(rows)
    b = ranker.train(rows)
    assert a["weights"] == b["weights"]
    assert a["bias"] == b["bias"]


def test_train_guards_zero_variance_features(ranker):
    # duration is constant here — std 0 must become 1.0, not inf/NaN.
    rows = [
        {"features": {"audio_rms_mean": float(i % 2), "duration": 30.0},
         "label": float(i % 2), "group": "p", "clip_id": str(i)}
        for i in range(10)
    ]
    model = ranker.train(rows)
    assert all(s > 0 for s in model["std"])
    assert all(math.isfinite(w) for w in model["weights"])
    assert math.isfinite(model["bias"])


def test_train_on_no_rows_returns_a_usable_empty_model(ranker):
    model = ranker.train([])
    assert model["n"] == 0
    assert len(model["weights"]) == len(ranker.FEATURE_ORDER)
    # An empty model must still answer, at chance.
    assert ranker.predict(model, {"duration": 20.0}) == pytest.approx(0.5)


def test_predict_rejects_a_malformed_model_without_raising(ranker):
    assert ranker.predict({"weights": [1.0], "feature_order": ["a", "b"]}, {"a": 1}) == 0.5
    assert ranker.predict({}, {"duration": 10.0}) == 0.5
    assert ranker.predict(None, {"duration": 10.0}) == 0.5


def test_predict_replays_the_models_own_feature_order(ranker):
    # An older ranker.json trained on a shorter feature list must keep working.
    model = {
        "version": "lr-0",
        "feature_order": ["audio_rms_mean"],
        "weights": [4.0],
        "bias": 0.0,
        "mean": [0.0],
        "std": [1.0],
    }
    assert ranker.predict(model, {"audio_rms_mean": 1.0}) > 0.9
    assert ranker.predict(model, {"audio_rms_mean": -1.0}) < 0.1


# ── evaluate ─────────────────────────────────────────────────────────────────

def _linear_model(weight: float = 4.0) -> dict:
    """A hand-built model whose score is just sigmoid(weight * x) — lets the
    NDCG tests control the predicted ordering exactly."""
    return {
        "version": "test",
        "feature_order": ["audio_rms_mean"],
        "weights": [weight],
        "bias": 0.0,
        "mean": [0.0],
        "std": [1.0],
    }


def _rows_with_scores(xs: list[float], labels: list[float]) -> list[dict]:
    return [
        {"clip_id": f"c{i}", "features": {"audio_rms_mean": x}, "label": lab, "group": "proj1"}
        for i, (x, lab) in enumerate(zip(xs, labels))
    ]


def test_ndcg_at_5_is_one_for_a_perfect_ordering(ranker):
    rows = _rows_with_scores([6, 5, 4, 3, 2, 1], [1.0, 1.0, 0.75, 0.0, 0.0, 0.0])
    metrics = ranker.evaluate(_linear_model(), rows)
    assert metrics["ndcg_at_5"] == pytest.approx(1.0)
    assert metrics["precision_at_5"] == pytest.approx(0.6)  # 3 of the top 5
    assert metrics["auc"] == pytest.approx(1.0)
    assert metrics["n"] == 6


def test_ndcg_at_5_drops_for_a_reversed_ordering(ranker):
    perfect = ranker.evaluate(
        _linear_model(), _rows_with_scores([6, 5, 4, 3, 2, 1], [1.0, 1.0, 0.75, 0.0, 0.0, 0.0])
    )
    reversed_ = ranker.evaluate(
        _linear_model(), _rows_with_scores([1, 2, 3, 4, 5, 6], [1.0, 1.0, 0.75, 0.0, 0.0, 0.0])
    )
    assert reversed_["ndcg_at_5"] < perfect["ndcg_at_5"]
    assert reversed_["auc"] < 0.5


def test_evaluate_skips_degenerate_groups(ranker):
    # One row, and a group where every label is identical: any ordering scores
    # 1.0, so neither may contribute to the average.
    rows = [
        {"clip_id": "a", "features": {"audio_rms_mean": 1.0}, "label": 1.0, "group": "solo"},
        {"clip_id": "b", "features": {"audio_rms_mean": 1.0}, "label": 1.0, "group": "flat"},
        {"clip_id": "c", "features": {"audio_rms_mean": 0.0}, "label": 1.0, "group": "flat"},
    ]
    metrics = ranker.evaluate(_linear_model(), rows)
    assert metrics["ndcg_at_5"] == 0.0
    assert metrics["n"] == 3


def test_evaluate_on_no_rows_is_safe(ranker):
    metrics = ranker.evaluate(_linear_model(), [])
    assert metrics == {
        "ndcg_at_5": 0.0,
        "precision_at_5": 0.0,
        "auc": 0.5,
        "n": 0,
        "baseline_ndcg_at_5": 0.0,
    }


def test_evaluate_records_the_baseline_ordering(ranker):
    # Incoming order is the heuristic ranking: here it is perfect, the model's
    # ordering is reversed, so the model must not beat the baseline.
    rows = _rows_with_scores([1, 2, 3, 4, 5, 6], [1.0, 1.0, 0.75, 0.0, 0.0, 0.0])
    metrics = ranker.evaluate(_linear_model(), rows)
    assert metrics["baseline_ndcg_at_5"] == pytest.approx(1.0)
    assert metrics["ndcg_at_5"] < metrics["baseline_ndcg_at_5"]


# ── should_use_learned ───────────────────────────────────────────────────────

def _model_with(ndcg: float, baseline: float) -> dict:
    return {
        "version": "test",
        "weights": [1.0],
        "feature_order": ["audio_rms_mean"],
        "bias": 0.0,
        "mean": [0.0],
        "std": [1.0],
        "metrics": {"ndcg_at_5": ndcg, "baseline_ndcg_at_5": baseline},
    }


def test_should_use_learned_needs_enough_examples(ranker):
    good = _model_with(0.9, 0.7)
    assert ranker.should_use_learned(good, ranker.MIN_TRAINING_EXAMPLES) is True
    assert ranker.should_use_learned(good, ranker.MIN_TRAINING_EXAMPLES - 1) is False


def test_should_use_learned_needs_to_beat_the_baseline(ranker):
    n = ranker.MIN_TRAINING_EXAMPLES + 10
    assert ranker.should_use_learned(_model_with(0.71, 0.70), n) is True
    assert ranker.should_use_learned(_model_with(0.70, 0.70), n) is False  # a tie keeps heuristic
    assert ranker.should_use_learned(_model_with(0.60, 0.70), n) is False


def test_should_use_learned_rejects_missing_or_broken_models(ranker):
    n = ranker.MIN_TRAINING_EXAMPLES + 10
    assert ranker.should_use_learned(None, n) is False
    assert ranker.should_use_learned({}, n) is False
    assert ranker.should_use_learned({"weights": [1.0]}, n) is False


# ── persistence ──────────────────────────────────────────────────────────────

@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def test_load_model_returns_none_when_never_trained(ranker, model_dir):
    assert ranker.load_model() is None


def test_save_then_load_round_trips(ranker, model_dir):
    model = ranker.train(_separable_rows())
    path = ranker.save_model(model)
    assert path == model_dir / "clipper" / "ranker.json"
    assert path.exists()

    loaded = ranker.load_model()
    assert loaded is not None
    assert loaded["weights"] == model["weights"]
    assert ranker.predict(loaded, {"audio_rms_mean": 1.0}) == pytest.approx(
        ranker.predict(model, {"audio_rms_mean": 1.0})
    )


def test_load_model_swallows_a_corrupt_file(ranker, model_dir):
    path = ranker.model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert ranker.load_model() is None

    path.write_text('{"version": "lr-1"}', encoding="utf-8")
    assert ranker.load_model() is None
