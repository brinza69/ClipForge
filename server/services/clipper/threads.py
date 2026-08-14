"""
ClipForge — AI Stream Clipper: narrative threads across a stream (§3).

Nothing in the pipeline knows that five candidates belong to one forty-minute
arc. Dedupe collapses two cuts of one MOMENT and diversity spreads clips along
the clock and across archetypes, but a stream that spends an hour on one boss
can still hand back a board that is entirely that boss. A thread is the missing
axis.

CHEAP, LIKE ATOMS. Threads are found by lexical chaining over the atoms: an
atom joins a thread when it shares enough rare vocabulary with what that thread
has been about recently, and threads close after a gap. No model, because a
12-hour stream is thousands of atoms and the cost rule (§29) rules out a call
per unit. What a thread is FOR here is grouping, not prose, and grouping does
not need a language model.

WHAT ABOUT THE EVENT GRAPH (§5). The relations worth having already exist as
targeted mechanisms and are already read: SETUP_FOR and RESOLVES are what
`promises` and `callback_to` do, SAME_STORY is what `dedupe.same_story` does by
payoff, CALLBACK_TO is on the anchor. A general edge store on top would restate
them in a second vocabulary that nothing queries — and an unread structure is
the failure mode this codebase has hit three times already. `edges()` below
derives the relations that a consumer actually asks for, and no others.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.clipper.atoms import _TOO_COMMON
from services.clipper.candidate_terms import _num
from services.clipper.segmentation import norm_token

logger = logging.getLogger("clipforge.clipper.threads")

THREADS_VERSION = "threads_v1"

# A thread that has not been touched for this long is over. Long enough that a
# fight interrupted by an inventory trip is still one thread; short enough that
# the whole stream does not collapse into a single arc.
THREAD_GAP_S = 300.0
# How many rare words an atom must share with a thread to belong to it. One is
# coincidence on a stream that says "diamond" all night.
MIN_SHARED = 2
# The thread's vocabulary is what it has been about RECENTLY, not everything it
# ever said — otherwise a long thread accumulates half the dictionary and
# swallows the rest of the stream.
VOCAB_MEMORY = 6
# A single atom is not a narrative arc, it is an atom. Measured on the real
# stream, chaining produced 43 "threads" of which 30 were singletons — as a
# diversity axis that degenerates to one bucket per moment and adds nothing
# over the timeline. The arcs it finds that ARE real all have several atoms:
# the Minecraft-admins bit (18-74s), the trial chamber (240-379s), the
# fortress (612-706s). Below this a moment simply belongs to no thread, which
# is the honest answer rather than a thread of one.
MIN_THREAD_ATOMS = 2


def _content(text: str) -> set[str]:
    return {t for t in (norm_token(w) for w in str(text or "").split())
            if t and len(t) > 3 and t not in _TOO_COMMON}


def build(atoms: Sequence[dict]) -> list[dict]:
    """Every narrative thread in the stream, in order of first appearance.

    Pure and cheap: one pass over the atoms, set intersections only.
    """
    threads: list[dict] = []
    open_threads: list[dict] = []

    for atom in atoms or []:
        start = _num(atom.get("start"))
        tokens = _content(atom.get("text", ""))

        open_threads = [t for t in open_threads
                        if start - t["last_update"] <= THREAD_GAP_S]
        best, best_hits = None, 0
        for thread in open_threads:
            recent: set[str] = set()
            for chunk in thread["_recent"][-VOCAB_MEMORY:]:
                recent |= chunk
            hits = len(tokens & recent)
            if hits > best_hits:
                best, best_hits = thread, hits

        if best is not None and best_hits >= MIN_SHARED:
            best["end"] = _num(atom.get("end"))
            best["last_update"] = start
            best["atoms"].append(int(atom.get("i", len(best["atoms"]))))
            best["_recent"].append(tokens)
            best["_all"] |= tokens
            continue

        thread = {
            "id": f"thread_{len(threads):03d}",
            "start": start,
            "end": _num(atom.get("end")),
            "last_update": start,
            "atoms": [int(atom.get("i", 0))],
            "_recent": [tokens],
            "_all": set(tokens),
        }
        threads.append(thread)
        open_threads.append(thread)

    # How many threads each word turns up in — a word in half of them names
    # none of them. Taking sorted(vocab)[:12] instead gave every arc the same
    # alphabetical opening: "able, activate, activated, actual, actually,
    # ain't, alive", which identifies nothing.
    spread: dict[str, int] = {}
    for thread in threads:
        for token in thread["_all"]:
            spread[token] = spread.get(token, 0) + 1

    out: list[dict] = []
    for thread in threads:
        if len(thread["atoms"]) < MIN_THREAD_ATOMS:
            continue
        vocab = sorted(thread["_all"], key=lambda t: (spread.get(t, 1), t))
        out.append({
            "id": thread["id"],
            "start": round(thread["start"], 3),
            "end": round(thread["end"], 3),
            "atoms": thread["atoms"],
            # The words that ran through it — enough to recognise the thread
            # in a debug panel without asking a model to name it.
            "keywords": vocab[:12],
            "size": len(thread["atoms"]),
            "version": THREADS_VERSION,
        })
    logger.info("threads: %d over %d atoms (%s)", len(out), len(atoms or []),
                THREADS_VERSION)
    return out


def thread_at(threads: Sequence[dict], start: float, end: float) -> str | None:
    """The thread a window sits in — the one it overlaps most.

    A zero-width query is a POINT, not an empty window: `edges` asks where a
    payoff timestamp sits, and comparing overlaps would answer "nowhere" for
    every thread because every overlap is 0.
    """
    if end <= start:
        for thread in threads or []:
            if _num(thread.get("start")) <= start <= _num(thread.get("end")):
                return thread["id"]
        return None

    best, best_overlap = None, 0.0
    for thread in threads or []:
        overlap = min(end, _num(thread.get("end"))) - max(start, _num(thread.get("start")))
        if overlap > best_overlap:
            best, best_overlap = thread, overlap
    return best["id"] if best is not None else None


def edges(threads: Sequence[dict], promises: Sequence[dict],
          anchors: Sequence[dict]) -> list[dict]:
    """The relations something actually reads, derived from what exists.

    Deliberately not a general graph. SETUP_FOR comes from a promise an anchor
    named as its callback; SAME_STORY comes from two anchors landing in one
    thread. Every other relation the spec lists (CAUSES, ESCALATES,
    CONTRADICTS, …) would need either a model per pair or a guess, and nothing
    downstream would query them — so they are not invented here.
    """
    out: list[dict] = []
    for anchor in anchors or []:
        callback = anchor.get("callback_to")
        if isinstance(callback, dict):
            out.append({"kind": "SETUP_FOR",
                        "from_t": _num(callback.get("t")),
                        "to_t": _num(anchor.get("payoff_t")),
                        "why": str(callback.get("text") or "")[:120]})

    by_thread: dict[str, list[float]] = {}
    for anchor in anchors or []:
        payoff = _num(anchor.get("payoff_t"))
        tid = thread_at(threads, payoff, payoff)
        if tid:
            by_thread.setdefault(tid, []).append(payoff)
    for tid, times in by_thread.items():
        times.sort()
        for earlier, later in zip(times, times[1:]):
            out.append({"kind": "SAME_STORY", "from_t": earlier,
                        "to_t": later, "why": tid})
    return out
