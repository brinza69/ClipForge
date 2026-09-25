"""B3: which selection run a blind review may compare, and why the others may not.

A rescore keeps exports and clips a person worked on WITH THEIR OLD
`selection_run_id` (`clipper_finalize._kept_clips`), so a project's board mixes
runs. `start_session` refuses that mix with 409; this module is what lets a
person pick ONE run instead, and it refuses to call a run complete unless the
run's own artefacts can prove it.

WHAT CAN PROVE A RUN'S ORIGINAL MEMBERS. Only the two artefacts `_write_traces`
writes after `_write_clips`, and only while they still belong to that run —
both are overwritten by the next rescore, so an older run cannot be proven from
anything on disk:

- `selection_trace.json`: `run_id`, and every candidate's span and
  `rank_position` — the LEGACY board, rank by rank (`totals.winners` = N) —
  and, since B3r, `shadow_selection.picks`: the SHADOW board, each pick's
  `shadow_rank`, `shadow_run_id` and window, as the build stamped them.
- `reasoning_run.json`: `run_id`, and the `board_v2` stage whose detail reads
  "M winners, ..." — the shadow board's size, cross-checked against the picks.

B3 proved the shadow board by COUNT + ranks 1..M + "every window is in the
pool", and Codex showed that is not proof: another candidate of the same pool,
given the same rank, passed. Membership is now an EXACT comparison, rank by
rank, of the recorded window with the row's, one row per rank. A trace written
before the picks were recorded stays readable and proves nothing
(`trace_has_no_shadow_selection`); it is never completed from the rows, which
are what it is checked against.

The row carries a window, not a candidate id, so a window two candidates of
the run share cannot say which one the row is: `trace_window_ambiguous`.

Unknown (NULL run) cohorts are never provable: no trace can name them.
DB-free on purpose: rows and artefacts in, verdict out.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

SELECTION_TRACE_VERSION = "selection_trace_v1"
SHADOW_SELECTION_VERSION = "shadow_selection_v1"
_SHADOW_DETAIL = re.compile(r"^(\d+) winners, ")


def _int(value: Any) -> int | None:
    # `True == 1`: a bool is not a rank (CLAUDE.md, the verdict gotcha).
    return value if type(value) is int else None


def _span(start: Any, end: Any) -> tuple[float, float] | None:
    try:
        return float(start), float(end)
    except (TypeError, ValueError):
        return None


def expected_members(run_id: str, trace: Any, run_record: Any) -> tuple[dict | None, str]:
    """What the run itself wrote down about its boards, or why it cannot say.

    Returns ({"legacy": {rank: span}, "shadow": {shadow_rank: span}}, "")
    or (None, reason). Anything malformed is a reason, never an empty board.
    """
    if not isinstance(trace, dict) or not isinstance(run_record, dict):
        return None, "trace_missing"
    if trace.get("run_id") != run_id or run_record.get("run_id") != run_id:
        return None, "trace_of_another_run"
    entries, totals = trace.get("entries"), trace.get("totals")
    if (trace.get("trace_version") != SELECTION_TRACE_VERSION
            or not isinstance(entries, list) or not isinstance(totals, dict)
            or _int(totals.get("winners")) is None):
        return None, "trace_unreadable"
    record = trace.get("shadow_selection")
    if not isinstance(record, dict) or record.get("version") != SHADOW_SELECTION_VERSION:
        return None, "trace_has_no_shadow_selection"
    legacy: dict[int, tuple[float, float]] = {}
    windows: Counter[tuple[float, float]] = Counter()
    for entry in entries:
        if not isinstance(entry, dict):
            return None, "trace_unreadable"
        if entry.get("eliminated_by"):
            continue
        span = _span(entry.get("start"), entry.get("end"))
        if span is None:
            return None, "trace_unreadable"
        windows[span] += 1
        rank = entry.get("rank_position")
        if rank in (None, 0):
            continue
        if _int(rank) is None or rank < 1 or rank in legacy:
            return None, "trace_unreadable"
        legacy[rank] = span
    if sorted(legacy) != list(range(1, totals["winners"] + 1)):
        return None, "trace_unreadable"

    picks = record.get("picks")
    if not isinstance(picks, list):
        return None, "trace_unreadable"
    shadow: dict[int, tuple[float, float]] = {}
    for pick in picks:
        if not isinstance(pick, dict) or pick.get("eliminated_by"):
            return None, "trace_unreadable"
        rank, span = _int(pick.get("shadow_rank")), _span(pick.get("start"), pick.get("end"))
        if (rank is None or rank < 1 or rank in shadow or span is None
                or pick.get("shadow_run_id") != run_id or not windows[span]):
            return None, "trace_unreadable"
        shadow[rank] = span
    # One window at two shadow ranks: the writer takes each pick from a distinct
    # entry, so the record contradicts itself (review F5). A window on BOTH
    # boards is fine — one candidate can be a legacy and a shadow winner.
    if (sorted(shadow) != list(range(1, len(shadow) + 1))
            or len(set(shadow.values())) != len(shadow)):
        return None, "trace_unreadable"

    stages = [s for s in (run_record.get("stages") or ())
              if isinstance(s, dict) and s.get("name") == "board_v2"]
    if not stages:
        count = 0  # not a shadow run: recorded picks would contradict it
    else:
        match = (_SHADOW_DETAIL.match(str(stages[-1].get("detail") or ""))
                 if len(stages) == 1 else None)
        if match is None:
            return None, "trace_unreadable"
        count = int(match.group(1))
    if count != len(shadow):
        return None, "trace_unreadable"
    if any(windows[span] > 1 for span in (*legacy.values(), *shadow.values())):
        return None, "trace_window_ambiguous"
    return {"legacy": legacy, "shadow": shadow}, ""


def _compare(rows: list[dict], key: str,
             expected: dict[int, tuple[float, float]]) -> tuple[bool, bool]:
    """(same ranks, one row each; and every row's window is the recorded one)."""
    observed: dict[Any, list] = {}
    for m in rows:
        observed.setdefault(m[key], []).append(_span(m.get("start_time"), m.get("end_time")))
    same_ranks = (sorted(observed) == sorted(expected)
                  and all(len(v) == 1 for v in observed.values()))
    return same_ranks, same_ranks and all(observed[r][0] == s for r, s in expected.items())


def evaluate(run_id: str | None, members: list[dict], foreign_shadow: int,
             trace: Any, run_record: Any, capture: Callable[[dict], dict],
             media_error: type[Exception]) -> tuple[dict, dict[str, dict]]:
    """One cohort's report, plus the media snapshots taken while checking it.

    `members`: the board rows (rank or shadow rank) whose `selection_run_id` is
    `run_id`. `foreign_shadow`: board rows of OTHER runs whose `shadow_run_id`
    names this one. Every check runs even after one fails, so the report lists
    everything that is missing rather than the first thing.
    """
    missing: list[str] = []
    legacy = [m for m in members if m.get("rank_position") is not None]
    shadow = [m for m in members if m.get("shadow_rank") is not None]
    if not legacy:
        missing.append("no_legacy_board")
    if not shadow:
        missing.append("no_shadow_board")
    if foreign_shadow or any(m.get("shadow_run_id") != run_id for m in shadow):
        missing.append("shadow_run_mismatch")

    membership: dict[str, Any] = {"status": "unprovable", "reason": None,
                                  "expected_legacy": None, "expected_shadow": None}
    if run_id is None:
        membership["reason"] = "unknown_run_has_no_trace"
    else:
        expected, reason = expected_members(run_id, trace, run_record)
        membership["reason"] = reason or None
        if expected is not None:
            membership.update(expected_legacy=len(expected["legacy"]),
                              expected_shadow=len(expected["shadow"]))
            same_ranks, legacy_windows = _compare(legacy, "rank_position", expected["legacy"])
            same_shadow, shadow_windows = _compare(shadow, "shadow_rank", expected["shadow"])
            if not same_ranks:
                missing.append("legacy_board_differs_from_trace")
            if not same_shadow:
                missing.append("shadow_board_differs_from_trace")
            # A row at a recorded rank whose window is not that pick's: trimmed,
            # or another candidate in its place — either way not what was chosen.
            if (same_ranks and not legacy_windows) or (same_shadow and not shadow_windows):
                missing.append("member_window_changed")
            membership["status"] = ("proven" if legacy_windows and shadow_windows
                                    else "differs")
    if membership["status"] != "proven":
        missing.append("membership_" + membership["status"])

    snapshots: dict[str, dict] = {}
    problems: dict[str, int] = {}
    for m in members:
        try:
            snapshots[m["clip_id"]] = capture(m)
        except media_error as exc:
            problems[str(exc)] = problems.get(str(exc), 0) + 1
    if problems:
        missing.append("media_invalid")

    report = {
        "selection_run_id": run_id,
        # Denominator first: every board row of this run, then how it splits.
        "members": len(members),
        "legacy_members": len(legacy),
        "shadow_members": len(shadow),
        "membership": membership,
        "media": {"checked": len(members), "valid": len(snapshots),
                  "problems": dict(sorted(problems.items()))},
        "eligible": not missing,
        "missing": missing,
    }
    return report, snapshots
