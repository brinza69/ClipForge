"""A transcript with holes must not pass as a whole one.

`_tolerant` stopped one bad chunk losing a 4-hour file — a real repair — and
the transcriber has reported `failed_chunks` ever since, to nobody. Nothing
downstream read it, so a transcript missing three of twenty-four chunks scored
and clipped exactly like a complete one, and every candidate drawn from the
missing stretch was drawn from silence that was not there.
"""

from __future__ import annotations

import inspect

from workers import clipper_pipeline


def test_the_handler_reads_what_the_transcriber_reports():
    """The assertion that would have caught three sessions of this: the value
    exists, is logged by the producer, and was consumed by nobody."""
    src = inspect.getsource(clipper_pipeline.handle_transcribe)
    assert "failed_chunks" in src, "handle_transcribe still ignores the gaps"
    assert "failed_chunks=failed_chunks" in src, "the gaps are not persisted"


def test_both_the_insert_and_the_update_persist_it():
    """A project transcribed twice takes the UPDATE branch. Recording the gaps
    only on first insert would lose them on exactly the re-run someone does
    BECAUSE the first attempt looked wrong."""
    src = inspect.getsource(clipper_pipeline.handle_transcribe)
    assert src.count("failed_chunks=failed_chunks or None") == 2


def test_the_stage_says_so_out_loud():
    """The progress message is the only thing a person is looking at while this
    stage runs, and it runs for ninety minutes on a 4-hour source."""
    src = inspect.getsource(clipper_pipeline.handle_transcribe)
    assert "chunk(s) missing" in src


def test_the_column_exists_on_the_model_and_in_the_migrations():
    """CLAUDE.md rule 3: both, or an existing database silently lacks it."""
    import pathlib

    from models import TranscriptModel

    assert "failed_chunks" in [c.name for c in TranscriptModel.__table__.columns]
    db = pathlib.Path(__file__).resolve().parents[1] / "database.py"
    text = db.read_text(encoding="utf-8")
    assert "ALTER TABLE transcripts ADD COLUMN" in text
    assert '"failed_chunks"' in text or "'failed_chunks'" in text
