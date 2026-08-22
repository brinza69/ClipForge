"""Who caused a feedback event, and what that means for training.

The defect is measured, not hypothetical. On the day this was written the rig
held 69 feedback rows: 61 exports, 5 previews, 3 edits, and ZERO approve or
reject events. `training_rows()` returned 43 labelled rows, every single one
carrying label 1.0, against `ranker.MIN_TRAINING_EXAMPLES = 40`. A large share
of those exports were made by `auto_export` and by a batch script — nobody had
looked at the clips. The learned ranker was one call away from being trainable
on a set with no negative example in it, most of it its own decisions played
back as approval.

So the rule these guard is narrow and blunt: only a manual event is a verdict.
"""

from __future__ import annotations

import pytest

from services.clipper import feedback as fb


def _m(*events):
    """Events a person caused."""
    return [(e, fb.ORIGIN_MANUAL) for e in events]


# ── Only a person's action is a verdict ──────────────────────────────────────


def test_an_auto_export_is_not_approval():
    """The whole point. auto_export renders the top N the moment scoring ends;
    nobody has seen those clips."""
    assert fb.label_for_events([("exported", fb.ORIGIN_AUTO)]) is None


def test_auto_export_then_a_human_reject_is_a_reject():
    events = [("exported", fb.ORIGIN_AUTO), ("rejected", fb.ORIGIN_MANUAL)]
    assert fb.label_for_events(events) == fb.LABEL_REJECTED


def test_a_manual_export_is_still_approval():
    assert fb.label_for_events(_m("exported")) == fb.LABEL_EXPORTED


def test_a_system_event_is_not_a_verdict_either():
    assert fb.label_for_events([("exported", fb.ORIGIN_SYSTEM)]) is None


def test_a_row_from_before_the_column_existed_does_not_label():
    """NULL origin is unrecoverable, and we know for a fact that many of the
    historical exports on this rig were machine-made. Unknown is not a verdict."""
    assert fb.label_for_events([("exported", None)]) is None
    assert fb.label_for_events([("exported", ""), ("approved", None)]) is None


def test_an_unreviewed_clip_is_not_a_negative_example():
    assert fb.label_for_events([]) is None
    assert fb.label_for_events(_m("previewed", "start_changed")) is None


# ── The last verdict wins ────────────────────────────────────────────────────


def test_export_then_reject_keeps_the_correction():
    """The rule this replaced kept the 1.0 and threw the reject away, because
    "an export outranks everything". Watching a clip and changing your mind is
    exactly the signal worth having."""
    assert fb.label_for_events(_m("exported", "rejected")) == fb.LABEL_REJECTED


def test_reject_then_export_is_an_export():
    assert fb.label_for_events(_m("rejected", "exported")) == fb.LABEL_EXPORTED


def test_approve_then_reject_is_a_reject():
    assert fb.label_for_events(_m("approved", "rejected")) == fb.LABEL_REJECTED


def test_repeating_a_verdict_changes_nothing():
    assert (fb.label_for_events(_m("approved", "approved", "approved"))
            == fb.LABEL_APPROVED)


def test_non_decisive_events_do_not_displace_the_last_verdict():
    events = _m("approved", "previewed", "start_changed")
    assert fb.label_for_events(events) == fb.LABEL_APPROVED


# ── Writing ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unknown_origin_raises_rather_than_defaulting_to_manual():
    """The safe-looking fallback is the dangerous one: it files a machine's
    decision as a person's. Validated before the session is touched, which is
    why `None` is a fine session here."""
    with pytest.raises(ValueError, match="origin"):
        await fb.record(None, "clip1", "proj1", "exported", None, origin="robot")


def test_the_origins_are_a_closed_set():
    assert fb.ORIGINS == (fb.ORIGIN_MANUAL, fb.ORIGIN_AUTO, fb.ORIGIN_SYSTEM)


# ── The job carries it ───────────────────────────────────────────────────────


def test_an_unstamped_job_is_not_a_human_verdict():
    """The first version of `_job_origin` returned `manual` here, and it was
    true right up until the next piece of automation forgot to stamp its jobs —
    at which point every clip it rendered would be filed as human approval.
    A missing stamp costs a label instead of inventing one."""
    from workers.clipper_render_jobs import _job_origin

    assert _job_origin(None) == fb.ORIGIN_SYSTEM
    assert _job_origin({}) == fb.ORIGIN_SYSTEM
    assert _job_origin("not a dict") == fb.ORIGIN_SYSTEM
    assert fb.label_for_events([("exported", _job_origin(None))]) is None


def test_a_stamped_job_keeps_its_origin():
    from workers.clipper_render_jobs import _job_origin

    assert _job_origin({"origin": "auto"}) == fb.ORIGIN_AUTO
    assert _job_origin({"origin": "manual"}) == fb.ORIGIN_MANUAL
    # Garbage in the metadata must not become a made-up origin.
    assert _job_origin({"origin": "robot"}) == fb.ORIGIN_SYSTEM


def test_every_render_a_person_asked_for_is_stamped():
    """Because an unstamped job no longer counts, the two endpoints a person
    drives have to say so — otherwise real approvals stop being recorded."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routers" / "clipper_clips.py"
    enqueues = re.findall(r"job_queue\.enqueue\((.*?)\n    \)",
                          src.read_text(encoding="utf-8"), re.S)
    assert enqueues, "the parser has drifted — no enqueue calls found"
    for call in enqueues:
        assert "ORIGIN_MANUAL" in call, f"unstamped enqueue:\n{call}"


def test_record_refuses_to_guess():
    """`origin` has no default. A caller that forgets it fails at the call, not
    silently in the append-only log the ranker trains on."""
    import inspect

    origin = inspect.signature(fb.record).parameters["origin"]
    assert origin.default is inspect.Parameter.empty
    assert origin.kind is inspect.Parameter.KEYWORD_ONLY
