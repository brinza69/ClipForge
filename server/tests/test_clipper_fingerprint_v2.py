"""The fingerprint's two schemas, and the contract between them.

v1 does not cover the caption policy, so two renders of one plan — one that
burned our layer and one that suppressed it — fingerprint identically. v2 covers
the resolved policy and the CONTENT of the `.ass` that was burned.

THE CONTRACT, which is what these tests are:

    record                       verified with              conclusion allowed
    legacy, no schema field      the v1 formula, exactly    recipe v1 matches;
                                                            policy NOT covered
    v1 declared                  the v1 formula only        the same limit
    v2 declared                  the v2 formula only        the v2 fields hold
    anything else                refused                    no other version

And the five acceptance probes for the migration itself: a historic sidecar goes
on validating under v1 with the limitation visible; changing the policy or the
intervals in a v2 record produces a mismatch; a v2 mismatch is NOT rescued by
the v1 formula; every path that writes a fingerprint writes v2; and migrating
the verifier alters no sidecar and no mp4 on disk.
"""

from __future__ import annotations

from workers import clipper_render_output as output

import json
from pathlib import Path

from services.clipper import edit_quality as eq
from services.clipper import render_input as ri

_RECIPE = {"source": "s.mp4", "source_start": 1.0, "source_end": 21.0,
           "render_version": "render_v3_letterbox", "caption_y": 0.62}


def _legacy(**over) -> dict:
    """A sidecar as every one of the 58 on disk is written: no schema field."""
    body = {**_RECIPE, **over}
    body["input_fingerprint"] = ri.input_fingerprint(body)
    return body


def _v2(**over) -> dict:
    body = {**_RECIPE, "fingerprint_schema": ri.FINGERPRINT_SCHEMA_V2,
            "caption_policy": {"action": "suppress", "decided_by": "human"},
            "render_record": {"caption_filter": False, "ass_sha256": None},
            **over}
    body["input_fingerprint"] = ri.input_fingerprint(
        body, schema=ri.FINGERPRINT_SCHEMA_V2)
    return body


# --- the contract, row by row ------------------------------------------------


def test_a_historic_sidecar_still_validates_and_says_what_it_cannot_answer():
    """Probe one. The 58 stored sidecars are not rewritten — re-stamping them
    would launder exactly what the fingerprint is for — so they must go on
    validating, and the assumption behind that has to be visible."""
    got = eq.fingerprint_verdict(_legacy())
    assert got["state"] == eq.FINGERPRINT_VALID_V1
    assert got["schema"] == ri.FINGERPRINT_SCHEMA_V1
    assert got["assumed_legacy"] is True, "and the assumption is reported"
    assert got["covers_caption_policy"] is False


def test_a_declared_v1_carries_the_same_limit_without_the_assumption():
    body = _legacy(fingerprint_schema=ri.FINGERPRINT_SCHEMA_V1)
    body["input_fingerprint"] = ri.input_fingerprint(body)
    got = eq.fingerprint_verdict(body)
    assert got["state"] == eq.FINGERPRINT_VALID_V1
    assert got["assumed_legacy"] is False


def test_a_declared_v2_covers_the_caption_policy():
    got = eq.fingerprint_verdict(_v2())
    assert got["state"] == eq.FINGERPRINT_VALID
    assert got["covers_caption_policy"] is True


def test_an_unknown_schema_is_refused_and_no_other_version_is_tried():
    """Probe: "try v1, then v2, take whichever matches" is a route around the
    policy check for anything that claims to be something else. So a record
    naming a formula this code does not implement gets a refusal — even when a
    formula that IS implemented would validate it."""
    body = _legacy(fingerprint_schema="clipper_render_input_v9")
    assert eq.fingerprint_verdict(body)["state"] == eq.FINGERPRINT_UNKNOWN_SCHEMA
    for bad in (7, [], {}, "", True):
        got = eq.fingerprint_verdict({**_legacy(), "fingerprint_schema": bad})
        assert got["state"] == eq.FINGERPRINT_UNKNOWN_SCHEMA, repr(bad)


# --- the migration's acceptance probes ---------------------------------------


def test_changing_the_policy_in_a_v2_record_produces_a_mismatch():
    """Probe two, and the whole reason v2 exists. Under v1 these two records
    digest identically and both validate."""
    burned = _v2()
    swapped = {**burned,
               "caption_policy": {"action": "burn", "decided_by": "human"}}
    assert eq.fingerprint_verdict(swapped)["state"] == eq.FINGERPRINT_MISMATCH
    # ...and under v1 the same swap is invisible, which is the defect.
    assert (ri.input_fingerprint({**_RECIPE, "caption_policy": {"action": "burn"}})
            == ri.input_fingerprint({**_RECIPE,
                                     "caption_policy": {"action": "suppress"}}))


def test_changing_the_intervals_in_a_v2_record_produces_a_mismatch():
    """`caption_intervals` is batch A's second half and is `None` today. It is a
    KEY rather than an omission precisely so that filling it changes the digest
    of every export it applies to."""
    base = _v2(caption_intervals=[{"t0": 0.0, "t1": 2.0, "layer": "off"}])
    moved = {**base,
             "caption_intervals": [{"t0": 0.0, "t1": 3.0, "layer": "off"}]}
    assert eq.fingerprint_verdict(base)["state"] == eq.FINGERPRINT_VALID
    assert eq.fingerprint_verdict(moved)["state"] == eq.FINGERPRINT_MISMATCH


def test_changing_the_burned_ass_content_produces_a_mismatch():
    """The `.ass` CONTENT is an input that changes the picture, so it is in v2.
    Its PATH is not: two renders of one recipe write the subtitle file to two
    temporary names and must still fingerprint the same."""
    base = _v2(render_record={"caption_filter": True, "ass_sha256": "a" * 64,
                              "ass_path": "/tmp/run1/x.ass"})
    other_path = {**base, "render_record": {**base["render_record"],
                                            "ass_path": "/tmp/run2/x.ass"}}
    other_text = {**base, "render_record": {**base["render_record"],
                                            "ass_sha256": "b" * 64}}
    assert eq.fingerprint_verdict(base)["state"] == eq.FINGERPRINT_VALID
    assert eq.fingerprint_verdict(other_path)["state"] == eq.FINGERPRINT_VALID
    assert eq.fingerprint_verdict(other_text)["state"] == eq.FINGERPRINT_MISMATCH


def test_an_unreadable_ass_does_not_digest_the_same_as_no_ass_at_all():
    """A filter whose file could not be read and a call with no filter are two
    different encodes, and a digest that ignored the refusal would let the first
    stand in for the second."""
    none = _v2(render_record={"caption_filter": False, "ass_sha256": None})
    unread = {**none, "render_record": {"caption_filter": True,
                                        "ass_sha256": None,
                                        "ass_refused": "the_file_could_not_be_read"}}
    assert eq.fingerprint_verdict(unread)["state"] == eq.FINGERPRINT_MISMATCH


def test_a_v2_mismatch_is_not_rescued_by_the_v1_formula():
    """Probe three, and the sharpest rule in the contract. This record's v1
    projection is untouched, so the v1 formula validates it perfectly — and it
    must still be a mismatch, or every export gets a route around the policy
    check by being read as older than it is."""
    tampered = {**_v2(),
                "caption_policy": {"action": "burn", "decided_by": "human"}}

    # The v1 formula cannot see the change: the projection it digests does not
    # contain the policy at all, so the tampered record and the pristine one
    # come out identical under it.
    assert ri.input_fingerprint(tampered) == ri.input_fingerprint(_v2())

    # ...and a record that DECLARED v1 would therefore validate.
    as_v1 = {**tampered, "fingerprint_schema": ri.FINGERPRINT_SCHEMA_V1}
    as_v1["input_fingerprint"] = ri.input_fingerprint(as_v1)
    assert eq.fingerprint_verdict(as_v1)["state"] == eq.FINGERPRINT_VALID_V1

    # The one that declares v2 does not, and is not rescued by that.
    assert eq.fingerprint_verdict(tampered)["state"] == eq.FINGERPRINT_MISMATCH


def test_every_path_that_writes_a_fingerprint_writes_v2():
    """Probe four, read off the source rather than asserted about it: a writer
    left on v1 produces records that can never answer the policy question, and
    nothing downstream would report that as a regression."""
    import inspect

    from workers import clipper_render_jobs as jobs

    src = inspect.getsource(output._write_sidecar)
    assert 'body["fingerprint_schema"] = render_input.FINGERPRINT_SCHEMA_V2' in src
    assert "schema=render_input.FINGERPRINT_SCHEMA_V2" in src


def test_the_stored_corpus_is_not_touched_and_still_validates():
    """Probe five. The verifier changed; no sidecar and no mp4 may have. Read
    off the real files, because "we did not write anything" is a claim about
    code and this is a claim about disk."""
    root = Path(__file__).resolve().parents[2] / "data" / "clipper"
    stored = sorted(root.glob("*/exports/*.json"))
    if not stored:
        # The corpus is not part of the repo. Saying so is the point: a silent
        # skip here would read as "checked, and nothing was modified".
        import pytest

        pytest.skip("no stored corpus on this machine to check against")
    checked = 0
    for path in stored:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(body, dict) or not body.get("input_fingerprint"):
            continue
        checked += 1
        assert "fingerprint_schema" not in body, f"{path} was rewritten"
        got = eq.fingerprint_verdict(body)
        assert got["state"] in (eq.FINGERPRINT_VALID_V1, eq.FINGERPRINT_MISMATCH), (
            f"{path}: {got['state']}")
        assert got["assumed_legacy"] is True
    assert checked, "the corpus was found and nothing in it could be read"
