"""
Unit tests for event atoms.

Atoms are the substrate everything above reasons over, so the thing worth
pinning is not the numbers but the two properties that make them usable: an
atom is a natural utterance rather than a slice of clock, and it carries the
signals for its own span.

Pure throughout — `build` takes a transcript and a signals blob and touches
nothing else, which is what lets the whole segmentation be checked without
decoding a frame.
"""

from __future__ import annotations

import pytest

from services.clipper import atoms as atoms_mod


def _words(spec):
    """[(text, start, end)] -> word dicts, one per token, evenly spread."""
    out = []
    for text, start, end in spec:
        toks = text.split()
        step = (end - start) / max(1, len(toks))
        for i, tok in enumerate(toks):
            out.append({"word": tok, "start": round(start + i * step, 3),
                        "end": round(start + (i + 1) * step - 0.02, 3),
                        "probability": 0.95})
    return out


def _transcript(spec):
    words = _words(spec)
    return {"segments": [{"start": words[0]["start"], "end": words[-1]["end"],
                          "text": " ".join(w["word"] for w in words),
                          "words": words}]}


def _signals(duration=120.0, *, peaks=(), scenes=(), loud_from=None):
    hop, n = 0.25, int(duration / 0.25) + 1
    rms = [0.2] * n
    if loud_from is not None:
        for i in range(int(loud_from / hop), n):
            rms[i] = 0.9
    return {
        "duration": duration,
        "audio": {"hop_s": hop, "rms": rms, "peaks": list(peaks),
                  "silence": [], "speech": [[0.0, duration]]},
        "peaks": list(peaks), "silence": [], "speech": [[0.0, duration]],
        "scenes": list(scenes),
        "motion": {"hop_s": 0.5, "motion": [0.3] * (int(duration / 0.5) + 1)},
    }


# ── boundaries are utterances, not clock ─────────────────────────────────────


def test_an_atom_is_a_sentence_not_a_slice_of_clock():
    """A fixed grid cuts through the middle of the one utterance that
    mattered. A sentence is the unit a speaker actually produced."""
    out = atoms_mod.build(
        _transcript([("I thought I killed him.", 0.0, 3.0),
                     ("Turns out he was fine.", 3.2, 6.0)]),
        _signals())
    assert len(out) == 2
    assert "killed" in out[0]["text"] and "fine" in out[1]["text"]


def test_every_atom_stays_inside_the_length_bounds():
    long_line = " ".join(f"word{i}" for i in range(120))
    out = atoms_mod.build(_transcript([(long_line, 0.0, 60.0)]), _signals())
    assert out
    for a in out:
        assert a["end"] - a["start"] <= atoms_mod.MAX_ATOM_S + 1e-6, a


def test_a_run_of_fragments_does_not_chain_into_one_huge_atom():
    """Letting a fragment merge past the bound is right; letting it do so
    without a ceiling produced a single 79-second atom on the real source."""
    spec = [(f"w{i}", i * 0.5, i * 0.5 + 0.3) for i in range(60)]
    out = atoms_mod.build(_transcript(spec), _signals(duration=60.0))
    assert out
    for a in out:
        assert a["end"] - a["start"] <= atoms_mod.MERGE_CEILING_S + 1e-6, a


def test_a_fragment_is_folded_into_its_neighbour_not_emitted_alone():
    out = atoms_mod.build(
        _transcript([("Yeah.", 0.0, 0.4), ("So this is the whole story.", 0.5, 5.0)]),
        _signals())
    assert all(a["end"] - a["start"] >= atoms_mod.MIN_ATOM_S - 1e-6
               or a is out[-1] for a in out)
    assert len(out) == 1, "a 0.4s fragment should not be its own atom"


def test_atoms_are_in_time_order_and_indexed():
    out = atoms_mod.build(
        _transcript([("One thing happens here.", 0.0, 4.0),
                     ("Then another thing happens.", 4.5, 8.0),
                     ("And finally this one does.", 8.5, 12.0)]), _signals())
    assert [a["i"] for a in out] == list(range(len(out)))
    assert out == sorted(out, key=lambda a: a["start"])


@pytest.mark.parametrize("transcript", [None, {}, {"segments": []}])
def test_a_source_with_no_words_yields_no_atoms(transcript):
    assert atoms_mod.build(transcript, _signals()) == []


# ── each atom carries its own evidence ───────────────────────────────────────


def test_an_atom_carries_the_signals_for_its_own_span():
    out = atoms_mod.build(
        _transcript([("Quiet bit over here first.", 0.0, 5.0),
                     ("And now the loud bit lands.", 6.0, 11.0)]),
        _signals(peaks=[7.5], scenes=[7.0], loud_from=6.0))
    quiet, loud = out[0], out[1]
    assert loud["audio"]["energy"] > quiet["audio"]["energy"] + 0.3
    assert loud["audio"]["peaks"] == 1 and quiet["audio"]["peaks"] == 0
    assert loud["visual"]["scene_change"] and not quiet["visual"]["scene_change"]


def test_a_question_and_a_laugh_are_told_apart():
    out = atoms_mod.build(
        _transcript([("Did I not block that?", 0.0, 4.0),
                     ("haha haha that was terrible.", 4.5, 8.5)]), _signals())
    kinds = [a["semantic"]["kind"] for a in out]
    assert "question" in kinds and "laughter" in kinds


def test_a_forward_looking_statement_reads_as_a_prediction():
    """The one event type worth detecting cheaply — it is what a callback
    pays off."""
    out = atoms_mod.build(
        _transcript([("I bet you I will lose this one.", 0.0, 5.0)]), _signals())
    assert out[0]["semantic"]["kind"] == "prediction"


def test_importance_is_a_usable_number_for_every_atom():
    out = atoms_mod.build(
        _transcript([("Something happens here now.", 0.0, 4.0),
                     ("haha that was amazing bro.", 5.0, 9.0)]),
        _signals(peaks=[6.0], loud_from=5.0))
    for a in out:
        assert 0.0 <= a["semantic"]["importance"] <= 1.0
    assert out[1]["semantic"]["importance"] > out[0]["semantic"]["importance"]


# ── the lines a model actually reads ─────────────────────────────────────────


def test_prompt_lines_carry_the_evidence_not_only_the_words():
    """This is what makes atoms load-bearing rather than an unread artifact."""
    out = atoms_mod.build(
        _transcript([("Nothing much happens over here.", 0.0, 5.0),
                     ("Oh my god what was that.", 6.0, 11.0)]),
        _signals(peaks=[7.5], scenes=[7.0], loud_from=6.0))
    lines = atoms_mod.to_lines(out).splitlines()
    assert len(lines) == 2
    assert "<" not in lines[0], "a quiet line should not be decorated"
    assert "LOUD" in lines[1] and "cut" in lines[1]


def test_marking_is_relative_to_the_source_not_an_absolute_bar():
    """Measured on the real source, which runs about one audio peak every
    three seconds: an absolute `peaks >= 1` tagged 94% of lines, so the marks
    became the noise they exist to cut through."""
    spec = [(f"line {i} says a thing here", i * 6.0, i * 6.0 + 5.0)
            for i in range(12)]
    # A peak inside every single atom, and two inside the last one.
    peaks = [i * 6.0 + 2.0 for i in range(12)] + [11 * 6.0 + 3.5]
    out = atoms_mod.build(_transcript(spec), _signals(duration=100.0, peaks=peaks))
    lines = atoms_mod.to_lines(out).splitlines()
    marked = [ln for ln in lines if "peak" in ln]
    assert len(marked) <= 3, (
        f"{len(marked)} of {len(lines)} lines marked on a uniformly peaky "
        f"source — the bar is absolute again"
    )


def test_prompt_lines_keep_their_timestamp():
    out = atoms_mod.build(
        _transcript([("Something is said at one hundred seconds.", 100.0, 105.0)]),
        _signals(duration=200.0))
    assert atoms_mod.to_lines(out).startswith("[100] ")


def test_the_line_budget_is_respected():
    spec = [(f"line number {i} says something", i * 5.0, i * 5.0 + 4.0)
            for i in range(60)]
    out = atoms_mod.build(_transcript(spec), _signals(duration=400.0))
    assert len(atoms_mod.to_lines(out, limit=200)) <= 200 + 80
