"""Resumable raw verdicts for the comparative judge.

One score run may ask the judge about several pools.  A provider call is the
expensive, nondeterministic part; applying its raw verdicts to candidates is
cheap and deterministic.  Rows are therefore keyed by the exact prompt the
judge saw, not by a candidate count or a round label that merely resembles
the question.

The state lives inside the common ``judge`` reasoning envelope.  Malformed
state is discarded as a whole: filtering a broken row could shift a verdict
onto a later round and turn unreadable evidence into a cache hit.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Sequence

from services.clipper import reasoning_cache


JUDGE_CACHE_VERSION = "judge_rounds_v1"
USABLE = "usable"
UNUSABLE = "unusable"
STATUSES = frozenset({USABLE, UNUSABLE})

NEW = "new"
HIT = "hit"
MALFORMED = "malformed"


@dataclass(frozen=True)
class Prepared:
    state: dict
    reused: bool
    reason: str


def _fresh() -> dict:
    return {"judge_cache_version": JUDGE_CACHE_VERSION, "rounds": []}


def _valid_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    round_index = row.get("round")
    if (not isinstance(round_index, int) or isinstance(round_index, bool)
            or round_index < 0):
        return False
    if not isinstance(row.get("prompt_fingerprint"), str):
        return False
    status = row.get("status")
    if status not in STATUSES:
        return False
    verdicts = row.get("verdicts")
    if not isinstance(verdicts, list):
        return False
    if status == USABLE and (not verdicts or not any(
            isinstance(verdict, dict) and verdict.get("id") is not None
            for verdict in verdicts)):
        return False
    if status == UNUSABLE and verdicts:
        return False
    return isinstance(row.get("provenance"), dict)


def prepare(cached: Any = None) -> Prepared:
    if cached is None:
        return Prepared(_fresh(), False, NEW)
    if (not isinstance(cached, dict)
            or cached.get("judge_cache_version") != JUDGE_CACHE_VERSION):
        return Prepared(_fresh(), False, MALFORMED)
    rows = cached.get("rounds")
    if not isinstance(rows, list) or any(not _valid_row(row) for row in rows):
        return Prepared(_fresh(), False, MALFORMED)
    indices = [row["round"] for row in rows]
    if len(indices) != len(set(indices)):
        return Prepared(_fresh(), False, MALFORMED)
    return Prepared(deepcopy(cached), True, HIT)


def reusable(state: dict, round_index: int, prompt: str, *,
             candidate_count: int | None = None) -> list | None:
    """Raw verdicts for this exact question, or ``None`` when it needs a call."""
    rows = state.get("rounds") if isinstance(state, dict) else None
    if not isinstance(rows, list):
        return None
    prompt_fp = reasoning_cache.fingerprint(prompt)
    matches = [row for row in rows
               if isinstance(row, dict) and row.get("round") == round_index]
    if len(matches) != 1:
        return None
    row = matches[0]
    if (not _valid_row(row) or row["status"] != USABLE
            or row["prompt_fingerprint"] != prompt_fp):
        return None
    if candidate_count is not None:
        applicable = False
        for verdict in row["verdicts"]:
            try:
                index = int(verdict.get("id"))
            except (AttributeError, TypeError, ValueError):
                continue
            if 0 <= index < candidate_count:
                applicable = True
                break
        if not applicable:
            return None
    return deepcopy(row["verdicts"])


def record(state: dict, round_index: int, prompt: str, *, usable: bool,
           verdicts: Sequence, provenance: dict) -> dict:
    """Return a new state with this round's attempted answer replaced."""
    prepared = prepare(state)
    if not prepared.reused:
        raise ValueError("judge cache state is malformed")
    out = prepared.state
    row = {
        "round": int(round_index),
        "prompt_fingerprint": reasoning_cache.fingerprint(prompt),
        "status": USABLE if usable else UNUSABLE,
        "verdicts": list(verdicts) if usable else [],
        "provenance": dict(provenance),
    }
    rows = [saved for saved in out["rounds"]
            if saved["round"] != round_index]
    rows.append(row)
    rows.sort(key=lambda saved: saved["round"])
    out["rounds"] = rows
    return out


def counts(state: dict) -> dict[str, int]:
    out = {USABLE: 0, UNUSABLE: 0}
    for row in state.get("rounds", []) if isinstance(state, dict) else []:
        status = row.get("status") if isinstance(row, dict) else None
        if status in out:
            out[status] += 1
    return out
