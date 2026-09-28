"""Resumable state for model passes that read a source chunk by chunk.

The whole-artifact envelope answers whether a checkpoint belongs to this run.
This module answers the next question: which individual requests inside that
checkpoint produced a usable answer?  A failed chunk stays named and retryable;
successful neighbours are never paid for again merely because one provider
returned prose or timed out.

The state is ordinary strict JSON and lives as the ``data`` of the artifact's
common reasoning envelope.  It never stores prompt text or provider responses,
only their fingerprints and the normalised items that downstream code reads.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence

from services.clipper import reasoning_cache


CHUNK_SET_VERSION = "reasoning_chunks_v1"
PENDING = "pending"
USABLE = "usable"
UNUSABLE = "unusable"
STATUSES = frozenset({PENDING, USABLE, UNUSABLE})

NEW = "new"
HIT = "hit"
MALFORMED = "malformed"
PLAN_CHANGED = "plan_changed"


@dataclass(frozen=True)
class Prepared:
    state: dict
    reused: bool
    reason: str


def _chunk_projection(chunk: dict) -> dict:
    """The exact slice read by one request, without persisting its text."""
    return {
        "index": int(chunk["index"]),
        "t_start": float(chunk["t_start"]),
        "t_end": float(chunk["t_end"]),
        "chars": int(chunk["chars"]),
        "lines_fingerprint": reasoning_cache.fingerprint(chunk["lines"]),
    }


def _plan(kind: str, chunks: Sequence[dict]) -> tuple[list[dict], str]:
    rows = [_chunk_projection(chunk) for chunk in chunks]
    indices = [row["index"] for row in rows]
    if len(indices) != len(set(indices)):
        raise ValueError("chunk plan contains duplicate indices")
    return rows, reasoning_cache.fingerprint({"kind": kind, "chunks": rows})


def _fresh(kind: str, rows: Sequence[dict], plan_fp: str) -> dict:
    return {
        "chunk_set_version": CHUNK_SET_VERSION,
        "kind": str(kind),
        "plan_fingerprint": plan_fp,
        "chunks": [
            {
                **dict(row),
                "status": PENDING,
                "items": [],
                "provenance": None,
            }
            for row in rows
        ],
    }


def _valid_row(row: Any, expected: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if any(row.get(key) != value for key, value in expected.items()):
        return False
    if row.get("status") not in STATUSES or not isinstance(row.get("items"), list):
        return False
    provenance = row.get("provenance")
    return provenance is None or isinstance(provenance, dict)


def prepare(kind: str, chunks: Sequence[dict], cached: Any = None) -> Prepared:
    """Return a validated state for this exact chunk plan.

    A malformed nested state is not partially salvaged.  Otherwise one broken
    row could shift cached answers onto later chunks — the same disappearing-
    denominator bug, this time in time order.
    """
    rows, plan_fp = _plan(kind, chunks)
    fresh = _fresh(kind, rows, plan_fp)
    if cached is None:
        return Prepared(fresh, False, NEW)
    if not isinstance(cached, dict):
        return Prepared(fresh, False, MALFORMED)
    if cached.get("chunk_set_version") != CHUNK_SET_VERSION:
        return Prepared(fresh, False, MALFORMED)
    if cached.get("kind") != kind or cached.get("plan_fingerprint") != plan_fp:
        return Prepared(fresh, False, PLAN_CHANGED)
    saved = cached.get("chunks")
    if (not isinstance(saved, list) or len(saved) != len(rows)
            or any(not _valid_row(row, expected)
                   for row, expected in zip(saved, rows))):
        return Prepared(fresh, False, MALFORMED)
    return Prepared(deepcopy(cached), True, HIT)


def reusable(state: dict, chunk: dict) -> list | None:
    """Normalised items for a usable chunk; ``None`` means it needs a call."""
    index = int(chunk["index"])
    rows = state.get("chunks") if isinstance(state, dict) else None
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows
               if isinstance(row, dict) and row.get("index") == index]
    if len(matches) != 1:
        return None
    row = matches[0]
    expected = _chunk_projection(chunk)
    if not _valid_row(row, expected) or row.get("status") != USABLE:
        return None
    return list(row["items"])


def record(state: dict, chunk: dict, *, usable: bool, items: Sequence,
           provenance: dict | None) -> dict:
    """Return a new state with one attempted chunk replaced atomically."""
    out = deepcopy(state)
    index = int(chunk["index"])
    rows = out.get("chunks")
    if not isinstance(rows, list):
        raise ValueError(f"chunk index outside plan: {index}")
    positions = [position for position, row in enumerate(rows)
                 if isinstance(row, dict) and row.get("index") == index]
    if len(positions) != 1:
        raise ValueError(f"chunk index not unique in plan: {index}")
    position = positions[0]
    expected = _chunk_projection(chunk)
    if not _valid_row(rows[position], expected):
        raise ValueError(f"chunk does not match prepared plan: {index}")
    rows[position] = {
        **expected,
        "status": USABLE if usable else UNUSABLE,
        "items": list(items) if usable else [],
        "provenance": dict(provenance or {}),
    }
    return out


def items(state: dict) -> list:
    """All usable rows, in chunk order. Unusable/pending rows stay visible."""
    out = []
    for row in state.get("chunks", []) if isinstance(state, dict) else []:
        if isinstance(row, dict) and row.get("status") == USABLE:
            out.extend(row.get("items") or [])
    return out


def counts(state: dict) -> dict[str, int]:
    out = {PENDING: 0, USABLE: 0, UNUSABLE: 0}
    for row in state.get("chunks", []) if isinstance(state, dict) else []:
        status = row.get("status") if isinstance(row, dict) else None
        if status in out:
            out[status] += 1
    return out


def complete(state: dict) -> bool:
    tally = counts(state)
    return tally[USABLE] == sum(tally.values())
