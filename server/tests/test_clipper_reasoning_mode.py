"""The one switch that decides which reasoning engine runs.

The defect these guard is not hypothetical. `_default_settings()` listed neither
`llm_select` nor `reasoning_version`, and `_normalise_settings` keeps only keys
already in that dict — so both were discarded, in silence, on every create and
every patch. The story engine could not be turned on from the UI or the API at
all. Two projects on this rig have it on and got there by direct DB writes.

So the tests that matter are the round trip (the setting survives the API) and
the compatibility mapping (133 projects that predate the setting keep the
behaviour they had).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.clipper import reasoning_mode as rm


# ── The mapping ──────────────────────────────────────────────────────────────


def test_the_old_pair_maps_onto_a_mode():
    assert rm.from_legacy_keys(False, "legacy") == rm.LEGACY
    assert rm.from_legacy_keys(False, "story_v1") == rm.LEGACY, (
        "reasoning_version alone never did anything — llm_select gated it"
    )
    assert rm.from_legacy_keys(True, "story_v1") == rm.STORY_V1
    assert rm.from_legacy_keys(True, "legacy") == rm.LLM_NOMINATE
    assert rm.from_legacy_keys(True, None) == rm.LLM_NOMINATE


def test_llm_select_without_story_keeps_its_own_name():
    """The reachable state the four-value contract could not express.

    Folding it into `legacy` would switch off both the nomination pass and the
    judge for anyone who had them on.
    """
    mode = rm.from_legacy_keys(True, "legacy")
    assert mode != rm.LEGACY
    assert rm.uses_llm(mode) and not rm.uses_story(mode)


def test_an_explicit_mode_wins_over_the_old_keys():
    cfg = {"reasoning_mode": "legacy", "llm_select": True,
           "reasoning_version": "story_v1"}
    assert rm.resolve(cfg) == rm.LEGACY


def test_a_project_carrying_only_the_old_keys_resolves_to_what_it_meant():
    """Exactly the two rows on this rig: 4-5 keys, written straight to the DB."""
    cfg = {"clip_count": 8, "llm_select": True, "reasoning_version": "story_v1"}
    assert rm.resolve(cfg) == rm.STORY_V1


def test_a_key_the_project_does_not_carry_falls_through_to_the_rig():
    """`cfg.get(key, default)` is what the worker did before this module."""
    cfg = {"reasoning_version": "story_v1"}
    assert rm.resolve(cfg, llm_select_default=True) == rm.STORY_V1
    assert rm.resolve(cfg, llm_select_default=False) == rm.LEGACY


def test_a_project_with_no_reasoning_keys_uses_the_rig_default():
    assert rm.resolve({"clip_count": 8}) == rm.LEGACY
    assert rm.resolve({}, mode_default="story_v1") == rm.STORY_V1
    assert rm.resolve(None, llm_select_default=True,
                      version_default="story_v1") == rm.STORY_V1


def test_garbage_never_raises_and_never_returns_an_unknown_mode():
    for cfg in ({"reasoning_mode": "banana"}, {"reasoning_mode": 7},
                {"reasoning_mode": None}, {"reasoning_mode": ""}):
        assert rm.resolve(cfg) in rm.MODES


def test_resolve_reads_story_v2_even_though_the_api_refuses_to_write_it():
    """A stored value has already been through validation. A resolver that
    rejected it would make an old row unreadable rather than unwritable."""
    assert rm.resolve({"reasoning_mode": "story_v2"}) == rm.STORY_V2


def test_the_gates_agree_with_the_mode_list():
    assert not rm.uses_llm(rm.LEGACY) and not rm.uses_story(rm.LEGACY)
    for mode in (rm.STORY_V1, rm.STORY_V2_SHADOW, rm.STORY_V2):
        assert rm.uses_llm(mode) and rm.uses_story(mode)
    assert set(rm.SELECTABLE) < set(rm.MODES)


def test_no_mode_is_offered_before_it_behaves_the_way_its_name_says():
    """A mode is selectable only once the pipeline implements it.

    `story_v2_shadow` promises "v2 writes its artefacts, legacy still orders the
    board". It was refused until 2026-08-22 because the second half did not
    exist: the mode ran the story engine AND the judge and reordered the user's
    board, which is the opposite of what it says. `selection.board` now computes
    the v2 ordering and leaves the legacy order shipping, so it is offered.

    `story_v2` is still refused, and for a different reason: the rule WORKS, it
    has simply never been compared against legacy on a corpus. That comparison
    is the only step that could show the change is an improvement rather than a
    difference, and skipping it is what this assertion prevents.
    """
    assert rm.STORY_V2 not in rm.SELECTABLE
    assert rm.STORY_V2_SHADOW in rm.SELECTABLE


# ── The round trip through the API ───────────────────────────────────────────


def _normalise(raw):
    from routers.clipper import _normalise_settings

    return _normalise_settings(raw)


def test_the_setting_survives_normalisation():
    """The whole point of Batch 1. Before it, this key came back missing."""
    out = _normalise({"reasoning_mode": "story_v1"})
    assert out["reasoning_mode"] == "story_v1"


def test_it_is_present_even_when_the_client_says_nothing():
    assert _normalise({})["reasoning_mode"] in rm.SELECTABLE
    assert _normalise(None)["reasoning_mode"] in rm.SELECTABLE


def test_a_patch_does_not_switch_the_engine_off_behind_the_users_back():
    """PATCH sends the WHOLE settings dict, and a project written before this
    key carries the pair it replaced. Those two keys are not in the defaults,
    so the merge drops them — and writing a bare default on top would read as
    if legacy had been CHOSEN. The two story projects on this rig would lose
    story_v1 the first time anyone changed their clip count."""
    out = _normalise({"clip_count": 5, "llm_select": True,
                      "reasoning_version": "story_v1"})
    assert out["reasoning_mode"] == "story_v1"


def test_an_explicit_choice_still_beats_the_old_keys_on_patch():
    out = _normalise({"reasoning_mode": "legacy", "llm_select": True,
                      "reasoning_version": "story_v1"})
    assert out["reasoning_mode"] == "legacy"


def test_shadow_is_accepted_now_that_it_leaves_the_board_alone():
    """Refused until 2026-08-22, when `selection.board` gained the shadow path.
    A mode is offered exactly when it does what its name says."""
    assert _normalise({"reasoning_mode": "story_v2_shadow"})["reasoning_mode"] \
        == "story_v2_shadow"


def test_v2_itself_is_still_not_yet_rather_than_no_such_thing():
    """"Not yet" and "no such thing" are different answers, and a client that
    spelled a real mode correctly should be told which one it got."""
    with pytest.raises(HTTPException) as caught:
        _normalise({"reasoning_mode": "story_v2"})
    assert caught.value.detail["error"] == "reasoning_mode_unavailable"


def test_a_refusal_only_points_at_a_mode_that_can_be_used():
    """The story_v2 message pointed at story_v2_shadow while shadow was itself
    refused — advice that could not be followed. Now that shadow is selectable
    the same sentence is useful again, and this is what keeps it honest if
    either one is gated later."""
    import re

    from routers.clipper import _NOT_AVAILABLE_YET

    # Whole words. `"story_v2" in "...run story_v2_shadow first..."` is True and
    # means nothing — the same substring trap the quote matcher fell into.
    for detail in _NOT_AVAILABLE_YET.values():
        for blocked in _NOT_AVAILABLE_YET:
            named = re.search(rf"{re.escape(blocked)}(?!_)", detail)
            assert not named or blocked in rm.SELECTABLE


def test_the_rig_cannot_grant_itself_a_mode_the_api_refuses(monkeypatch):
    """config.py is the second door into this setting. Without the clamp, an
    operator who set an unimplemented mode there put EVERY new project in it —
    the gate refused to the browser and granted to the machine."""
    from config import settings as cfg
    from routers.clipper import _default_settings

    monkeypatch.setattr(cfg, "clipper_reasoning_mode", "story_v2")
    assert _default_settings()["reasoning_mode"] == rm.LEGACY
    assert _normalise({"clip_count": 8})["reasoning_mode"] == rm.LEGACY


def test_a_rig_configured_with_an_available_mode_is_left_alone(monkeypatch):
    from config import settings as cfg
    from routers.clipper import _default_settings

    monkeypatch.setattr(cfg, "clipper_reasoning_mode", "story_v1")
    assert _default_settings()["reasoning_mode"] == rm.STORY_V1
    # ...and a project that says nothing inherits it, rather than being pinned
    # to legacy by a default the operator did not choose.
    assert _normalise({"clip_count": 8})["reasoning_mode"] == rm.STORY_V1


def test_story_v2_is_refused_rather_than_downgraded():
    """Not redirected to shadow. A setting that reads back as something other
    than what it asked for is the defect this replaced."""
    with pytest.raises(HTTPException) as caught:
        _normalise({"reasoning_mode": "story_v2"})
    assert caught.value.status_code == 400
    assert caught.value.detail["error"] == "reasoning_mode_unavailable"


def test_an_unknown_mode_is_refused_too():
    with pytest.raises(HTTPException) as caught:
        _normalise({"reasoning_mode": "story_v9"})
    assert caught.value.status_code == 400
    assert caught.value.detail["error"] == "reasoning_mode_invalid"


def test_case_and_whitespace_do_not_decide_which_engine_runs():
    assert _normalise({"reasoning_mode": "  STORY_V1 "})["reasoning_mode"] == "story_v1"


# ── The worker reads the same answer ─────────────────────────────────────────


def test_the_worker_resolves_what_the_api_stored():
    from workers.clipper_build import _reasoning_mode

    stored = _normalise({"reasoning_mode": "story_v1"})
    assert _reasoning_mode(stored) == rm.STORY_V1


def test_the_worker_still_understands_a_project_written_before_batch_1():
    from workers.clipper_build import _reasoning_mode

    assert _reasoning_mode({"llm_select": True,
                            "reasoning_version": "story_v1"}) == rm.STORY_V1
    assert _reasoning_mode({"llm_select": False}) == rm.LEGACY
    assert _reasoning_mode({"clip_count": 8}) == rm.LEGACY


def test_the_anchor_cache_is_not_invalidated_by_the_rename():
    """The stamp decides whether a previous run's anchors can be reused. For
    the two story projects it read "story_v1" before this change, and it has to
    keep reading "story_v1" — otherwise switching the switch silently throws
    away hours of model output."""
    from workers.clipper_build import _anchor_stamp

    old_shape = {"llm_select": True, "reasoning_version": "story_v1"}
    assert _anchor_stamp(old_shape, 100.0)["reasoning"] == "story_v1"
