"""Batch R2: which editing grammar a clip gets, and how sure we are.

The rule the whole module turns on is that a weak classification buys a SAFER
edit, never a bolder one — and that "low" and "never measured" are different
answers. Most of what is pinned here is that difference, because the failure it
prevents is a guess about the content type turning into an aggressive cut
pattern the source cannot support.

The pace bands themselves are deliberately NOT asserted against anything. They
are chosen guardrails, not measurements, and a test that treated them as facts
would make them look calibrated.
"""

from __future__ import annotations

import pytest

from routers.clipper_settings import _default_settings, _normalise_settings
from services.clipper import edit_profiles as ep
from services.clipper.content_geom import CONTENT_TYPES


@pytest.mark.parametrize("content_type", CONTENT_TYPES)
def test_every_content_type_the_classifier_can_emit_has_a_profile(content_type):
    """All ten of them, including `unknown`. A type with no home would fall to
    whatever the lookup's default happened to be, silently."""
    resolved = ep.resolve(content_type, 0.9)
    assert resolved["profile"] in ep.PROFILES
    assert resolved["reason"] in ep.REASONS


def test_the_mapping_covers_the_classifier_exactly():
    """No type the classifier can emit is missing, and none is invented here.
    A second list would drift from `content_geom.CONTENT_TYPES` the first time
    either changed."""
    assert set(ep.PROFILE_FOR_TYPE) == set(CONTENT_TYPES)
    assert set(ep.PROFILE_FOR_TYPE.values()) <= set(ep.PROFILES)


def test_every_profile_only_names_regimes_that_exist():
    """The regime list is closed. A profile naming one outside it would ask R3
    for a grammar nobody defined."""
    for name, spec in ep.PROFILES.items():
        assert set(spec["regimes"]) <= set(ep.REGIMES), name


def test_a_confident_classification_gets_its_own_profile():
    assert ep.resolve("gaming", 0.9)["profile"] == "action"
    assert ep.resolve("tutorial", 0.8)["profile"] == "instructional"
    assert ep.resolve("podcast", 0.7)["profile"] == "conversation"
    assert ep.resolve("irl", 0.6)["profile"] == "exploration"
    assert ep.resolve("commentary", 0.9)["profile"] == "talking_head"


def test_a_weak_classification_buys_a_safer_edit_not_a_bolder_one():
    weak = ep.resolve("gaming", ep.CONFIDENCE_FLOOR - 0.01)
    assert weak["profile"] == ep.CONSERVATIVE
    assert weak["reason"] == ep.BY_LOW_CONFIDENCE
    # The number survives: "measured, and low" is a fact worth keeping.
    assert weak["confidence"] == pytest.approx(ep.CONFIDENCE_FLOOR - 0.01)


def test_a_missing_confidence_is_not_a_low_one():
    """Two different answers, and the report must be able to tell them apart.
    Reconstructing a number here — from the source's score, or from the label
    itself — would be indistinguishable downstream from having measured it."""
    missing = ep.resolve("gaming", None)
    assert missing["profile"] == ep.CONSERVATIVE
    assert missing["reason"] == ep.BY_MISSING_CONFIDENCE
    assert missing["confidence"] is None


def test_an_unknown_or_unrecognised_type_is_conservative():
    for value in ("unknown", "", None, "banana", 7):
        resolved = ep.resolve(value, 0.99)
        assert resolved["profile"] == ep.CONSERVATIVE, value
        assert resolved["reason"] == ep.BY_UNKNOWN_TYPE, value


def test_a_persons_override_is_trusted_without_a_number():
    """The provenance IS the evidence. Demanding a confidence a human never
    produced would quietly downgrade every overridden project to conservative,
    which is the opposite of what the override was for."""
    resolved = ep.resolve("podcast", None, origin="override")
    assert resolved["profile"] == "conversation"
    assert resolved["reason"] == ep.BY_OVERRIDE
    assert resolved["confidence"] is None
    assert resolved["origin"] == "override"


def test_an_override_of_unknown_is_still_conservative():
    """A person saying "I don't know" is not a licence to cut harder."""
    assert ep.resolve("unknown", None, origin="override")["profile"] == ep.CONSERVATIVE


def test_a_confidence_that_is_not_a_number_is_absent_not_zero():
    """0.0 reads as "measured, and low". These were never measured."""
    for value in (float("nan"), float("inf"), "high", True, [0.9]):
        assert ep.resolve("gaming", value)["confidence"] is None, value


def test_an_out_of_range_confidence_is_reported_not_clamped():
    """A classifier emitting 1.7 has a bug worth seeing."""
    assert ep.resolve("gaming", 1.7)["confidence"] == pytest.approx(1.7)


# --- the mode ----------------------------------------------------------------


def test_only_the_implemented_modes_are_selectable():
    assert ep.CONTENT_AWARE in ep.MODES
    assert ep.CONTENT_AWARE not in ep.SELECTABLE
    assert set(ep.SELECTABLE) < set(ep.MODES)


def test_the_mode_falls_back_rather_than_raising_in_the_worker():
    """The API refuses a bad mode at the door. By the time the worker reads the
    settings the only useful behaviour is to keep rendering."""
    assert ep.resolve_mode({"edit_mode": "nonsense"}) == ep.DEFAULT_MODE
    assert ep.resolve_mode(None) == ep.DEFAULT_MODE
    assert ep.resolve_mode({}, default=ep.CONTENT_AWARE_SHADOW) == ep.CONTENT_AWARE_SHADOW
    assert ep.resolve_mode({"edit_mode": ep.LEGACY_DYNAMIC},
                           default=ep.CONTENT_AWARE_SHADOW) == ep.LEGACY_DYNAMIC


def test_nothing_delivers_the_profile_while_no_grammar_is_connected():
    """The availability check lives in `delivers_profile`, not in its callers.
    Answering only for the mode it was handed made this true for
    `content_aware` while nothing was connected to it — correct in today's one
    call site, and one careless caller away from being wrong again.

    All three are false today. The day the final gate adds `CONTENT_AWARE` to
    `SELECTABLE`, the third becomes true on its own: no second switch."""
    assert not ep.delivers_profile(ep.LEGACY_DYNAMIC)
    assert not ep.delivers_profile(ep.CONTENT_AWARE_SHADOW)
    assert not ep.delivers_profile(ep.CONTENT_AWARE)
    assert ep.CONTENT_AWARE not in ep.SELECTABLE


# --- the settings contract ---------------------------------------------------


def test_an_unknown_edit_mode_is_refused_not_replaced():
    """Refused rather than clamped, like the reasoning mode: a setting that
    reads back as something other than what was asked for is the defect that
    made the story engine unreachable from the API for four months."""
    with pytest.raises(Exception) as caught:
        _normalise_settings({"edit_mode": "cinematic"})
    assert caught.value.detail["error"] == "edit_mode_invalid"


def test_a_known_but_unavailable_mode_says_so_distinctly():
    """"Not yet" and "no such thing" are different answers and a client should
    be able to tell them apart."""
    with pytest.raises(Exception) as caught:
        _normalise_settings({"edit_mode": ep.CONTENT_AWARE})
    assert caught.value.detail["error"] == "edit_mode_unavailable"


def test_a_project_saved_before_the_key_existed_keeps_the_rigs_choice():
    """PATCH sends the whole settings dict. Without resolving against the rig
    default, an unrelated edit — a clip count — would move such a project onto
    a mode nobody chose for it."""
    out = _normalise_settings({"clip_count": 7})
    assert out["edit_mode"] == _default_settings()["edit_mode"]


def test_the_mode_the_user_picked_survives_normalisation():
    """Only normalisation. The round trip over HTTP and the DB is
    `test_clipper_api.test_the_edit_mode_survives_a_real_round_trip`, and it is
    a different question — this one cannot see a PATCH that reverts."""
    out = _normalise_settings({"edit_mode": ep.CONTENT_AWARE_SHADOW})
    assert out["edit_mode"] == ep.CONTENT_AWARE_SHADOW


# --- the verdict that reaches the clip ---------------------------------------


def test_the_confidence_travels_with_the_label():
    """`type_at` answers the same question and throws the confidence away,
    which was fine while nothing read it. R2 does."""
    from services.clipper import segment_type

    segments = [{"start": 0.0, "end": 10.0, "content_type": "gaming", "confidence": 0.82}]
    verdict = segment_type.verdict_at(segments, 5.0, "podcast")
    assert verdict == {"content_type": "gaming", "confidence": 0.82,
                       "origin": segment_type.FROM_SEGMENT}


def test_the_sources_own_profile_is_an_answer_but_not_a_measurement():
    """A stretch too short to classify is better scored by the source's overall
    profile than by nothing — but filling in the source's confidence here would
    be indistinguishable downstream from having measured this stretch."""
    from services.clipper import segment_type

    verdict = segment_type.verdict_at([], 5.0, "podcast")
    assert verdict["content_type"] == "podcast"
    assert verdict["confidence"] is None
    assert verdict["origin"] == segment_type.FROM_SOURCE
    # And that is exactly what makes it conservative, rather than a confident
    # `conversation` nobody measured.
    assert ep.resolve(verdict["content_type"], verdict["confidence"],
                      verdict["origin"])["profile"] == ep.CONSERVATIVE


def test_the_shadow_records_the_profile_without_delivering_it():
    """R2's gate, as a property of the resolver: in shadow the profile is fully
    resolved and observable, and `applied` is false — so nothing about the
    render can depend on it yet."""
    resolved = ep.resolve("gaming", 0.9)
    assert resolved["profile"] == "action"
    assert not ep.delivers_profile(ep.CONTENT_AWARE_SHADOW)


def test_the_export_records_the_profile_on_every_render():
    """Written to the sidecar whatever the mode, because a profile that is only
    recorded when it is used cannot be compared against the edit that shipped
    without it."""
    import inspect

    from workers import clipper_render_jobs as jobs
    from workers import clipper_render_plan as plan

    assert '"edit_profile": decision["edit_profile"]' in inspect.getsource(jobs.handle_export)
    assert '"edit_profile"' in inspect.getsource(plan._decide_render)


def test_the_profile_is_not_part_of_the_render_fingerprint():
    """The fingerprint covers what changes the picture. In shadow this changes
    nothing, and adding it would invalidate every stamped export for a field
    with no effect. The batch that makes the profile apply adds it."""
    from services.clipper import render_input

    assert "edit_profile" not in render_input.FINGERPRINT_KEYS


# --- the hole the central rule had -------------------------------------------


def test_an_impossible_confidence_refuses_the_profile_and_keeps_the_number():
    """The rule was "a weak classification buys a safer edit". An IMPOSSIBLE one
    bought the boldest: `resolve("gaming", 1.7)` came back `action`, at 12-28
    cuts a minute. The number is kept for diagnosis and the grammar is not."""
    for value in (1.7, -0.2, 100.0):
        resolved = ep.resolve("gaming", value)
        assert resolved["profile"] == ep.CONSERVATIVE, value
        assert resolved["reason"] == ep.BY_INVALID_CONFIDENCE, value
        assert resolved["confidence"] == pytest.approx(value), value


def test_the_boundaries_of_the_range_are_still_measurements():
    assert ep.resolve("gaming", 1.0)["profile"] == "action"
    assert ep.resolve("gaming", 0.0)["reason"] == ep.BY_LOW_CONFIDENCE


def test_the_worker_and_the_router_share_one_availability_resolver():
    """config.py and a hand-edited settings row are two more doors into this
    setting; the router's refusal only guards the third. A rig set to
    `content_aware` produced `applied: true` on every render."""
    assert ep.available_mode({}, default=ep.CONTENT_AWARE) == ep.DEFAULT_MODE
    assert ep.available_mode({"edit_mode": ep.CONTENT_AWARE}) == ep.DEFAULT_MODE
    assert not ep.delivers_profile(ep.available_mode({}, default=ep.CONTENT_AWARE))
    # And it still passes through a mode that IS available.
    assert ep.available_mode({}, default=ep.CONTENT_AWARE_SHADOW) == ep.CONTENT_AWARE_SHADOW


def test_a_confidence_that_cannot_reach_a_float_column_is_coerced_at_the_boundary():
    """`content_confidence` is a Float. A classifier handing back "high" or
    [0.9] raises on the way in and takes the whole board's clips with it, for a
    diagnostic field nothing depends on."""
    import inspect

    from workers import clipper_finalize

    src = inspect.getsource(clipper_finalize)
    assert "edit_profiles.confidence_value(" in src
    for bad in ("high", [0.9], object()):
        assert ep.confidence_value(bad) is None, bad
    assert ep.confidence_value("0.8") == pytest.approx(0.8)


def test_the_resolved_profile_is_serialized_for_the_ui():
    """The plan asks for the profile and the reason to be visible. The backend
    resolves it; the browser must not own a second copy of the mapping."""
    import inspect

    from services.clipper import serialize

    src = inspect.getsource(serialize.clip_to_dict)
    for field in ('"content_confidence"', '"content_type_origin"', '"edit_profile"'):
        assert field in src, field
