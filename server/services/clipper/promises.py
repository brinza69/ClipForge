"""
ClipForge — AI Stream Clipper: promises made early, paid off later.

Anchors are found chunk by chunk with no memory across them, so the one kind
of clip the payoff-first design is best suited to find is the one it cannot
see: "he predicted he would choke" at hour two and "he chokes exactly as
predicted" at hour three are, to a per-chunk pass, two unrelated events.

This is the smallest thing that fixes that. One cheap sweep over the whole
transcript collects statements that CREATE AN EXPECTATION — a prediction, a
promise, a bet, a stated goal. Anchor detection in a later chunk is then handed
the ones still open, so it can say "this is the payoff to that".

WHY NOT A FULL EVENT GRAPH. The upgrade spec proposes event atoms and a graph
to carry this (§1, §5). Those are the right substrate for threads and
retrieval, but the user-visible win here is callbacks alone, and callbacks need
only the setups and a link. Building the substrate first would spend the whole
budget before anything detected a single callback.

WHAT A CALLBACK MEANS FOR THE CUT, which is the part worth getting right: the
setup is an hour away, so it CANNOT be in the window. A callback is therefore
not required context to include — it is context the clip OWES and cannot pay
from inside its own boundaries, unless the payoff line happens to restate it.
`callback_debt` is that distinction, and it is why a callback both raises the
interest of a moment and costs it something.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.clipper.candidate_terms import _clamp01, _num
from services.clipper.segmentation import norm_token

logger = logging.getLogger("clipforge.clipper.promises")

PROMISE_PROMPT_VERSION = "promises_v2"

KINDS: tuple[str, ...] = ("prediction", "promise", "challenge", "goal", "bet")
# Kinds that are not falsifiable by themselves and are only a setup when
# something is riding on them. See normalise_promise for the measurement.
_STAKE_REQUIRED: frozenset[str] = frozenset({"goal"})

# A setup stops being live eventually — a prediction about "this round" does
# not pay off two hours later, and treating it as open forever would attach a
# callback to every loud moment for the rest of the stream.
DEFAULT_LIFETIME_S = 45 * 60.0
# ...and a payoff cannot precede its own setup, nor sit on top of it: closer
# than this and the two are one moment, which the ordinary context path
# already handles better.
MIN_CALLBACK_GAP_S = 90.0


def normalise_promise(raw: Any, duration: float) -> dict | None:
    """One model-proposed setup, validated. None when it cannot be placed.

    A `goal` with no stake is dropped. Measured on the 4-hour run: 8 of the 12
    setups the sweep returned came back as `goal`, and they were "I'm going to
    go to the gym", "I'm hitting the cave" — announcements of the next action,
    which nobody waits for and which cannot turn out to be wrong. `goal` was
    the bucket the model emptied ordinary intentions into. The other four kinds
    are falsifiable on their own; a goal only becomes one when something is
    riding on it, which is also the spec's own example ("or I quit").
    """
    if not isinstance(raw, dict):
        return None
    t = _num(raw.get("t", raw.get("time")), -1.0)
    if t < 0 or (duration > 0 and t > duration):
        return None
    text = str(raw.get("text") or raw.get("what") or "").strip()[:160]
    if not text:
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    kind = kind if kind in KINDS else "prediction"
    stake = str(raw.get("stake") or "").strip()[:120]
    if kind in _STAKE_REQUIRED and not stake:
        return None
    return {
        "t": round(t, 3),
        "kind": kind,
        "text": text,
        "stake": stake,
        "confidence": _clamp01(_num(raw.get("confidence"), 0.6)),
        "prompt_version": PROMISE_PROMPT_VERSION,
    }


def open_at(promises: Sequence[dict], t: float,
            lifetime_s: float = DEFAULT_LIFETIME_S,
            span_from: float | None = None) -> list[dict]:
    """Setups a payoff anywhere in [span_from, t] could still be resolving.

    Bounded by both a gap and a lifetime. Without the gap a payoff would be
    matched to a setup it is sitting on top of, which the ordinary
    required-context path already handles better; without the lifetime every
    loud moment for the rest of the stream inherits a callback.

    `span_from` matters whenever the caller is asking on behalf of a RANGE
    rather than a moment — a prompt covers a whole chunk, and a chunk of this
    source is hours long. Asking only about its end left the first chunk of a
    4-hour run with zero setups offered, because every promise in it was more
    than a lifetime older than minute 201. A payoff at minute 60 has to be
    able to see a promise from minute 20.
    """
    earliest = t if span_from is None else min(span_from, t)
    live = []
    for p in promises or []:
        if not isinstance(p, dict):
            continue
        when = _num(p.get("t"), -1e9)
        # Live for anyone in the span: old enough for the earliest payoff that
        # could use it, young enough for the latest.
        if when > t - MIN_CALLBACK_GAP_S:
            continue
        if when < earliest - lifetime_s:
            continue
        live.append(p)
    live.sort(key=lambda p: -_num(p.get("t")))
    return live


def callback_debt(promise: dict | None, words: Sequence[dict]) -> float:
    """What a callback costs a cold viewer, 0..1.

    The setup is an hour away and cannot be in the window, so by default the
    clip owes all of it. It owes less when the payoff line restates the setup
    in its own words — which is what a streamer usually does, and is exactly
    the case where a callback works as a standalone clip.
    """
    if not isinstance(promise, dict):
        return 0.0
    said = {norm_token(w.get("word", "")) for w in words or []}
    said.discard("")
    if not said:
        return 1.0
    wanted = {norm_token(tok) for tok in str(promise.get("text") or "").split()}
    wanted = {t for t in wanted if len(t) > 3}
    if not wanted:
        return 0.6
    restated = len(wanted & said) / float(len(wanted))
    return round(_clamp01(1.0 - restated), 3)


def prompt(lines: str, want: int) -> str:
    return (
        "Below is part of a livestream transcript, one line per segment, "
        "prefixed with its start time in seconds.\n\n"
        f"Find up to {want} statements that CREATE AN EXPECTATION someone "
        "could pay off later: a prediction about what will happen, a promise "
        "to do something, a bet, a challenge accepted, a goal announced.\n\n"
        "Examples of the shape:\n"
        '  "if I lose this I\'ll shave my head"\n'
        '  "there is no way he does that"\n'
        '  "today we are beating this boss or I quit"\n'
        '  "I guarantee this next one works"\n\n'
        "Ignore ordinary intentions — \"let me get my food\", \"I'll go left\", "
        "\"I'm going to the gym later\", \"I'm hitting the cave\". Announcing "
        "what you are about to do next is not a setup: nobody waits for it and "
        "nothing about it can turn out to be wrong.\n\n"
        "A setup must be FALSIFIABLE — there has to be an outcome that would "
        "make it right or wrong. If it also carries a stake (what happens if "
        "it fails), say so in `stake`; leave `stake` empty when there is none.\n"
        "Use kind \"goal\" ONLY when the goal carries a stake. A goal with no "
        "stake is an ordinary intention: leave it out.\n\n"
        f'Answer as JSON only: [{{"t": <seconds>, "kind": '
        f'"{"|".join(KINDS)}", "text": "<max 12 words, what was promised>", '
        f'"stake": "<max 8 words, or empty>", "confidence": <0-1>}}]\n\n'
        f"--- TRANSCRIPT ---\n{lines}"
    )


async def detect(segments: Sequence[dict], duration: float, *,
                 per_chunk: int = 6,
                 engines: Sequence[str] | None = None,
                 model: str | None = None) -> list[dict]:
    """Every setup in the stream that could pay off later. [] when no engine."""
    from services.clipper import llm_select

    lines = llm_select.transcript_lines(segments)
    if not lines:
        return []
    engines = engines or llm_select.NOMINATE_ENGINES
    found: list[dict] = []
    for chunk in llm_select.chunk_lines(lines):
        answer = await llm_select._ask(engines, prompt(chunk, per_chunk),
                                       model=model)
        if answer is None:
            continue
        for raw in (llm_select.parse_json(answer) or []):
            item = normalise_promise(raw, duration)
            if item is not None:
                found.append(item)
    found.sort(key=lambda p: p["t"])
    logger.info("promises: %d setups found (%s)", len(found),
                PROMISE_PROMPT_VERSION)
    return found
