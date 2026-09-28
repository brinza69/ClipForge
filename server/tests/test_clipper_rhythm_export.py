"""Batch R4 on the wire: the proposal is computed, recorded, and inert.

Grepping the source proves a line exists, not that it runs. These execute the
decision the export path actually takes and look at what would be written beside
the mp4.

The case worth naming is the last one. `_dynamic_plan` is the only place the
scene list and the audio peaks exist, so the boundaries are built there and
ride to the report on the plan. Whether the source was SCANNED has to survive
that ride: a signals artefact with no `scenes` key is an unmeasured source, and
reporting it as a source that cut nowhere would turn "nobody looked" into
evidence — which is the mistake the last three batches are mostly made of.
"""

from __future__ import annotations

from services.clipper import dynamic_rhythm, edit_profiles
from workers import clipper_render_jobs as jobs
from workers import clipper_render_output as output

from tests.test_clipper_dynamic_export import _Clip, _Project, _decide_in, wired

__all__ = ["wired"]        # re-exported so pytest resolves the fixture here


async def test_the_rhythm_view_reaches_the_export(wired, monkeypatch, tmp_path):
    decision = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                                monkeypatch, tmp_path)
    view = decision["rhythm_view"]
    assert view["schema"] == "rhythm_view_v1"
    assert view["available"] is True
    assert view["applied"] is False
    # The clip is `gaming` with no confidence, so R2 resolves it conservative —
    # and R4 reads the profile it was handed rather than the content type.
    assert view["profile"] == edit_profiles.CONSERVATIVE
    assert view["admits"] == list(dynamic_rhythm.ADMITS[edit_profiles.CONSERVATIVE])
    # Two shots, so one delivered cut, against however many the proposal earns.
    assert view["legacy_cuts"] == len(decision["dyn"]["shots"]) - 1
    assert view["duration_s"] == 30.0


async def test_a_source_nobody_scanned_says_so_end_to_end(wired, monkeypatch,
                                                          tmp_path):
    """The fixture's signals artefact carries no `scenes` key and no audio, so
    both signals arrive unknown. `False` here is the report refusing to claim a
    scan that never happened — not a scan that found nothing."""
    decision = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                                monkeypatch, tmp_path)
    view = decision["rhythm_view"]
    assert view["scenes_known"] is False
    assert view["beats_known"] is False


async def test_the_boundaries_are_built_where_the_signals_are(wired, monkeypatch,
                                                              tmp_path):
    """Words, scene cuts and audio peaks only exist inside `_dynamic_plan`, so
    the boundary list is computed there and carried. If it were rebuilt in the
    report it would use a different style and compare the proposal against a
    cadence nobody rendered."""
    monkeypatch.setattr(jobs.storage, "read_artifact", lambda _pid, _name: {
        "proxy_width": 480, "proxy_height": 270,
        # Absolute, on the source clock: the clip starts at 100.0.
        "scenes": [104.0, 118.0],
        "audio": {"peaks": [103.0, 111.0], "rms": [0.4] * 200, "hop_s": 0.25},
    })
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face", "t0": 0.0, "t1": 2.0, "composition": "crop",
                   "rect": {"x": 0, "y": 0, "w": 540, "h": 960}, "anchor": [270, 480]},
                  {"camera": "game", "t0": 2.0, "t1": 4.0, "composition": "crop",
                   "rect": {"x": 700, "y": 0, "w": 606, "h": 1080},
                   "anchor": [1003, 540]}],
        "warnings": [], "style": {}, "hits": [3.0, 11.0]})

    plan = await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080)
    rhythm = plan["_rhythm"]
    # Clip-relative, and only what falls inside the window.
    assert rhythm["scenes"] == [4.0, 18.0]
    assert rhythm["beats_known"] is True
    # A scene cut is a place as well as a reason, so it is in the boundary list
    # too — at the weight the delivered planner already gives it.
    assert {4.0, 18.0} <= {round(t, 3) for t, _w in rhythm["boundaries"]}


async def test_the_working_key_never_becomes_a_deliverable(wired, monkeypatch,
                                                           tmp_path):
    """`_rhythm` carries a boundary list dozens of entries long. It belongs to
    the decision, not to the sidecar, and the export pops it with the rest of
    the working data."""
    import inspect

    decision = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                                monkeypatch, tmp_path)
    assert "_rhythm" in decision["dyn"], "it has to get there to be popped"
    assert '"_rhythm"' in inspect.getsource(output.render_export)


async def test_the_proposal_is_not_part_of_the_render_fingerprint(
        wired, monkeypatch, tmp_path):
    """The fingerprint covers what changes the picture. In shadow this changes
    nothing, and adding it would invalidate every stamped export for a field
    with no effect."""
    from services.clipper import render_input

    assert "rhythm_view" not in render_input.FINGERPRINT_KEYS
    legacy = await _decide_in(edit_profiles.LEGACY_DYNAMIC, wired, monkeypatch,
                              tmp_path)
    shadow = await _decide_in(edit_profiles.CONTENT_AWARE_SHADOW, wired,
                              monkeypatch, tmp_path)
    assert legacy["dyn"] == shadow["dyn"]
    assert legacy["rhythm_view"] == shadow["rhythm_view"]
