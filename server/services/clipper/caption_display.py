"""How long each caption card is ON SCREEN — display time, never speech time (BURST1).

`MIN_CHUNK_S` is a chosen display duration, not a measured word time (closure-3 §1 B). Before this
module the builder gave every chunk `max(last word end, start + MIN_CHUNK_S)` and nothing looked at the
next chunk, so on the 85 burned exports 878 event pairs sat on one `\\pos` at once: a Whisper burst of
point words (start == end) made several chunks start at one instant and each got the same
[s, s + 0.12] — 478 of the 602 cross-chunk pairs — and the display minimum alone pushed an end past
the next start in the other 124 (B/BURST1-result.md §2, measured, not inferred).

The rule, for the chunks of ONE plan (one anchor):
  * a RUN is a maximal sequence where each next chunk starts less than the display minimum after the
    current one — no room for the current card before the next begins; a chunk with room is a run of 1;
  * the run's window starts at its first chunk (or where the previous card ends, if later) and ends at
    the earliest of: the next run's start, the clip's end, and max(its latest end, n x minimum). It never
    reaches into the next card's time or past the clip;
  * the window is split evenly, in order, on whole milliseconds: contiguous, no overlap;
  * a share below the minimum is a LIMIT, recorded on the plan. The time is not invented — nothing is
    borrowed from the next card or from before the burst — and the cards are not merged: one card with
    twelve words for 0.12 s is not a repair either;
  * a window too short for every card to get one tick of the .ass clock (`_FLOOR_MS`) shows the first
    ones it has room for, in order, and the rest are `no_time`: not drawn, and named in the limit's
    `unshown`. The floor used to be taken from the time after the run, which moved the next card's start
    (codex next-17 R1: 15 cards at 5.000 pushed a card measured at 5.120 to 5.165).
Word timings on the chunks stay the MEASURED ones; only the card's start/end are display.
"""

from __future__ import annotations

RULE = "settled_v1"

#: The .ass clock is centiseconds (pysubs2 rounds whole ms to the nearest cs): a card under 10 ms can
#: round to nothing. 11, because the writer's int(seconds * 1000) can take 1 ms off a card's length.
#: A card with less than this is not drawn; the time is never taken from after the run.
_FLOOR_MS = 11


def _ms(value) -> int:
    return int(round(float(value) * 1000))


def _file_cs(seconds) -> int:
    """Where a time lands in the .ass: `build_overlays_ass` takes int(seconds * 1000), pysubs2 rounds
    that to the nearest centisecond."""
    return (int(float(seconds) * 1000) + 5) // 10


def settle(chunks: list[dict], duration: float, min_chunk_s: float) -> tuple[list[dict], list[dict]]:
    """`(chunks, limits)`: the chunks with display intervals, and every run too short to read.

    `chunks` are in plan order with non-decreasing starts (the builder's word order). Each returned
    chunk is a copy; `display` says which intervals are the rule's rather than the last word's:
    "redistributed" (a card of a run of 2+), "min_duration" (a single card held past its last word),
    "no_time" (no room left for it before the next run or the clip's end — not drawn, listed in its
    limit's `unshown`).
    """
    out = [dict(c) for c in chunks]
    limits: list[dict] = []
    min_ms, dur_ms = _ms(min_chunk_s), _ms(max(duration, 0.0))
    starts = [_ms(c["start"]) for c in out]
    i, cursor = 0, 0
    while i < len(out):
        j = i
        while j + 1 < len(out) and starts[j + 1] - starts[j] < min_ms:
            j += 1
        n = j - i + 1
        lo = max(starts[i], cursor)
        hi = max(max(_ms(c["end"]) for c in out[i:j + 1]), starts[i] + n * min_ms)
        if j + 1 < len(out):
            hi = min(hi, starts[j + 1])
        hi = max(min(hi, dur_ms), lo)
        shown = min(n, (hi - lo) // _FLOOR_MS)
        bounds = [lo + (hi - lo) * k // shown for k in range(shown + 1)] if shown else [hi]
        bounds += [hi] * (n - shown)
        for k in range(n):
            c = out[i + k]
            c["start"], c["end"] = bounds[k] / 1000, bounds[k + 1] / 1000
            words = c.get("words") or []
            if bounds[k + 1] <= bounds[k]:
                c["display"] = "no_time"
            elif n > 1:
                c["display"] = "redistributed"
            elif words and bounds[k + 1] > _ms(words[-1].get("end", words[-1].get("start", 0))):
                c["display"] = "min_duration"
        if hi - lo < n * min_ms:
            limits.append({"chunks": [i, j], "start": lo / 1000, "end": max(hi, lo) / 1000,
                           "available_s": max(hi - lo, 0) / 1000, "needed_s": n * min_ms / 1000,
                           "unshown": list(range(i + shown, j + 1))})
        cursor = max(hi, lo)
        i = j + 1
    return out, limits


def is_settled(plan: dict) -> bool:
    display = (plan or {}).get("display")
    return isinstance(display, dict) and display.get("rule") == RULE


def drawable(overlays: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(drawn, unshown)`: the overlays that keep at least one tick of the .ass clock, and the rest.

    `build_overlays_ass` turns an end at or before its start into start + 1 s, for every caller. On the
    clipper path an interval the file cannot hold is not handed to it: a 1 ms remainder the remap left
    came back as a whole second, 500 ms on top of the next card (codex next-17 R2). It is reported.
    """
    drawn: list[dict] = []
    unshown: list[dict] = []
    for o in overlays:
        (drawn if _file_cs(o["end_t"]) > _file_cs(o["start_t"]) else unshown).append(o)
    return drawn, unshown


def ass_events(path) -> list[tuple[int, int, str | None]]:
    """`(start_ms, end_ms, \\pos)` of every event in the written file, in file order."""
    import re

    import pysubs2

    pos = re.compile(r"\\pos\(([^)]*)\)")
    return [(e.start, e.end, (m.group(1) if (m := pos.search(e.text)) else None))
            for e in pysubs2.load(str(path)).events]


def _agrees(events: list[tuple], cards: list[tuple[int, int]]) -> bool:
    """The file is the cards in order: each card's events start at its start, follow each other with
    no gap, and end at its end — nothing before, between or after. An event with no length at a
    card's boundary is counted in `empty_events`, not here."""
    k = 0
    for s, e in cards:
        cur = s
        while k < len(events) and events[k][0] == cur and (cur < e or events[k][1] == cur):
            cur = max(cur, events[k][1])
            k += 1
        if cur != e:
            return False
    return k == len(events)


def effective_report(events: list[tuple], drawn: list[dict], unshown: list[dict], plan: dict,
                     min_chunk_s: float) -> dict:
    """What the written file shows (`events` = `ass_events` of it), after the remap and the highlight
    spans: pairs of events on one anchor at once, events with no length, drawn cards shorter than
    the display minimum, every card that has NO display time (the plan's `no_time` and the overlays
    `drawable` held back) and whether the file is exactly the drawn cards. {} when there is nothing to
    report. A stored plan is reported, not re-timed. Cards the remap removes whole are not counted:
    that is dead-air trimming, and D2r K2 already reports it.
    """
    live = sorted((e for e in events if e[1] > e[0]), key=lambda e: (e[2] or "", e[0], e[1]))
    pairs = 0
    for a, (s, e, key) in enumerate(live):
        for s2, _e2, key2 in live[a + 1:]:
            if key2 != key or s2 >= e:
                break
            pairs += 1
    empty = sum(1 for s, e, _ in events if e <= s)
    cards = [(_file_cs(o["start_t"]) * 10, _file_cs(o["end_t"]) * 10) for o in drawn]
    short = [{"text": o.get("text"), "start": round(o["start_t"], 3), "end": round(o["end_t"], 3)}
             for o, (s, e) in zip(drawn, cards) if e - s < _ms(min_chunk_s)]
    settled = is_settled(plan)
    missing = [{"text": c.get("text"), "start": c.get("start"), "end": c.get("end"), "why": "no_time"}
               for c in ((plan or {}).get("chunks") or [] if settled else [])
               if c.get("display") == "no_time" and (c.get("text") or "").strip()]
    missing += [{"text": o.get("text"), "start": round(o["start_t"], 3), "end": round(o["end_t"], 3),
                 "why": "under_one_tick"} for o in unshown]
    agrees = _agrees(events, cards)
    limits = ((plan or {}).get("display") or {}).get("limits") or [] if settled else []
    if agrees and not (pairs or empty or short or missing or limits):
        return {}
    return {"overlapping_pairs": pairs, "empty_events": empty, "short_cards": len(short),
            "short_intervals": short, "unshown_cards": missing, "ass_agrees": agrees,
            "plan_settled": settled, "plan_limits": len(limits)}


def plan_facts(plan, min_chunk_s: float) -> dict:
    """What the plan ITSELF says about display time, on its own clock (clip-relative seconds, before
    any dead air is removed) — no render involved. `state`: "no_plan" (none stored: a render builds
    one), "unreadable", "not_settled" (a plan from before `settle`: its display times were never
    checked, which is not "clean"), "clean" or "limited" (a short or `no_time` card with text)."""
    if plan is None:
        return {"state": "no_plan"}
    if not isinstance(plan, dict) or not isinstance(plan.get("chunks") or [], list):
        return {"state": "unreadable"}
    if not is_settled(plan):
        return {"state": "not_settled"}
    short, unshown = [], []
    try:        # a PATCH can store any JSON as the plan; a card that cannot be read is no verdict
        for i, c in enumerate(plan.get("chunks") or []):
            if not isinstance(c, dict) or not str(c.get("text") or "").strip():
                continue
            card = {"index": i, "text": c["text"], "start": c.get("start"), "end": c.get("end")}
            if c.get("display") == "no_time":
                unshown.append({**card, "why": "no_time"})
            elif _ms(c["end"]) - _ms(c["start"]) < _ms(min_chunk_s):
                short.append(card)
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"state": "unreadable"}
    limits = plan["display"].get("limits")
    limits = limits if isinstance(limits, list) else []
    return {"state": "limited" if short or unshown else "clean", "clock": "plan",
            "short_cards": short, "unshown_cards": unshown, "limits": limits}
