"""
ClipForge — AI Stream Clipper: which reasoning engine a project runs.

ONE setting decides this. Before Batch 1 it took two — `llm_select` (a bool)
and `reasoning_version` (a string) — and the pair had a failure mode that cost
a whole audit to find: `_default_settings()` in routers/clipper.py never listed
either key, and `_normalise_settings` keeps only keys already in that dict. So
both were dropped, in silence, on every create and every patch. The story
engine could not be turned on from the UI or the API at all; the only way in
was a global environment variable. Two projects on this rig carry
`llm_select: true` in their stored settings and got there by direct DB writes —
their settings dicts hold 4 and 5 keys, and a dict that came through
`_normalise_settings` always holds the full default set.

WHAT IS PURE HERE. Everything. No DB, no config import, no FastAPI. The caller
supplies the fallbacks, which is what lets the router and the worker resolve a
mode the same way without either one importing the other.

WHY FIVE VALUES AND NOT FOUR. The agreed contract named four — legacy,
story_v1, story_v2_shadow, story_v2 — but the old pair could express a third
state those four cannot: `llm_select=true` with `reasoning_version` left at
"legacy" runs `llm_select.nominate()`, the LLM nomination pass, WITHOUT the
story engine. No project in the DB is in that state today, but config.py can
still put the whole rig there, and folding it into `legacy` would silently
switch off both nomination and the judge for anyone who had it on. So it keeps
a name. Do not "simplify" this back to four without checking
`clipper_build._propose` for what `uses_llm` and `uses_story` gate.

STORY_V2 IS NOT SELECTABLE YET. It is a value the rest of the pipeline does not
implement — the selection rule that defines it ships in shadow first and cannot
become the default until the Batch 5 shortlist exists (see
docs/plans/ai-stream-clipper-reasoning-v2.md §6). A request that asks for it is
REFUSED rather than quietly downgraded to shadow: a setting that reads back
differently from what runs is the exact defect this module was written to end.
"""

from __future__ import annotations

LEGACY = "legacy"
LLM_NOMINATE = "llm_nominate"
STORY_V1 = "story_v1"
STORY_V2_SHADOW = "story_v2_shadow"
STORY_V2 = "story_v2"

#: Every mode the pipeline can be resolved INTO, including ones a client may
#: not ask for. Order is roughly "how much model is involved".
MODES: tuple[str, ...] = (LEGACY, LLM_NOMINATE, STORY_V1, STORY_V2_SHADOW, STORY_V2)

#: What a client is allowed to SET.
#:
#: `story_v2_shadow` was added on 2026-08-22, when the thing its name promises
#: became true: `selection.board` computes the v2 ordering and, in shadow, the
#: legacy order still ships. Before that it would have run the story engine AND
#: the judge and quietly reordered the user's board — the opposite of what it
#: says, and the exact divergence this module exists to end.
#:
#: `story_v2` is still missing. The rule it names now WORKS; what has not
#: happened is the comparison. Shadow has to run on the corpus and be reviewed
#: against legacy before v2 orders anyone's board, which is Batch 10. Offering
#: it now would skip the only step that could show the change is an improvement
#: rather than a difference.
SELECTABLE: tuple[str, ...] = (LEGACY, LLM_NOMINATE, STORY_V1, STORY_V2_SHADOW)

#: Modes that run a language model at all: the proposal pass and the judge.
_WITH_LLM = frozenset((LLM_NOMINATE, STORY_V1, STORY_V2_SHADOW, STORY_V2))

#: Modes that reason payoff-first through `story.py` rather than nominating
#: bare windows. These also set `keep_overlaps` when merging nominations.
_WITH_STORY = frozenset((STORY_V1, STORY_V2_SHADOW, STORY_V2))


def _clean(value: object) -> str:
    return str(value or "").strip().lower()


def from_legacy_keys(llm_select: bool, version: object) -> str:
    """The old `llm_select` + `reasoning_version` pair, as one mode.

    This is the compatibility path and it has to stay: 133 projects on this rig
    predate the setting entirely and two carry the old pair. Their behaviour
    must not change because the name of the switch did.
    """
    if not llm_select:
        return LEGACY
    return STORY_V1 if _clean(version) == STORY_V1 else LLM_NOMINATE


def resolve(cfg: dict | None, *, llm_select_default: bool = False,
            version_default: str = LEGACY, mode_default: str = "") -> str:
    """The mode a project actually runs in. Never raises, always a valid mode.

    Precedence, highest first:

    1. an explicit `reasoning_mode` in the project's own settings;
    2. the old pair in the project's own settings, if either key is present —
       a key the project does not carry falls through to the rig's default,
       which is what `cfg.get(key, default)` did before this module existed;
    3. the rig's `reasoning_mode` default;
    4. the rig's old pair.

    `story_v2` resolves normally here. Refusing it is the API's job, not this
    function's — a stored value has already been through that check, and a
    resolver that rejected it would make an old row unreadable rather than
    unwritable.
    """
    cfg = cfg or {}

    explicit = _clean(cfg.get("reasoning_mode"))
    if explicit in MODES:
        return explicit

    if "llm_select" in cfg or "reasoning_version" in cfg:
        return from_legacy_keys(
            bool(cfg.get("llm_select", llm_select_default)),
            cfg.get("reasoning_version", version_default),
        )

    default = _clean(mode_default)
    if default in MODES:
        return default
    return from_legacy_keys(bool(llm_select_default), version_default)


def uses_llm(mode: str) -> bool:
    """Whether the proposal pass and the judge run at all."""
    return _clean(mode) in _WITH_LLM


def uses_story(mode: str) -> bool:
    """Whether proposals come from anchors rather than from bare nominations."""
    return _clean(mode) in _WITH_STORY
