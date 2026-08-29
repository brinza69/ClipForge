"""Structural quality metrics for rendered clipper exports.

Batch R0 of `docs/plans/ai-stream-clipper-production-engine-v1.md`. The audit
that produced the v3 baseline — 1.341 shots, 29,3/min, 116 invisible cuts — was
done by hand once. This module is that audit written down, so any agent can
rerun it and get the same numbers instead of re-deriving them.

Pure and DB-free on purpose: it takes the export sidecar as a dict and returns
dicts. The only input is `exports/<clip_id>.json`, because that is the render
plan as persisted — the plan the file was actually made from. The DB can have
been rescored since, and `.cmd.txt` is a rendering of the plan, not the plan.

Three rules the numbers depend on:

- **A missing measurement is `unavailable`, never zero.** Zero is a
  measurement — "this clip has no invisible cuts" — and a report that spells
  absence the same way it spells success cannot be used as a gate. Aggregates
  therefore carry a `coverage` block saying how many clips actually contributed
  to each figure.
- **A malformed artifact is a finding, not something to repair.** Shot order is
  read as persisted, never sorted; a shot list that is not a list of shots does
  not quietly become a static export; a plan with no `composition` does not
  quietly produce zero equivalences. Every one of those was silently repaired in
  the first draft of this module and every one of them hid the defect the audit
  exists to find.
- **The clock is the delivered clock.** `drop_spans` are the seconds the render
  removed. Shot and word times are remapped through them before anything is
  measured, because a shot rate computed on the untrimmed window describes a
  video nobody has.

What this module deliberately does NOT do is decide whether two DIFFERENT
compositions look alike: no IoU, no rect proximity, no pixel similarity, no
perceptual thresholds. Those belong to Batch R1 and need a measurement first.
R0 counts only equivalence that is exactly demonstrable — see `_boundary`.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from services.clipper import dead_air

# Split out at 500 lines and re-exported, so `edit_quality.input_fingerprint`
# keeps working for the export path and the audit script.
from services.clipper.render_input import (  # noqa: F401
    FINGERPRINT_KEYS,
    FINGERPRINT_SCHEMA,
    input_fingerprint,
    render_input,
)

# Absence, spelled one way everywhere. A plain string so it survives a round
# trip through JSON and can be compared with `==`.
UNAVAILABLE = "unavailable"

# Two shots are contiguous when the second starts where the first ended. The
# tolerance is float noise from json, not an editorial judgement — plans are
# written with two decimals and read back as binary floats.
CONTIGUITY_EPS = 0.001

# A clip "starts on the first word" when there is no measurable gap before it.
# Same tolerance, same reason.
LEAD_IN_EPS = 0.001

# The audit's threshold for "no air at the end": 50ms after the last word is
# below the gap a viewer reads as a finished sentence. It is a reporting
# threshold, not a repair — R5 owns the repair.
TAIL_TIGHT_S = 0.050

FINGERPRINT_VALID = "valid"
FINGERPRINT_MISMATCH = "mismatch"


def fingerprint_status(sidecar: dict) -> str:
    """`valid`, `mismatch`, or `unavailable`.

    Copying the stored digest into the report would have made it decorative:
    an artifact whose plan was edited after the render would carry a stale
    fingerprint and pass. Recomputing is the only thing that makes it a check.
    """
    stored = sidecar.get("input_fingerprint")
    if not isinstance(stored, str) or not stored:
        return UNAVAILABLE
    return FINGERPRINT_VALID if stored == input_fingerprint(sidecar) else FINGERPRINT_MISMATCH

def _num(value: Any) -> float | None:
    """A finite float, or None when the value cannot be one. Never raises: this
    module reads artifacts written by six months of different code paths.

    NaN was rejected here from the start; the infinities were not, and `inf`
    read as a perfectly good number all the way into `min_shot_s`. Readable is
    not the same as valid, which is the whole of `_times_are_sane` below.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _drop_spans(sidecar: dict) -> tuple[list[tuple[float, float]] | None, str | None]:
    """The seconds this render removed, plus what is wrong with the claim.

    Three states, and the first version collapsed two of them:

    - the key is absent — the sidecar does not say. Exports written before it
      existed cannot tell us either way, and that decides whether a duration is
      the delivered one or merely the window it was cut from;
    - the key is present and readable — the trim, in seconds;
    - the key is present and CORRUPT — a claim about the delivered video that
      cannot be read. Returning None for this said "never declared", which is
      the one answer that is certainly wrong: the export did declare something.
      The rule that shots and words already follow, arriving late.
    """
    if "drop_spans" not in sidecar or sidecar.get("drop_spans") is None:
        return None, None
    spans = sidecar.get("drop_spans")
    if not isinstance(spans, list):
        return None, "malformed_drop_spans"
    out: list[tuple[float, float]] = []
    for span in spans:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            return None, "malformed_drop_spans"
        a, b = _num(span[0]), _num(span[1])
        if a is None or b is None or b < a:
            return None, "malformed_drop_spans"
        out.append((a, b))
    return out, None


def _shots(sidecar: dict) -> tuple[list[dict] | None, str | None]:
    """The persisted shot list, plus what is wrong with it.

    Returns `(shots, defect)`. `shots` is None when there is nothing to measure.
    The three states this keeps apart were one state in the first draft:

    - no `dynamic_plan` at all — a static export, legitimate;
    - a `dynamic_plan` whose `shots` is not a list of shots — a broken artifact
      that must NOT read as a static export;
    - a list that is empty — the planner ran and produced nothing.
    """
    if "dynamic_plan" not in sidecar or sidecar.get("dynamic_plan") is None:
        return None, None
    plan = sidecar.get("dynamic_plan")
    if not isinstance(plan, dict):
        return None, "malformed_dynamic_plan"
    shots = plan.get("shots")
    if not isinstance(shots, list):
        return None, "malformed_shots"
    if any(not isinstance(s, dict) for s in shots):
        # Dropping the bad entries would renumber every boundary after them and
        # report a shot count for a plan that does not exist.
        return None, "malformed_shots"
    return shots, None


#: The closed list. A composition outside it cannot be reasoned about by the
#: equivalence rule, so it is undecidable — not "not fit". `composition="banana"`
#: used to produce a `real` boundary and sit in the distribution looking like a
#: measurement.
COMPOSITIONS: frozenset[str] = frozenset({"crop", "fit"})


def _times_are_sane(t0: float | None, t1: float | None, clock: float | None) -> bool:
    """Whether a pair of clip times can describe a piece of THIS clip.

    Checked against the WINDOW, never the delivered clock: the times in a plan
    are written before the trim is applied, so measuring them against the
    trimmed duration condemns everything after the first removed second.

    A time that parses is not a time that happened. Shots starting five seconds
    before the clip did, and a 900-second shot inside a ten-second export, both
    read as clean edits: the numbers were floats, so nothing looked at them
    again. The clip runs from 0 to its own duration, and anything outside that
    is a claim about a video that does not exist.
    """
    if t0 is None or t1 is None:
        return False
    if t0 < 0 or t1 <= t0:
        return False
    return clock is None or t1 <= clock + CONTIGUITY_EPS


def _word_times_are_sane(start: float | None, end: float | None,
                         clock: float | None) -> bool:
    """The same question for a caption word, with one difference that is a
    MEASUREMENT, not an oversight: a word may have zero length.

    156 of the 8.249 words in the four pilot exports have `end <= start` — the
    transcriber emits them for very short tokens. Requiring `end > start`, as
    the shot rule does, would condemn 58 of 58 clips and take the baseline with
    them. A word that runs BACKWARDS is still refused.
    """
    if start is None or end is None:
        return False
    if start < 0 or end < start:
        return False
    return clock is None or end <= clock + CONTIGUITY_EPS


def _composition(shot: dict) -> str | None:
    """The shot's composition, or None when it is absent OR not one this rule
    knows. Both mean the same thing to `_boundary`: it cannot be judged."""
    value = shot.get("composition")
    if not isinstance(value, str) or value not in COMPOSITIONS:
        return None
    return value


def _composition_label(shot: dict) -> str:
    """What to show in the distribution. Unlike `_composition` this keeps an
    unknown value visible under its own name — hiding it in `unavailable` would
    lose the one thing worth seeing about a plan nobody can read.

    ABSENT is the only thing that reads as `unavailable`. A key that is present
    and holds `null`, a number, or an unknown string is a plan making a claim
    that cannot be true, and folding those into "no composition" filed them
    under age instead of corruption — the same confusion one layer down.
    """
    if "composition" not in shot:
        return UNAVAILABLE
    value = shot.get("composition")
    if isinstance(value, str) and value in COMPOSITIONS:
        return value
    return f"unknown:{value!r}" if not isinstance(value, str) or not value         else f"unknown:{value}"


def _words(sidecar: dict) -> tuple[list[dict] | None, str | None]:
    """Every word in the caption plan, in chunk order, plus what is wrong.

    The caption plan is the only place in the sidecar with word-level times, so
    it is where lead-in and tail have to come from. Chunks are read in the order
    they were written, for the same reason shots are.

    Same rule as the shot list, and for the same reason: skipping a malformed
    chunk would compute a lead-in and a tail from a SUBSET of the captions and
    report them as the clip's. A subset silently presented as the whole is how
    an audit passes a file it never read.
    """
    plan = sidecar.get("caption_plan")
    if not isinstance(plan, dict):
        return None, None
    chunks = plan.get("chunks")
    if chunks is None:
        return None, None
    if not isinstance(chunks, list):
        return None, "malformed_caption_plan"
    out: list[dict] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            return None, "malformed_caption_plan"
        words = chunk.get("words") or []
        if not isinstance(words, list) or any(not isinstance(w, dict) for w in words):
            return None, "malformed_caption_plan"
        out.extend(words)
    return out, None


def _remap(t: float | None, spans: Sequence[tuple[float, float]] | None) -> float | None:
    """A clip time on the delivered clock. Identity when nothing was removed."""
    if t is None or not spans:
        return t
    return dead_air.remap_time(t, spans)


def _boundary(previous: dict, shot: dict) -> str:
    """What the cut between two shots is: `equivalent`, `real`, `gap`, or
    `undecidable`.

    The whole of R0's equivalence rule lives in the one branch. After
    composition a `fit` shot is the same virtual camera whatever the plan says
    about it — full canvas, position 0,0, and rect/camera/move no longer reach
    the framing. So two contiguous `fit` shots deliver one uninterrupted image
    and the cut between them exists only in the plan.

    `undecidable` is the case that must not collapse into `real`: a plan written
    before `composition` existed cannot be judged by this rule at all, and
    counting its boundaries as "not equivalent" would report a clean corpus.
    """
    prev_t1 = _num(previous.get("t1"))
    t0 = _num(shot.get("t0"))
    if prev_t1 is None or t0 is None or abs(prev_t1 - t0) > CONTIGUITY_EPS:
        return "gap"
    before, after = _composition(previous), _composition(shot)
    if before is None or after is None:
        return "undecidable"
    return "equivalent" if before == "fit" == after else "real"


def clip_report(sidecar: dict) -> dict:
    """Structural metrics for one exported clip.

    Every key is either a number or `UNAVAILABLE`. Nothing here reads the DB,
    the mp4, or the scores: this is a report about the edit, and mixing in
    quality signals is what made the previous review unusable.
    """
    if not isinstance(sidecar, dict):
        raise TypeError("clip_report needs the parsed sidecar dict")

    shots, defect = _shots(sidecar)
    spans, span_defect = _drop_spans(sidecar)
    removed = dead_air.removed_seconds(spans) if spans is not None else None

    # ABSENT is unknown; PRESENT and unusable is a defect. A zero-length export
    # is not a clip with no shots per minute, and `inf` is not a duration — both
    # used to become "no duration" and pass the gate beside the exports that
    # simply never recorded one.
    duration_defect = None
    window = _num(sidecar.get("duration"))
    if "duration" in sidecar and sidecar.get("duration") is not None:
        if window is None or window <= 0:
            duration_defect = "invalid_duration"
    if window is not None and window <= 0:
        window = None
    delivered = None if window is None or removed is None else window - removed
    if delivered is not None and delivered <= 0:
        delivered = None
    # The clock everything else is measured on, and the name of which one it is.
    # An export that never declared its trim is measured on the window, and the
    # report says so rather than implying the two are the same thing.
    clock = delivered if delivered is not None else window
    clock_name = ("delivered" if delivered is not None
                  else "window" if window is not None else UNAVAILABLE)

    report: dict[str, Any] = {
        "clip_id": sidecar.get("clip_id") or UNAVAILABLE,
        "project_id": sidecar.get("project_id") or UNAVAILABLE,
        "render_version": sidecar.get("render_version") or UNAVAILABLE,
        "input_fingerprint": sidecar.get("input_fingerprint") or UNAVAILABLE,
        "fingerprint_status": fingerprint_status(sidecar),
        "duration_s": clock if clock is not None else UNAVAILABLE,
        "duration_clock": clock_name,
        "removed_s": removed if removed is not None else UNAVAILABLE,
        "shots": UNAVAILABLE,
        "shots_per_minute": UNAVAILABLE,
        "min_shot_s": UNAVAILABLE,
        "composition": UNAVAILABLE,
        # Declared per shot by the sequence regime of production-engine R3.
        # Nothing writes it yet, so the honest reading of this corpus is
        # `unavailable` — but the metric exists, because R0 was asked for it and
        # a metric that appears later cannot be compared against a baseline.
        "regime": UNAVAILABLE,
        "equivalent_cuts": UNAVAILABLE,
        "non_contiguous_boundaries": UNAVAILABLE,
        "undecidable_boundaries": UNAVAILABLE,
        "lead_in_s": UNAVAILABLE,
        "tail_s": UNAVAILABLE,
        # Nothing in the sidecar declares that the SOURCE already carried burned
        # subtitles. The audit's 15/15 on go ghost was a human watching the
        # files. Reporting 0 here would turn "we never asked" into "we checked
        # and it is clean", which is the exact failure this module exists to
        # prevent. R6 owns the signal; until it lands this stays unavailable.
        "captions_duplicate_declared": UNAVAILABLE,
        "static_export": shots is None and defect is None,
        # A LIST. The singular field meant a clip with a malformed plan AND a
        # malformed caption plan reported only whichever was found first, and
        # the second defect left no trace at all.
        "defects": [d for d in (defect, span_defect, duration_defect) if d],
    }

    if shots and spans:
        # A trim does not just move the clock, it changes the EDIT: a shot that
        # falls entirely inside a removed span is not in the delivered video at
        # all, and one with a span through its middle becomes two pieces with a
        # jump between them. Counting the planned shots against the delivered
        # duration mixes two different videos — a plan of three shots whose
        # middle one was cut whole still reported `shots=3`.
        #
        # Reconstructing the delivered sequence is R1's job and needs a rule for
        # what a mid-shot trim IS. Until there is one, the shot-based half of
        # this report is refused rather than guessed. The clock-based half —
        # duration, lead-in, tail — stays exact, because remapping times is
        # arithmetic and needs no such rule.
        report["defects"].append("trimmed_edit_not_reconstructed")
    elif shots and any(not _times_are_sane(_num(s.get("t0")), _num(s.get("t1")), window)
                       for s in shots):
        # A shot whose times cannot be read is not a shot that happens to be
        # missing from `min_shot_s`. Skipping it left the plan looking like a
        # clean edit: the count still included it, its length vanished from the
        # minimum, and its two boundaries became `gap`s — jump cuts that nothing
        # in the plan actually asked for.
        report["defects"].append("malformed_shot_times")
    elif shots:
        lengths = []
        composition: dict[str, int] = {}
        regime: dict[str, int] = {}
        kinds = {"equivalent": 0, "real": 0, "gap": 0, "undecidable": 0}
        invalid_compositions: set[str] = set()
        for i, shot in enumerate(shots):
            t0, t1 = _num(shot.get("t0")), _num(shot.get("t1"))
            lengths.append(t1 - t0)
            label = _composition_label(shot)
            if label.startswith("unknown:"):
                # An old plan has no `composition` and is undecidable — age is
                # not corruption, and the corpus is full of them. A plan that
                # names a composition nobody ever wrote is the other thing, and
                # treating the two the same let an explicitly invalid plan pass
                # the gate. The value stays visible in the distribution: it is
                # the evidence for the finding.
                invalid_compositions.add(label)
            composition[label] = composition.get(label, 0) + 1
            mode = shot.get("regime")
            mode = mode if isinstance(mode, str) and mode else UNAVAILABLE
            regime[mode] = regime.get(mode, 0) + 1
            if i:
                kinds[_boundary(shots[i - 1], shot)] += 1

        if invalid_compositions:
            report["defects"].append("invalid_composition")
        report["shots"] = len(shots)
        report["composition"] = composition
        report["regime"] = regime
        report["non_contiguous_boundaries"] = kinds["gap"]
        report["undecidable_boundaries"] = kinds["undecidable"]
        # One boundary nobody can judge makes the count of invisible cuts a
        # lower bound, and a lower bound reported as a total is the failure
        # this module is a reaction to.
        report["equivalent_cuts"] = (kinds["equivalent"] if not kinds["undecidable"]
                                     else UNAVAILABLE)
        if lengths:
            report["min_shot_s"] = min(lengths)
        if clock is not None:
            report["shots_per_minute"] = len(shots) / (clock / 60.0)
    elif defect is None and shots is not None:
        # A dynamic plan that rendered nothing. Counts stay unavailable, but the
        # empty list is itself the finding.
        report["shots"] = 0
        report["defects"].append("empty_dynamic_plan")

    words, word_defect = _words(sidecar)
    if word_defect:
        report["defects"].append(word_defect)
    elif words and any(not _word_times_are_sane(_num(w.get("start")),
                                                _num(w.get("end")), window)
                       for w in words):
        # Same rule as the shots, and it was missing for the same reason: the
        # surviving words still produced a lead-in and a tail, and the report
        # presented them as the clip's.
        report["defects"].append("malformed_word_times")
        words = None
    starts = [s for s in (_remap(_num(w.get("start")), spans) for w in (words or []))
              if s is not None]
    ends = [e for e in (_remap(_num(w.get("end")), spans) for w in (words or []))
            if e is not None]
    if starts:
        report["lead_in_s"] = min(starts)
    if ends and clock is not None:
        report["tail_s"] = clock - max(ends)
    return report


# NO re-export of `edit_quality_totals` here. It was one, and it made the two
# modules a cycle that worked only if `edit_quality` happened to be imported
# first — `import edit_quality_totals` on its own raised ImportError. The
# aggregation imports this module; this module must not import it back. Callers
# that want both ask for both.
