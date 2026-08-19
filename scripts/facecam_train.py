"""Train a small classifier to say whether a candidate rect is a facecam.

Pure numpy logistic regression, for the reason decision D-7 gives: the venv
already carries a fragile torch/transformers pin and adding scipy and sklearn
for a model this size is real dependency risk for no gain.

VALIDATED LEAVE-ONE-SOURCE-OUT, and that is not a detail. Candidates from one
stream share a layout, a streamer and a camera; scoring a model on rows held
out at random measures how well it recognises sources it has already seen. With
nine sources the honest question is "trained on eight, does it work on the
ninth", and the honest sample size is nine.

The bar is not 50%. It is:

  * the current detector, 6 of 9 sources correct;
  * `corner_proximity` alone, which separates these candidates 93% of the time
    with one threshold and is already computed.

A learned model that does not beat a single existing feature is not worth the
file it lives in, let alone the inference.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

from facecam_dataset import FEATURES                               # noqa: E402

DATA = _REPO / "data" / "facecam_candidates.json"


def load():
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    X = np.array([[float(r[f]) for f in FEATURES] for r in rows])
    y = np.array([int(r["label"]) for r in rows])
    g = np.array([r["source"] for r in rows])
    return X, y, g


def fit(X, y, *, steps=4000, lr=0.15, l2=0.05):
    """Logistic regression by gradient descent, standardised, with L2.

    The regularisation is not decoration at this size: nine features and 60-odd
    rows will fit anything asked of them, and an unregularised fit here scores
    beautifully in training and randomly on a held-out source.
    """
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = (X - mu) / sd
    Z = np.hstack([Z, np.ones((Z.shape[0], 1))])
    w = np.zeros(Z.shape[1])
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-Z @ w))
        grad = Z.T @ (p - y) / len(y)
        grad[:-1] += l2 * w[:-1]
        w -= lr * grad
    return w, mu, sd


def predict(w, mu, sd, X):
    Z = np.hstack([(X - mu) / sd, np.ones((X.shape[0], 1))])
    return 1.0 / (1.0 + np.exp(-Z @ w))


def loso(X, y, g):
    """Leave-one-SOURCE-out. Returns per-source accuracy."""
    out = {}
    for src in sorted(set(g)):
        test = g == src
        w, mu, sd = fit(X[~test], y[~test])
        pred = predict(w, mu, sd, X[test]) >= 0.5
        out[src] = float(np.mean(pred == y[test].astype(bool))), int(test.sum())
    return out


def corner_only(X, y, g):
    """The bar: one threshold on the feature that already exists."""
    idx = FEATURES.index("corner")
    best_t, best = 0.0, 0.0
    for t in np.linspace(0, 1, 101):
        acc = np.mean((X[:, idx] >= t) == y.astype(bool))
        if acc > best:
            best, best_t = acc, t
    out = {}
    for src in sorted(set(g)):
        m = g == src
        out[src] = float(np.mean((X[m, idx] >= best_t) == y[m].astype(bool))), int(m.sum())
    return out, best_t, best


if __name__ == "__main__":
    X, y, g = load()
    print(f"{len(y)} candidates, {y.sum()} facecam, {len(y) - y.sum()} phantom, "
          f"{len(set(g))} sources\n")

    base, thr, overall = corner_only(X, y, g)
    print(f"BASELINE — corner_proximity >= {thr:.2f} alone: {overall:.0%} of candidates")
    for src, (acc, n) in base.items():
        print(f"  {src:24s} {acc:5.0%}  ({n} candidates)")

    print("\nLEARNED — logistic over 9 features, leave-one-source-out")
    got = loso(X, y, g)
    for src, (acc, n) in got.items():
        mark = "  " if acc >= base[src][0] else "<-"
        print(f"  {src:24s} {acc:5.0%}  ({n} candidates) {mark}")

    lm = np.mean([a for a, _ in got.values()])
    bm = np.mean([a for a, _ in base.values()])
    print(f"\nmean over sources — learned {lm:.0%}, corner alone {bm:.0%}")
    print("the learned model is worth building only if that first number is "
          "clearly the larger one")

    w, mu, sd = fit(X, y)
    order = np.argsort(-np.abs(w[:-1]))
    print("\nweights on the full fit, largest first "
          "(direction only — this is not a held-out result):")
    for i in order:
        print(f"  {FEATURES[i]:14s} {w[i]:+.2f}")
