"""Stable identity for one model-proposed story anchor.

An anchor is the finding; its two to four candidate windows are alternative
edits of that finding.  A time bucket cannot prove that relationship: two
anchors can share a bucket, while boundary work can move two variants across a
bucket edge.  This module assigns the identity once, before variants exist.

The id is deterministic for the exact source evidence and semantic anchor. It
does not claim that two fresh nondeterministic model calls found the same thing
when their semantic answers differ. S7's cache is what preserves the exact
answer across an identical rerun.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from services.clipper import reasoning_cache
from services.clipper.segmentation import norm_token


ANCHOR_ID_VERSION = "anchor_id_v1"


def _number(value: Any, default: float = -1.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _text(value: Any) -> str:
    return " ".join(token for token in
                    (norm_token(word) for word in str(value or "").split())
                    if token)


def _claim(item: Any) -> dict:
    if not isinstance(item, dict):
        return {"invalid": type(item).__name__}
    return {
        "t": round(_number(item.get("t")), 3),
        "fact": _text(item.get("fact")),
        "quote": _text(item.get("quote")),
        "atom_ids": sorted(str(i) for i in (item.get("atom_ids") or [])
                           if i is not None),
    }


def source_namespace(items: Sequence[dict], duration: float) -> str:
    """Identity of the exact source evidence the anchor model could read."""
    return reasoning_cache.fingerprint({
        "anchor_id_version": ANCHOR_ID_VERSION,
        "duration": round(_number(duration, 0.0), 3),
        "items": list(items),
    })


def identity(anchor: dict, namespace: str) -> str:
    """A source-scoped id from semantic fields, excluding confidence/provenance."""
    if not isinstance(anchor, dict):
        raise ValueError("anchor must be a record")
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("anchor namespace is missing")
    callback = anchor.get("callback_to")
    callback_t = (callback.get("t") if isinstance(callback, dict) else None)
    projection = {
        "version": ANCHOR_ID_VERSION,
        "source": namespace,
        "payoff_t": round(_number(anchor.get("payoff_t")), 3),
        "payoff_quote": _text(anchor.get("payoff_quote")),
        "why": _text(anchor.get("why")),
        "archetypes": sorted(str(kind) for kind in
                             (anchor.get("archetypes") or [])),
        "hook_t": round(_number(anchor.get("hook_t")), 3),
        "callback_t": round(_number(callback_t), 3),
        "required_context": sorted(
            (_claim(item) for item in (anchor.get("required_context") or [])),
            key=reasoning_cache.fingerprint),
    }
    return f"a1-{reasoning_cache.fingerprint(projection)[:16]}"


def assign(anchors: Sequence[dict], namespace: str) -> list[dict]:
    """Copy anchors and stamp their deterministic identity; never filter rows."""
    out = []
    for anchor in anchors or ():
        if not isinstance(anchor, dict):
            raise ValueError("anchor list contains a non-record")
        out.append({**anchor, "anchor_id": identity(anchor, namespace)})
    return out


def of_candidate(candidate: Any) -> str | None:
    if not isinstance(candidate, dict):
        return None
    story = candidate.get("story")
    value = candidate.get("anchor_id")
    if value is None and isinstance(story, dict):
        value = story.get("anchor_id")
    return value if isinstance(value, str) and value else None


def discovery_key(group: dict, quantum_s: float) -> tuple[str, Any] | None:
    """Canonical anchor when available; the old payoff bucket for legacy data."""
    anchor_id = of_candidate(group)
    if anchor_id is not None:
        return "anchor", anchor_id
    payoff = _number(group.get("payoff_t"))
    if payoff < 0 or quantum_s <= 0:
        return None
    return "payoff", int(payoff // quantum_s)


def discovery_keys(groups: Sequence[dict], quantum_s: float) -> tuple[set, set]:
    """All anchors represented by groups, even when dedupe merged several."""
    found, grounded = set(), set()
    for group in groups or ():
        ids = group.get("anchor_ids") if isinstance(group, dict) else None
        if isinstance(ids, list) and ids:
            found.update(("anchor", value) for value in ids)
            grounded_ids = group.get("grounded_anchor_ids") or []
            grounded.update(("anchor", value) for value in grounded_ids)
            continue
        key = discovery_key(group, quantum_s)
        if key is not None:
            found.add(key)
            if group.get("grounded"):
                grounded.add(key)
    return found, grounded
