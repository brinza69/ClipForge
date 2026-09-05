"""What was actually sent to ffmpeg, read off the call that ran.

THE THING THIS SEPARATES. `caption_policy` says what was decided,
`output_identity` says what came out, and neither can say whether the encode
that ran carried a caption filter. The corpus already has the two coming apart:
15 exports were rendered under a `suppress` decision with an `.ass` from an
earlier run still on disk beside them, so every check that read the FILE instead
of the CALL got the wrong answer.
"""

from __future__ import annotations

from pathlib import Path

from services.clipper import render_record as rr
from services.clipper.ffmpeg_tools import escape_filter_path


def _escaped(path) -> str:
    """The renderer's own escaping. A test that re-spells it here compares this
    module against a second implementation of the thing it has to match."""
    return escape_filter_path(path)


def _argv(*extra: str) -> list[str]:
    return ["ffmpeg", "-y", "-i", "in.mp4", "-filter_complex", *extra,
            "-c:v", "libx264", "out.mp4"]


# --- the filter, read off the command ---------------------------------------


def test_a_call_with_no_subtitle_filter_says_so(tmp_path):
    got = rr.record(_argv("crop=810:1440,scale=1080:1920"))
    assert got["caption_filter"] is False
    assert got["ass_path"] is None and got["ass_sha256"] is None


def test_a_file_handed_over_and_not_used_is_the_finding(tmp_path):
    """The whole reason the record is taken from the argv. A caller passing an
    `.ass` proves an intention; the filtergraph proves the encode."""
    ass = tmp_path / "x.ass"
    ass.write_text("[Events]\n", encoding="utf-8")
    got = rr.record(_argv("crop=810:1440"), ass_path=str(ass))
    assert got["caption_filter"] is False
    assert got["ass_path_offered"] == str(ass)
    assert got["offered_matches_used"] is False


def test_the_hashed_file_is_the_one_inside_the_filter(tmp_path):
    used = tmp_path / "used.ass"
    used.write_text("[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,D,,0,0,0,,hi\n",
                    encoding="utf-8")
    other = tmp_path / "other.ass"
    other.write_text("[Events]\n", encoding="utf-8")
    filt = (f"crop=810:1440,subtitles=filename='{_escaped(used)}'"
            f":fontsdir='f'")
    got = rr.record(_argv(filt), ass_path=str(other))
    assert got["caption_filter"] is True
    assert Path(got["ass_path"]).resolve() == used.resolve()
    assert got["ass_bytes"] == used.stat().st_size
    assert got["ass_sha256"] and len(got["ass_sha256"]) == 64
    assert got["offered_matches_used"] is False, "and the mismatch is reported"


def test_the_other_spelling_of_the_filter_is_recognised(tmp_path):
    """`false` here is a claim about the encode, not a gap, so a recogniser that
    knows only today's spelling would start making a false claim the day
    somebody writes `ass=` instead."""
    ass = tmp_path / "a.ass"
    ass.write_text("[Events]\n", encoding="utf-8")
    got = rr.record(_argv(f"ass=filename='{_escaped(ass)}'"))
    assert got["caption_filter"] is True
    assert Path(got["ass_path"]).resolve() == ass.resolve()


def test_an_escaped_path_is_unescaped_before_it_is_opened(tmp_path):
    r"""THROUGH THE SHIPPING FUNCTION, never through a re-spelling of it here.
    `escape_filter_path` turns backslashes into forward slashes and escapes the
    drive colon, so the only escape to undo is `\:`. The first version dropped
    every backslash instead, which looks equivalent and is not: handed a Windows
    path that was never escaped it produced `C:UsersvladoAppData...` and the
    record reported the file as missing."""
    ass = tmp_path / "a.ass"
    ass.write_text("[Events]\n", encoding="utf-8")
    got = rr.record(_argv(f"subtitles=filename='{_escaped(ass)}'"))
    assert got["ass_sha256"] is not None and got["ass_refused"] is None
    assert Path(got["ass_path"]).resolve() == ass.resolve()


def test_a_filter_naming_a_missing_file_refuses_rather_than_hashing_nothing(tmp_path):
    got = rr.record(_argv(
        f"subtitles=filename='{_escaped(tmp_path / 'gone.ass')}'"))
    assert got["caption_filter"] is True, "the call did carry one"
    assert got["ass_sha256"] is None and got["ass_refused"] == rr.NO_FILE


def test_a_filter_with_no_filename_is_refused_not_read_as_absent(tmp_path):
    got = rr.record(_argv("subtitles=x"))
    assert got["caption_filter"] is True
    assert got["ass_refused"] == rr.NO_PATH_IN_FILTER


def test_a_command_nobody_can_read_does_not_say_there_was_no_filter():
    """`caption_filter: False` is a demonstrated absence. An unreadable argv has
    demonstrated nothing, and reporting `False` for it would be the oldest
    defect in this batch wearing a new hat."""
    for bad in (None, "ffmpeg -i in.mp4", 7, ["ffmpeg", 3], {"a": 1}):
        got = rr.record(bad)
        assert got["caption_filter"] is None, repr(bad)
        assert got["ass_refused"] == rr.NO_ARGV, repr(bad)


def test_the_command_itself_is_digested_so_two_runs_can_be_compared():
    a = rr.record(_argv("crop=810:1440"))
    b = rr.record(_argv("crop=810:1440"))
    c = rr.record(_argv("crop=810:1442"))
    assert a["argv_sha256"] == b["argv_sha256"] != c["argv_sha256"]
    assert a["argv_len"] == len(_argv("crop=810:1440"))


# --- the events, on the export clock ----------------------------------------


def test_the_events_are_counted_with_their_first_and_last_times(tmp_path):
    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, ML, MR, MV, Effect, Text\n"
        "Dialogue: 0,0:00:01.50,0:00:02.25,D,,0,0,0,,one\n"
        "Dialogue: 0,0:00:04.00,0:00:05.75,D,,0,0,0,,two\n",
        encoding="utf-8")
    got = rr.ass_events(ass)
    assert got["events"] == 2 and got["refused"] is None
    assert got["first_s"] == 1.5 and got["last_start_s"] == 4.0
    assert got["last_end_s"] == 5.75


def test_a_file_with_no_dialogue_is_zero_events_and_not_a_refusal(tmp_path):
    ass = tmp_path / "a.ass"
    ass.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    got = rr.ass_events(ass)
    assert got["events"] == 0 and got["refused"] is None
    assert got["first_s"] is None


def test_an_unparsable_line_fails_the_whole_file(tmp_path):
    """A partial parse reports a caption track that ends earlier than it does,
    which measures the parser rather than the export."""
    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:01.50,0:00:02.25,D,,0,0,0,,one\n"
        "Dialogue: 0,not-a-time,0:00:05.75,D,,0,0,0,,two\n",
        encoding="utf-8")
    got = rr.ass_events(ass)
    assert got["events"] is None and got["refused"] == rr.UNREADABLE


def test_a_missing_file_is_refused_and_not_zero_events(tmp_path):
    for path in (None, tmp_path / "gone.ass"):
        got = rr.ass_events(path)
        assert got["events"] is None and got["refused"] == rr.NO_FILE, repr(path)


# --- the intervals, which the count could not stand in for -------------------


def test_the_merged_intervals_are_what_coverage_means(tmp_path):
    """A count with a first start and a last end says nothing about the middle:
    forty events and two events can share both endpoints. Word-level overlays
    overlap heavily, so the UNION of the spans is what "there was text on
    screen" means, and `covered_s` is how much of the export that is."""
    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,D,,0,0,0,,a\n"
        "Dialogue: 0,0:00:01.50,0:00:03.00,D,,0,0,0,,b\n"   # overlaps the first
        "Dialogue: 0,0:00:10.00,0:00:11.00,D,,0,0,0,,c\n",
        encoding="utf-8")
    got = rr.ass_events(ass)
    assert got["state"] == rr.EVENTS_READ and got["events"] == 3
    assert got["intervals"] == [[1.0, 3.0], [10.0, 11.0]]
    assert got["covered_s"] == 3.0, "not the 10s between the endpoints"
    assert got["first_s"] == 1.0 and got["last_end_s"] == 11.0


def test_a_gapless_run_of_events_merges_into_one_interval(tmp_path):
    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,D,,0,0,0,,a\n"
        "Dialogue: 0,0:00:02.00,0:00:03.00,D,,0,0,0,,b\n",
        encoding="utf-8")
    assert rr.ass_events(ass)["intervals"] == [[1.0, 3.0]]


def test_the_three_answers_have_three_names(tmp_path):
    """`empty_demonstrated` and `refused` both have no events, and only one of
    them is a statement about the export."""
    empty = tmp_path / "e.ass"
    empty.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
    assert rr.ass_events(empty)["state"] == rr.EVENTS_EMPTY
    assert rr.ass_events(empty)["covered_s"] == 0.0
    assert rr.ass_events(tmp_path / "gone.ass")["state"] == rr.EVENTS_REFUSED
    assert rr.ass_events(tmp_path / "gone.ass")["covered_s"] is None


def test_an_event_that_ends_before_it_starts_is_unreadable(tmp_path):
    """Two perfectly parseable timestamps that describe no span at all. Left in,
    it would shorten a merged interval and understate the coverage."""
    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:05.00,0:00:04.00,D,,0,0,0,,a\n", encoding="utf-8")
    got = rr.ass_events(ass)
    assert got["state"] == rr.EVENTS_REFUSED and got["refused"] == rr.UNREADABLE
    assert got["intervals"] is None


def test_the_intervals_are_on_the_export_clock_because_the_writer_remaps_them():
    """Read off `_write_ass` rather than assumed. It calls
    `dead_air.remap_overlays(overlays, drop_spans)` before writing, because
    libass positions against absolute times and a caption left on the untrimmed
    clock drifts further out of sync with every second cut. If that ever stops
    being true, every interval this module reports is on a clock the mp4 does
    not keep — and nothing else would notice."""
    import inspect

    from workers import clipper_captions

    src = inspect.getsource(clipper_captions._write_ass)
    assert "remap_overlays(overlays, drop_spans)" in src
