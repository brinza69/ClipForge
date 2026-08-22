"""
ClipForge — AI Stream Clipper: what the reasoning actually did, on disk.

Two artefacts, written by the scoring stage:

  reasoning_run.json   — how the run was configured and what the models did:
                         versions, the chunk plan, every provider attempt and
                         fallback, anchor counts, errors, and a snapshot of the
                         settings that were actually in force.
  selection_trace.json — why each candidate ended where it did: its scores,
                         whether the judge saw it, why it was dropped, and which
                         ones won.

WHY THIS COMES FIRST. Every later batch claims to improve the ranking. Without
these, "improved" is an assertion — the artefacts the audit had to work from
recorded the RESULT and not the reasoning, so establishing what happened cost a
day of reconstruction and still left holes. The clearest one: two story runs on
this rig were quoted as evidence, and it later turned out their settings could
not have come through the API at all (their stored dicts hold 4 and 5 keys,
where `_normalise_settings` always returns the full set). Nothing in the
artefacts said so. `settings_snapshot` and `launched_by` exist because of that.

WHAT IS PURE HERE. Everything. No DB, no config, no I/O — the caller passes what
it knows and writes the result through storage.py. That is what lets the tests
build a whole run out of dicts.

WHAT THIS CANNOT DO YET. A stable `moment_id` arrives with the grouping in a
later batch; until then a variant is identified by its own span, which is stable
within a run but says nothing about two runs agreeing on what "the same moment"
is. And a rerun is NOT expected to be byte-identical: the models are
nondeterministic and nothing here caches them. What must match is the STRUCTURE
and the input fingerprints — a difference in what a model said is recorded as a
difference, not treated as a failure.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

RUN_TRACE_VERSION = "reasoning_run_v1"
SELECTION_TRACE_VERSION = "selection_trace_v1"

# How a candidate ended up where it did. A closed list so they can be counted
# rather than read one at a time.
SELECTED = "selected"
NOT_SELECTED_IN_JUDGED_POOL = "not_selected_in_judged_pool"
NOT_EVALUATED = "not_evaluated"
JUDGE_STATUSES: tuple[str, ...] = (SELECTED, NOT_SELECTED_IN_JUDGED_POOL, NOT_EVALUATED)

# Why a candidate is not on the board. `None` means it survived.
ELIMINATED_ALREADY_EXPORTED = "already_exported"
ELIMINATED_DEDUPED = "deduped"


def fingerprint(payload: Any) -> str:
    """A short, stable digest of any JSON-able value.

    `sort_keys` is what makes it stable: two dicts that differ only in insertion
    order are the same input, and a fingerprint that disagreed would report a
    change on every rerun and so report nothing at all.
    """
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class RunTrace:
    """Accumulates one scoring run, then hands back a dict to persist.

    A plain accumulator rather than anything clever: the run is a long function
    with several optional stages, and the alternative — returning trace data up
    through every one of them — would put plumbing in code whose subject is
    something else.

    Never raises. A trace that could fail the run it is describing would be
    worse than no trace: the first thing anyone would do is turn it off.
    """

    def __init__(self, project_id: str, *, mode: str,
                 settings_snapshot: dict | None = None,
                 launched_by: str = "worker") -> None:
        self.project_id = str(project_id)
        # Stamped on BOTH artefacts. They are written independently so one
        # failure cannot lose the other, and the price of that is that a reader
        # can otherwise find a `reasoning_run` from today next to a
        # `selection_trace` from last week and compare them as if they were one
        # run. The id is what makes the mismatch detectable.
        self.run_id = uuid.uuid4().hex[:12]
        self.mode = str(mode)
        # WHAT WAS ACTUALLY IN FORCE, not what the defaults say. See the module
        # docstring for the run this would have caught.
        self.settings_snapshot = dict(settings_snapshot or {})
        self.launched_by = str(launched_by)
        self.versions: dict[str, Any] = {}
        self.chunks: list[dict] = []
        self.attempts: list[dict] = []
        self.counts: dict[str, int] = {}
        self.errors: list[dict] = []
        self.stages: list[dict] = []
        self.results: list[dict] = []

    # ── recording ────────────────────────────────────────────────────────────

    def note_versions(self, **versions: Any) -> None:
        self.versions.update({k: v for k, v in versions.items() if v is not None})

    def note_chunk(self, kind: str, index: int, *, chars: int,
                   t_start: float | None = None, t_end: float | None = None,
                   produced: int = 0) -> None:
        """One slice of transcript handed to a model.

        `t_start`/`t_end` are optional because the current chunker splits on
        CHARACTERS and does not know where it landed in time. Recording the gap
        is the point: a later batch replaces that, and this is what will show
        whether it worked.
        """
        self.chunks.append({
            "kind": str(kind), "index": int(index), "chars": int(chars),
            "t_start": t_start, "t_end": t_end, "produced": int(produced),
        })

    def note_attempt(self, stage: str, engine: str, *, ok: bool,
                     error: str | None = None, request: str = "") -> None:
        """One call to one provider, inside one REQUEST.

        Both outcomes are recorded, because a fallback that worked is
        indistinguishable from a first-choice success unless the failure before
        it was written down.

        `request` identifies which call this attempt belongs to, and it is not
        optional in practice: a stage makes one call per chunk, so without it
        chunk 0 failing over to a second engine and chunk 5 succeeding on the
        first are the same three rows. The first version grouped by stage alone
        and reported a fallback that never happened — it paired a failure in one
        chunk with a success in another.
        """
        self.attempts.append({
            "stage": str(stage), "request": str(request or stage),
            "engine": str(engine), "ok": bool(ok),
            "error": (str(error)[:300] if error else None),
        })

    def note_result(self, stage: str, request: str, *, parsed: bool) -> None:
        """Whether a call that got an ANSWER got a usable one.

        `note_attempt(ok=True)` means the transport worked: a provider returned
        a non-empty string. It says nothing about whether that string was the
        JSON we asked for, and models return prose, half-fenced blocks and
        apologies often enough that the difference matters. Without this, a run
        where every provider answered and every answer was unparseable reported
        itself `complete` — the same shape of lie as the exhaustion bug, one
        layer further in.
        """
        self.results.append({"stage": str(stage), "request": str(request),
                             "parsed": bool(parsed)})

    def note_count(self, name: str, value: int) -> None:
        try:
            self.counts[str(name)] = int(value)
        except (TypeError, ValueError):
            pass

    def note_stage(self, name: str, status: str, detail: str = "") -> None:
        self.stages.append({"name": str(name), "status": str(status),
                            "detail": str(detail)[:300]})

    def note_error(self, stage: str, error: BaseException | str) -> None:
        self.errors.append({"stage": str(stage), "error": str(error)[:500]})

    # ── reading back ─────────────────────────────────────────────────────────

    def _requests(self) -> list[dict]:
        """Attempts regrouped into the calls they belong to, in order."""
        order: list[str] = []
        by_request: dict[str, list[dict]] = {}
        for a in self.attempts:
            key = f"{a['stage']}::{a.get('request') or a['stage']}"
            if key not in by_request:
                by_request[key] = []
                order.append(key)
            by_request[key].append(a)
        return [{"stage": by_request[k][0]["stage"],
                 "request": by_request[k][0].get("request"),
                 "attempts": by_request[k]} for k in order]

    @property
    def fallbacks(self) -> list[dict]:
        """Calls that failed on one provider and were answered by a later one.

        Scoped to a single request. Across requests this is meaningless: two
        chunks are two independent calls, and a failure in one says nothing
        about the engine that answered the other.
        """
        out = []
        for req in self._requests():
            failed = [a["engine"] for a in req["attempts"] if not a["ok"]]
            answered = next((a for a in req["attempts"] if a["ok"]), None)
            if failed and answered is not None:
                out.append({"stage": req["stage"], "request": req["request"],
                            "after": failed, "used": answered["engine"]})
        return out

    @property
    def exhausted(self) -> list[dict]:
        """Calls where every provider failed and nothing answered.

        The state the first version could not see at all. `_ask` swallows its
        own failures by design — a clipper run must not die because Ollama is
        down — so no exception reaches the worker, `errors` stays empty, and a
        run in which NOTHING answered reported itself as `complete`.
        """
        return [{"stage": r["stage"], "request": r["request"],
                 "tried": [a["engine"] for a in r["attempts"]]}
                for r in self._requests()
                if r["attempts"] and not any(a["ok"] for a in r["attempts"])]

    @property
    def unusable(self) -> list[dict]:
        """Calls that were answered and whose answer could not be parsed."""
        return [r for r in self.results if not r["parsed"]]

    def outcome(self) -> str:
        """`complete`, `partial`, `fallback` or `failed_non_blocking`.

        Never `failed`: a reasoning run that could not reach a model still
        produced a board, by the legacy path. The distinction the UI needs is
        between "this is what the engine chose" and "the engine never ran".
        """
        broken = self.errors or self.exhausted or self.unusable
        produced = bool(self.counts.get("anchors") or self.counts.get("nominated"))
        if broken:
            return "partial" if produced else "failed_non_blocking"
        if self.fallbacks:
            return "fallback"
        return "complete"

    def as_dict(self) -> dict:
        return {
            "trace_version": RUN_TRACE_VERSION,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "reasoning_mode": self.mode,
            "launched_by": self.launched_by,
            "outcome": self.outcome(),
            "versions": dict(self.versions),
            "settings_snapshot": dict(self.settings_snapshot),
            "settings_fingerprint": fingerprint(self.settings_snapshot),
            "input_fingerprint": fingerprint(
                {"mode": self.mode, "versions": self.versions,
                 "settings": self.settings_snapshot}),
            "chunks": list(self.chunks),
            # None, not the digest of an empty list. A run that reused cached
            # anchors never chunks anything, and the empty-list digest is the
            # SAME on every project — so comparing two runs showed a matching
            # fingerprint that measured nothing at all. Caught by the first
            # rerun: two unrelated sources both produced 4f53cda18c2baa0c.
            "chunk_plan_fingerprint": (
                fingerprint([{k: c[k] for k in ("kind", "index", "chars")}
                             for c in self.chunks])
                if self.chunks else None),
            "attempts": list(self.attempts),
            "fallbacks": self.fallbacks,
            "exhausted": self.exhausted,
            "results": list(self.results),
            "unusable": self.unusable,
            "counts": dict(self.counts),
            "stages": list(self.stages),
            "errors": list(self.errors),
        }


# ── selection trace ──────────────────────────────────────────────────────────

def _variant_id(cand: dict) -> str:
    """Identify one candidate across a rerun.

    Its span, not its list position: position moves whenever anything upstream
    changes, which would make every rerun look like a total reshuffle. This is
    NOT the stable moment id the grouping batch introduces — two variants of one
    moment get two ids here, and nothing knows they belong together yet.

    Two candidates CAN share a rounded span — a heuristic window and a story
    variant that refined onto it. They are separated by a digest of their text
    rather than by their position in the list, because an ordinal suffix means
    "#2" swaps between two runs that merely enumerated them the other way round,
    and the whole point of this id is to survive a rerun.
    """
    start = round(float(cand.get("start") or 0.0), 2)
    end = round(float(cand.get("end") or 0.0), 2)
    return f"{start:.2f}-{end:.2f}"


def _disambiguate(cand: dict) -> str:
    """A content digest for two candidates whose rounded spans collide.

    Includes the UNROUNDED bounds: the rounding to 2dp is what made them
    collide in the first place, so a 0.00 and a 0.001 start are the same id and
    a different digest. Everything in here is a property of the candidate, not
    of its position, which is the whole point.
    """
    text = " ".join(str(cand.get("text") or "").split())[:400]
    return fingerprint({
        "start": cand.get("start"), "end": cand.get("end"), "text": text,
        "variant": cand.get("variant"),
        "reasons": sorted(str(r) for r in (cand.get("reasons") or [])),
    })[:6]


def _judge_status(cand: dict) -> str:
    """Whether the judge ranked this one.

    Prefers the status the run already stamped (`selection.mark`), so the trace
    and the board can never disagree about what happened. Falls back to
    deriving it, which is what a legacy artifact needs.

    `llm_score` cannot answer it. `apply_ranking` gives the field's last place
    `100 * (1 - position/span)` = 0.0 and then subtracts a fixed penalty per
    reject reason, clamped at zero — so a candidate the judge DID rank, and
    criticised, scores exactly what one it never looked at scores. Only
    `llm_rank` is set per ranked candidate and left absent otherwise.
    """
    stamped = cand.get("judge_status")
    if stamped in JUDGE_STATUSES:
        return str(stamped)
    if cand.get("llm_rank") is not None:
        return SELECTED
    if cand.get("llm_score") is not None:
        return NOT_SELECTED_IN_JUDGED_POOL
    return NOT_EVALUATED


def build_selection_trace(candidates: list[dict], *, mode: str,
                          judged_count: int = 0,
                          pool_rounds: int = 0,
                          run_id: str = "",
                          eliminated: dict[int, str] | None = None) -> dict:
    """Every candidate, why it scored what it did, and where it ended.

    Records the candidates the judge NEVER SAW as explicitly as the ones it
    ranked. That asymmetry is the thing worth measuring: on the four-hour source
    in the audit, 12 of the board's top 20 had never been judged at all, and no
    artefact said so — the number had to be recomputed by hand months later.

    `candidates` must be the FULL post-judge field, not the deduplicated one.
    Built from the survivors, this artefact answered "how did the winners rank"
    and could not answer "why is this moment missing" — which is the question it
    exists for. `eliminated` maps a candidate's index in that field to the stage
    that dropped it.
    """
    eliminated = eliminated or {}
    entries: list[dict] = []
    # Two candidates CAN land on the same rounded span — a heuristic window and
    # a story variant that refined onto it, for instance — and a colliding id
    # would make the structure fingerprint agree between two runs that do not.
    # Disambiguated by order of appearance, which is stable within a run.
    # Two passes. Suffixing only the SECOND arrival means the two ids swap when
    # the list is enumerated the other way round, which is exactly what this id
    # is supposed to survive — so a base that collides at all disambiguates
    # every one of its members, and neither depends on who came first.
    bases = [_variant_id(c) for c in (candidates or [])]
    collides = {b for b in bases if bases.count(b) > 1}
    for index, cand in enumerate(candidates or []):
        story = cand.get("story") if isinstance(cand.get("story"), dict) else {}
        base = bases[index]
        entries.append({
            "variant_id": (f"{base}+{_disambiguate(cand)}"
                           if base in collides else base),
            "_index": index,
            "eliminated_by": eliminated.get(index),
            "start": cand.get("start"),
            "end": cand.get("end"),
            "judge_status": _judge_status(cand),
            "scores": {
                # One field per scale, never merged: comparing a rank-derived
                # score with a heuristic one is the defect the later batches
                # exist to remove, and it cannot be seen unless both are kept.
                "overall": cand.get("overall"),
                "heuristic": cand.get("heuristic_score"),
                "learned": cand.get("learned_score"),
                "judge": cand.get("judge_score"),
                "selection": cand.get("selection_score"),
                "eligibility": cand.get("eligibility"),
                "llm_score": cand.get("llm_score"),
                "llm_rank": cand.get("llm_rank"),
                "ranker_version": cand.get("ranker_version"),
            },
            "reject_reasons": list(
                (cand.get("llm_verdict") or {}).get("reject_reasons") or []),
            "is_story": bool(story),
            "archetypes": list(story.get("archetypes") or []),
            "thread_id": story.get("thread_id"),
            "dedupe_group": cand.get("dedupe_group"),
            "is_alternative": bool(cand.get("is_alternative")),
            "rank_position": cand.get("rank_position"),
        })
    # Last resort. Two candidates identical in span, text, variant and reasons
    # are genuine duplicates, and nothing about their content can separate them
    # — so they fall back to order, which is at least stable within a run.
    used: dict[str, int] = {}
    for entry in entries:
        vid = entry["variant_id"]
        used[vid] = used.get(vid, 0) + 1
        if used[vid] > 1:
            entry["variant_id"] = f"{vid}~{used[vid]}"
        entry.pop("_index", None)

    entries.sort(key=lambda e: (e["start"] if e["start"] is not None else 0.0,
                                e["variant_id"]))

    winners = [e for e in entries if e["rank_position"]]
    story_entries = [e for e in entries if e["is_story"]]
    return {
        "trace_version": SELECTION_TRACE_VERSION,
        "run_id": str(run_id),
        "reasoning_mode": str(mode),
        "totals": {
            "candidates": len(entries),
            "judged": sum(1 for e in entries
                          if e["judge_status"] != NOT_EVALUATED) or int(judged_count),
            "not_evaluated": sum(1 for e in entries
                                 if e["judge_status"] == NOT_EVALUATED),
            "winners": len(winners),
            "story_candidates": len(story_entries),
            # The number the audit had to reconstruct by hand.
            "story_judged": sum(1 for e in story_entries
                                if e["judge_status"] != NOT_EVALUATED),
            "pool_rounds": int(pool_rounds),
            "eliminated": sum(1 for e in entries if e["eliminated_by"]),
        },
        "structure_fingerprint": fingerprint(
            [e["variant_id"] for e in entries]),
        "entries": entries,
    }
