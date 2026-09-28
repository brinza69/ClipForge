"""What the render produced, and the half `input_fingerprint` could not reach.

`FINGERPRINT_KEYS`' own comment recorded the gap: the output size "is decided by
defaults inside the two renderers with no shared authority to read it from, so
it is absent here until one exists". The delivered file is that authority. Two
renderers can disagree about what they meant to produce and cannot disagree
about what came out — so this measures the artefact instead of agreeing a
constant, which is the move that settled the caption position and the
composition count earlier in this batch.
"""

from __future__ import annotations

import subprocess

import pytest

from services.clipper import output_identity as oid
from services.clipper import publish_checks as pc
from services.clipper import publish_preflight as pf


def _mp4(path, *, seconds: float = 1.0, size: str = "320x240") -> str:
    """A real, tiny mp4. A fake one would test the refusal path, not this."""
    out = str(path)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=black:s={size}:d={seconds}", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", out, "-y"],
        check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    return _mp4(tmp_path_factory.mktemp("render") / "clip.mp4")


# --- what it measures --------------------------------------------------------


def test_it_measures_the_file_rather_than_declaring_a_size(rendered):
    got = oid.probe(rendered)
    assert got["refused"] is None
    assert got["width"] == 320 and got["height"] == 240
    assert got["bytes"] > 0 and len(got["sha256"]) == 64
    assert got["duration_s"] > 0


def test_the_same_file_matches_and_a_different_one_does_not(tmp_path, rendered):
    recorded = oid.probe(rendered)
    assert oid.matches(recorded, rendered) == (True, None)

    other = _mp4(tmp_path / "other.mp4", seconds=2.0)
    same, why = oid.matches(recorded, other)
    assert same is False and "not_the_one" in why


def test_an_identical_re_encode_still_matches_and_that_is_the_right_answer(
        tmp_path, rendered):
    """MEASURED, and it contradicted the assumption this test was written on.

    I expected a re-encode of the same plan to produce a different file, on the
    grounds that encoders vary. `libx264` on this rig is deterministic: the same
    input and the same flags give byte-identical output, so the digest matches.

    That is the behaviour to want. The digest identifies CONTENT, not the encode
    event — so an idempotent re-render does not spuriously invalidate a sidecar,
    while any file that is actually different still fails. Size-and-mtime, which
    the OCR cache uses for a different question, would have called this a
    mismatch and forced a re-measurement of an identical artefact.
    """
    again = _mp4(tmp_path / "again.mp4")
    assert oid.matches(oid.probe(rendered), again) == (True, None)
    assert oid.probe(again)["sha256"] == oid.probe(rendered)["sha256"]


# --- refusals, never guesses -------------------------------------------------


def test_a_missing_or_empty_file_is_refused_not_recorded_as_zero(tmp_path):
    gone = oid.probe(tmp_path / "nothing.mp4")
    assert gone["refused"] == oid.NO_FILE and "sha256" not in gone

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    got = oid.probe(empty)
    # A record of zeroes would satisfy every check that only asks whether the
    # field is present, and the digest of nothing is the same for every empty
    # export ever written.
    assert got["refused"] == oid.EMPTY_FILE and "sha256" not in got


def test_a_file_with_no_video_stream_is_refused(tmp_path):
    text = tmp_path / "notvideo.mp4"
    text.write_bytes(b"this is not a video" * 100)
    assert oid.probe(text)["refused"] == oid.NO_VIDEO


def test_a_comparison_nobody_could_make_is_not_a_match(tmp_path, rendered):
    """THREE ANSWERS, NOT TWO. Only an actual digest disagreement is `False`."""
    for recorded in (None, {}, {"schema": "something_else"},
                     {"schema": oid.SCHEMA, "refused": oid.NO_FILE},
                     {"schema": oid.SCHEMA, "sha256": ""}):
        same, why = oid.matches(recorded, rendered)
        assert same is None and why, repr(recorded)

    same, why = oid.matches(oid.probe(rendered), tmp_path / "gone.mp4")
    assert same is None and "file_now" in why


def test_probe_never_raises(tmp_path):
    for bad in (None, 7, b"", [], tmp_path):
        assert oid.probe(bad)["refused"] in oid.REFUSALS, repr(bad)


# --- and what it unlocks -----------------------------------------------------


def _recipe(**over) -> dict:
    """A sidecar as the export path writes one TODAY: schema declared, digest
    taken under it. Without the declaration these records are read as legacy v1
    and stop at that gate, which is a different finding from the one each test
    below is about."""
    from services.clipper.render_input import (FINGERPRINT_SCHEMA_V2,
                                               input_fingerprint)

    body = {"source": "s.mp4", "source_start": 1.0, "source_end": 21.0,
            "render_version": "render_v3_letterbox",
            "fingerprint_schema": FINGERPRINT_SCHEMA_V2, **over}
    body["input_fingerprint"] = input_fingerprint(
        body, schema=FINGERPRINT_SCHEMA_V2)
    return body


def test_provenance_still_cannot_pass_on_the_recipe_alone(rendered):
    """A valid digest says the plan was not edited after the render. It says
    nothing about the mp4, and for the whole of this batch that left the check
    with no route to a pass at all."""
    got = pc.provenance(_recipe(), export_path=rendered)
    assert got["state"] == pf.UNAVAILABLE
    assert "does_not_describe_the_delivered_file" in got["why"]


def test_provenance_passes_when_both_halves_hold(rendered):
    body = _recipe()
    body["output_identity"] = oid.probe(rendered)
    got = pc.provenance(body, export_path=rendered)
    assert got["state"] == pf.PASS
    assert got["evidence"]["file_matches"] is True


def test_a_swapped_file_is_rejectable(tmp_path, rendered):
    body = _recipe()
    body["output_identity"] = oid.probe(rendered)
    got = pc.provenance(body, export_path=_mp4(tmp_path / "swap.mp4", seconds=2.0))
    assert got["state"] == pf.FAIL and got["severity"] == pf.REJECTABLE


def test_a_record_with_no_file_offered_is_unavailable_not_a_pass(rendered):
    """The record exists and nobody offered the file to check it against. That
    is a caller that did not ask, not a clip that passed."""
    body = _recipe()
    body["output_identity"] = oid.probe(rendered)
    got = pc.provenance(body)
    assert got["state"] == pf.UNAVAILABLE
    assert "not_offered_for_comparison" in got["why"]


def test_a_refused_identity_does_not_become_a_pass(rendered, tmp_path):
    body = _recipe()
    body["output_identity"] = oid.probe(tmp_path / "never_written.mp4")
    got = pc.provenance(body, export_path=rendered)
    assert got["state"] == pf.UNAVAILABLE
    assert "refused" in got["why"]
