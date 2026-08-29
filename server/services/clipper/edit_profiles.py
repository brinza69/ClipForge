"""Which editing grammar a clip gets, and how sure we are it is the right one.

Batch R2 of `docs/plans/ai-stream-clipper-production-engine-v1.md`. The renderer
has one grammar today and applies it to everything: 29,3 cuts a minute on a
tutorial and on a Just Chatting stream alike. A profile is the pace and the
vocabulary a TYPE of content can carry; the sequence regime — R3's half — then
says what has to be visible right now.

Three things this module is careful about, all of them things the plan insists
on:

- **The numbers below are guardrails, not results.** Nothing here has been
  measured against a corpus yet. They are written down so the engine stops
  pretending every source is the same, and they must not be quoted as findings
  until the human gate in §7 has run. A comment saying "chosen" is the whole
  point of the comment.
- **A weak classification buys a SAFER edit, never a bolder one.** Missing or
  low confidence resolves to `conservative`: long holds, safe framing, no forced
  alternation. The failure this prevents is a guess about the content type
  turning into an aggressive cut pattern the source cannot support.
- **Absent confidence stays absent.** It is never reconstructed from the
  source's overall score or from the classifier's own label — a number invented
  here would look exactly like a measured one to everything downstream.

This maps the ten EXISTING content types onto six profiles. It does not add a
second classifier; `content_type.py` remains the only thing that decides what a
stretch is.
"""

from __future__ import annotations

from typing import Any

# --- the mode this all runs in ----------------------------------------------
#
# Same shape as `reasoning_mode`, and for the same reason: a setting that reads
# back as something other than what was asked for is worse than a refusal.

#: The renderer exactly as it is today. The rollback.
LEGACY_DYNAMIC = "legacy_dynamic"
#: Resolve the profile, record it, deliver the legacy plan anyway. This is how
#: the new grammar becomes observable without changing anybody's export.
CONTENT_AWARE_SHADOW = "content_aware_shadow"
#: Deliver the profile's grammar. Refused until the final gate in §7 has run.
CONTENT_AWARE = "content_aware"

MODES: tuple[str, ...] = (LEGACY_DYNAMIC, CONTENT_AWARE_SHADOW, CONTENT_AWARE)
#: What a client may ask for today.
SELECTABLE: tuple[str, ...] = (LEGACY_DYNAMIC, CONTENT_AWARE_SHADOW)

DEFAULT_MODE = LEGACY_DYNAMIC


def clean_mode(value: object) -> str:
    """A mode string, or `""` when it is not one we know."""
    text = str(value or "").strip().lower()
    return text if text in MODES else ""


def resolve_mode(cfg: dict | None, *, default: object = DEFAULT_MODE) -> str:
    """The mode ASKED FOR: the project's own setting, else the rig's.

    An unknown value falls back rather than raising. The API refuses a bad mode
    at the door with a message; by the time the worker reads the settings the
    only useful behaviour is to keep rendering.

    This does NOT check availability. Use `available_mode` anywhere the answer
    decides what runs.
    """
    chosen = clean_mode((cfg or {}).get("edit_mode"))
    return chosen or clean_mode(default) or DEFAULT_MODE


def available_mode(cfg: dict | None, *, default: object = DEFAULT_MODE) -> str:
    """The mode that will actually run, clamped to one that exists yet.

    The ONE resolver for "what is in force", shared by the router and the
    worker. The router refuses an unimplemented mode to a client, and config.py
    plus a hand-edited settings row are two more doors into the same setting: a
    rig set to `content_aware` produced `applied: true` on every render while no
    new grammar was connected to anything.
    """
    mode = resolve_mode(cfg, default=default)
    return mode if mode in SELECTABLE else DEFAULT_MODE


def delivers_profile(mode: str) -> bool:
    """Whether the resolved profile reaches the render, or only the record.

    The availability check is HERE rather than in the caller. Answering only for
    the mode it was handed made this true for `content_aware` while no grammar
    was connected to anything — correct in today's one call site, and one
    careless caller away from being wrong again.

    False today, and true by itself on the day the final gate adds
    `CONTENT_AWARE` to `SELECTABLE`. No second switch to remember.
    """
    return clean_mode(mode) == CONTENT_AWARE and CONTENT_AWARE in SELECTABLE


# --- the profiles ------------------------------------------------------------

#: The sequence regimes, as a CLOSED list. R3 assigns them; R2 only needs the
#: vocabulary to exist so a profile can say which ones it will use.
#:
#: `safe` is not an error. It is the correct answer when the alternative is an
#: invented crop.
REGIMES: tuple[str, ...] = (
    "speaker",           # a subject is tracked with confidence
    "conversation",      # two or more relevant people are visible
    "action",            # measured action in the right region
    "visual_evidence",   # a diagram, screen share or object that must be seen whole
    "reaction",          # a stable creator plus external content
    "safe",              # the signals disagree, or there are not enough of them
)

CONSERVATIVE = "conservative"

#: profile -> (cuts per minute low, high, the rule that matters, regimes it uses)
#:
#: CHOSEN, NOT MEASURED. See the module docstring. The bands come from the plan's
#: §4 table; the corpus has not been cut to them yet.
PROFILES: dict[str, dict[str, Any]] = {
    "talking_head": {
        "cuts_per_min": (5.0, 10.0),
        "rule": "hold the subject; reframe only on a clear idea or emotion",
        "regimes": ("speaker", "visual_evidence", "safe"),
    },
    "conversation": {
        "cuts_per_min": (5.0, 12.0),
        "rule": "stable frame; the active speaker only on a sure signal; fit on a slide",
        "regimes": ("conversation", "speaker", "visual_evidence", "safe"),
    },
    "action": {
        # The only profile whose pace is a range of ranges: events set it, and a
        # lull is not an invitation to cut faster.
        "cuts_per_min": (12.0, 28.0),
        "cuts_per_min_quiet": (5.0, 12.0),
        "rule": "events set the pace; the creator and the action have distinct roles",
        "regimes": ("action", "reaction", "speaker", "safe"),
    },
    "exploration": {
        "cuts_per_min": (6.0, 15.0),
        "rule": "follow the source's own scenes; fit for spaces and objects; "
                "do not chase every face",
        "regimes": ("visual_evidence", "speaker", "safe"),
    },
    "instructional": {
        "cuts_per_min": (4.0, 10.0),
        "rule": "stability while the information has to be read; no step cut out",
        "regimes": ("visual_evidence", "speaker", "safe"),
    },
    CONSERVATIVE: {
        "cuts_per_min": (3.0, 8.0),
        "rule": "long holds, safe framing, no forced alternation",
        "regimes": ("safe", "speaker"),
    },
}

#: The ten existing content types, mapped onto the six profiles. Types the
#: classifier can emit but nobody has profiled resolve to `conservative` through
#: the lookup below, not through a silent default here.
PROFILE_FOR_TYPE: dict[str, str] = {
    "talking_head": "talking_head",
    "commentary": "talking_head",
    "podcast": "conversation",
    "interview": "conversation",
    "gaming": "action",
    "sports": "action",
    "irl": "exploration",
    "low_dialogue": "exploration",
    "tutorial": "instructional",
    "unknown": CONSERVATIVE,
}

#: Below this, the classification is not trusted with a bolder grammar. CHOSEN,
#: not calibrated: the plan says the threshold is calibrated on the corpus, and
#: that has not happened. It sits here as one named constant so the calibration
#: has exactly one thing to change.
CONFIDENCE_FLOOR = 0.5

# Why a profile was chosen, as a closed set — so a report can count them instead
# of matching prose.
BY_TYPE = "type"
BY_LOW_CONFIDENCE = "low_confidence"
BY_MISSING_CONFIDENCE = "missing_confidence"
BY_UNKNOWN_TYPE = "unknown_type"
#: A person set the content type. Trusted without a confidence: the provenance
#: IS the evidence, and the alternative — demanding a number a human never
#: produced — would quietly downgrade every overridden project to conservative,
#: which is the opposite of what the override was for.
BY_OVERRIDE = "override"
#: A confidence outside 0..1. The number is kept for diagnosis and the profile
#: is refused: a classifier emitting 1.7 is broken, and "broken" must not read
#: as "even surer than sure". This was the hole in the rule the module is built
#: on — a weak classification bought a safer edit, and an IMPOSSIBLE one bought
#: the boldest.
BY_INVALID_CONFIDENCE = "invalid_confidence"
REASONS: tuple[str, ...] = (BY_TYPE, BY_LOW_CONFIDENCE, BY_MISSING_CONFIDENCE,
                            BY_UNKNOWN_TYPE, BY_OVERRIDE, BY_INVALID_CONFIDENCE)


def confidence_value(value: object) -> float | None:
    """A confidence, or None. None is a legitimate answer and stays one.

    Not clamped into range and not defaulted: a classifier that emits 1.7 has a
    bug worth seeing, and a missing number must not become 0.0 — that would read
    as "measured, and low" instead of "never measured". `resolve` refuses to act
    on an out-of-range number; this only refuses to invent one.

    Public because persistence needs the same coercion: `content_confidence` is
    a Float column, and a string or a list reaching it raises on the way in and
    can take a whole board's worth of clips down with it.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def resolve(content_type: object, confidence: object = None,
            origin: object = None) -> dict:
    """The profile for one clip, with the reason it was chosen.

    Returns `{profile, reason, content_type, confidence, origin, cuts_per_min,
    rule, regimes}`. `confidence` comes back exactly as it went in — a float or
    None — because everything downstream needs to tell "low" from "unknown".
    """
    name = str(content_type or "").strip().lower()
    score = confidence_value(confidence)
    overridden = str(origin or "").strip().lower() == "override"

    if overridden and name in PROFILE_FOR_TYPE and name != "unknown":
        profile, reason = PROFILE_FOR_TYPE[name], BY_OVERRIDE
    elif name not in PROFILE_FOR_TYPE:
        profile, reason = CONSERVATIVE, BY_UNKNOWN_TYPE
    elif name == "unknown":
        profile, reason = CONSERVATIVE, BY_UNKNOWN_TYPE
    elif score is None:
        # Never reconstructed from the source's score or from the label itself.
        profile, reason = CONSERVATIVE, BY_MISSING_CONFIDENCE
    elif not 0.0 <= score <= 1.0:
        profile, reason = CONSERVATIVE, BY_INVALID_CONFIDENCE
    elif score < CONFIDENCE_FLOOR:
        profile, reason = CONSERVATIVE, BY_LOW_CONFIDENCE
    else:
        profile, reason = PROFILE_FOR_TYPE[name], BY_TYPE

    spec = PROFILES[profile]
    return {
        "profile": profile,
        "reason": reason,
        "content_type": name or "unknown",
        "confidence": score,
        "origin": str(origin or "").strip().lower() or None,
        "cuts_per_min": list(spec["cuts_per_min"]),
        "rule": spec["rule"],
        "regimes": list(spec["regimes"]),
    }
