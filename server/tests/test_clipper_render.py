"""
Tests for AI Stream Clipper rendering (services/clipper/render.py).

Everything here asserts on the ARGV that `build_render_cmd` returns. That is
the whole point of keeping it pure: the invariants that actually matter — seek
before input, one encode, captions inside the single filtergraph, escaped user
text — are checkable without ffmpeg, without media on disk and in milliseconds.
"""

import pytest

from services.clipper import render


CAND = {"start": 125.5, "end": 168.25}


def _cmd(**kw):
    params = dict(fps=60, crf=20, preset="medium")
    params.update(kw)
    ass = params.pop("ass_path", None)
    plan = params.pop("plan", {})
    return render.build_render_cmd(
        "D:/media/vod.mp4", CAND, plan, ass, "D:/out/clip.mp4", **params
    )


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _video_branch(graph):
    """The graph up to where the audio chain starts.

    Several tests used to assert `graph.endswith("[vout]")` as a way of saying
    "the video chain terminates there". Once audio filtering was added they were
    really asserting that video comes LAST, which none of them meant."""
    for marker in (";[0:a]", ";[acut]"):
        if marker in graph:
            return graph.split(marker)[0]
    return graph


# --------------------------------------------------------------------------
# Seeking and trimming
# --------------------------------------------------------------------------

def test_seek_comes_before_the_input():
    # -ss after -i decodes from zero; on a 6-hour VOD that is the whole cost.
    cmd = _cmd()
    assert cmd.index("-ss") < cmd.index("-i")


def test_duration_is_the_candidate_window():
    cmd = _cmd()
    assert cmd[cmd.index("-ss") + 1] == "125.500"
    assert cmd[cmd.index("-t") + 1] == "42.750"


def test_inverted_or_missing_window_does_not_produce_a_zero_length_render():
    cmd = render.build_render_cmd(
        "s.mp4", {"start": 10, "end": 4}, {}, None, "o.mp4",
        fps=30, crf=20, preset="fast",
    )
    assert float(cmd[cmd.index("-t") + 1]) > 0

    cmd = render.build_render_cmd(
        "s.mp4", {}, {}, None, "o.mp4", fps=30, crf=20, preset="fast",
    )
    assert cmd[cmd.index("-ss") + 1] == "0.000"
    assert float(cmd[cmd.index("-t") + 1]) > 0


# --------------------------------------------------------------------------
# One encode, one filtergraph
# --------------------------------------------------------------------------

def test_single_video_encode():
    cmd = _cmd(ass_path="D:/proj/captions.ass", watermark="clipforge")
    assert cmd.count("-c:v") == 1
    assert cmd.count("libx264") == 1
    assert cmd.count("-filter_complex") == 1


def test_captions_are_burned_inside_the_same_filter_complex():
    cmd = _cmd(ass_path="D:/proj/captions.ass")
    graph = _graph(cmd)
    assert "subtitles=filename=" in graph
    assert "fontsdir=" in graph
    # A -vf alongside -filter_complex is ffmpeg's way of saying "second pass".
    assert "-vf" not in cmd
    # The caption filter must extend the layout chain's [v] pad, not open a new one.
    assert ";[v]subtitles=" in graph
    # The VIDEO branch ends at [vout]. Not the graph — the loudness chain is
    # appended after it, and asserting on the graph's last character made this
    # test a statement about filter ORDER, which it never meant to be.
    assert _video_branch(graph).endswith("[vout]")
    assert cmd[cmd.index("-map") + 1] == "[vout]"


def test_ass_path_is_escaped_for_the_filter_parser():
    graph = _graph(_cmd(ass_path="D:/proj/captions.ass"))
    assert "D\\:/proj/captions.ass" in graph


def test_without_captions_the_layout_pad_is_mapped_directly():
    cmd = _cmd()
    graph = _graph(cmd)
    assert "subtitles=" not in graph
    assert _video_branch(graph).endswith("[v]")
    assert cmd[cmd.index("-map") + 1] == "[v]"


# --------------------------------------------------------------------------
# Mapping and encoder settings
# --------------------------------------------------------------------------

def test_a_silent_source_still_renders():
    """`-map 0:a?` used to carry this on its own. It cannot any more: the
    loudness chain takes the audio as a FILTERGRAPH input, and `[0:a]` on a file
    with no audio track fails the whole render rather than being skipped. So the
    caller probes, and with no audio the optional map is what is left."""
    cmd = _cmd(has_audio=False)
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert "0:a?" in maps
    assert "loudnorm" not in _graph(cmd)


def test_audio_is_levelled_the_same_way_the_multi_shot_path_levels_it():
    """The two renderers had drifted: the multi-shot path normalised and this
    one shipped whatever level the source had, so a clip that FELL BACK to the
    static layout came out quieter than one that did not."""
    from services.clipper.ffmpeg_tools import loudness_chain

    graph = _graph(_cmd())
    assert loudness_chain() in graph
    assert graph.endswith("[aout]")


def test_encoder_settings():
    cmd = _cmd(fps=60, crf=18, preset="slow")
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[cmd.index("-crf") + 1] == "18"
    assert cmd[cmd.index("-preset") + 1] == "slow"
    assert cmd[cmd.index("-r") + 1] == "60"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[-1] == "D:/out/clip.mp4"
    assert "-y" in cmd


def test_command_is_an_argument_list_never_a_shell_string():
    cmd = _cmd(watermark="a b c")
    assert isinstance(cmd, list)
    assert all(isinstance(a, str) for a in cmd)


# --------------------------------------------------------------------------
# Even dimensions
# --------------------------------------------------------------------------

def test_odd_output_dimensions_are_forced_even():
    # libx264 + yuv420p refuses an odd width or height outright.
    graph = _graph(_cmd(out_w=1081, out_h=1921))
    assert "1080" in graph and "1920" in graph
    assert "1081" not in graph and "1921" not in graph


def test_default_output_is_1080x1920():
    graph = _graph(_cmd())
    assert "1080" in graph and "1920" in graph


# --------------------------------------------------------------------------
# Watermark — user input reaching a filter string
# --------------------------------------------------------------------------

WATERMARK = "Ratio: 100% 'live' back\\slash"


@pytest.fixture(autouse=True)
def _font(monkeypatch):
    # Pin the font so the watermark assertions do not depend on which TTFs the
    # box happens to have installed.
    monkeypatch.setattr(render, "_watermark_fontfile", lambda: "C:/fonts/test.ttf")


def test_watermark_text_is_escaped():
    graph = _graph(_cmd(watermark=WATERMARK))
    assert "drawtext=" in graph
    assert "Ratio\\:" in graph          # colon escaped
    assert "100\\%" in graph            # percent escaped
    assert "back\\\\slash" in graph     # backslash doubled
    assert "'live'" not in graph        # raw quote would close the text value
    assert "\u2019live\u2019" in graph


def test_watermark_disables_drawtext_expansion():
    # With the default expansion a literal % makes drawtext draw nothing at
    # all, and %{expr:...} in user text would be evaluated instead of shown.
    assert "expansion=none" in _graph(_cmd(watermark=WATERMARK))


def test_watermark_font_is_passed_as_a_file_not_a_fontconfig_name():
    # font= goes through fontconfig, which segfaults ffmpeg on a box with no
    # fontconfig.conf. The drive colon needs the double escape drawtext wants.
    graph = _graph(_cmd(watermark="clipforge"))
    assert "fontfile=C\\\\:/fonts/test.ttf" in graph
    assert "font=" not in graph.replace("fontfile=", "").replace("fontcolor=", "")


def test_watermark_is_dropped_when_no_font_is_available(monkeypatch):
    monkeypatch.setattr(render, "_watermark_fontfile", lambda: None)
    assert "drawtext" not in _graph(_cmd(watermark="clipforge"))


def test_watermark_newlines_are_flattened():
    graph = _graph(_cmd(watermark="line one\nline two"))
    assert "\n" not in graph
    assert "line one line two" in graph


def test_watermark_is_bottom_centred_and_translucent():
    graph = _graph(_cmd(watermark="clipforge"))
    assert "x=(w-text_w)/2" in graph
    assert "y=h-text_h-" in graph
    assert "fontcolor=white@0.55" in graph


def test_watermark_size_scales_with_output_width():
    big = _graph(_cmd(watermark="cf", out_w=1080, out_h=1920))
    small = _graph(_cmd(watermark="cf", out_w=540, out_h=960))
    assert "fontsize=32" in big
    assert "fontsize=16" in small


def test_empty_watermark_adds_no_drawtext():
    assert "drawtext" not in _graph(_cmd(watermark="   "))


def test_watermark_and_captions_share_one_chain():
    graph = _graph(_cmd(ass_path="D:/proj/c.ass", watermark="clipforge"))
    assert graph.count(";[v]") == 1
    assert graph.count("[vout]") == 1
    assert "subtitles=" in graph and "drawtext=" in graph


# --------------------------------------------------------------------------
# Layout plan handoff
# --------------------------------------------------------------------------

def test_missing_plan_falls_back_to_a_centre_crop_fill():
    graph = _graph(_cmd(plan={}))
    assert graph.startswith("[0:v]")
    assert "crop=1080:1920" in graph
    assert _video_branch(graph).endswith("[v]")


def test_layout_filtergraph_is_used_verbatim(monkeypatch):
    fake = "[0:v]crop=100:100[a];[a]scale=1080:1920[v]"
    monkeypatch.setattr(render, "_video_chain", lambda plan, w, h: fake)
    graph = _graph(_cmd(plan={"layout": "pip"}, ass_path="c.ass"))
    assert graph.startswith(fake)


def test_layout_chain_must_end_in_a_v_pad(monkeypatch):
    """A graph that does not end in [v] must fail loudly here.

    render appends the subtitles filter to that pad, so a mislabelled output
    would otherwise reach ffmpeg as a reference to a non-existent stream and
    die with a cryptic filtergraph error halfway through an export.

    Patch the function on the layout module itself: `_video_chain` does
    `from services.clipper import layout`, which reads the attribute off the
    already-imported package, so swapping sys.modules would not be seen.
    """
    from services.clipper import layout

    monkeypatch.setattr(
        layout, "build_filtergraph", lambda plan, w, h: "[0:v]scale=1080:1920[wrong]"
    )
    with pytest.raises(ValueError):
        _cmd(plan={"layout": "pip"})


# --------------------------------------------------------------------------
# render_clip / render_preview
# --------------------------------------------------------------------------

async def test_render_clip_honours_cancellation_before_spawning_ffmpeg():
    from job_queue import JobCancelledError

    with pytest.raises(JobCancelledError):
        await render.render_clip(
            "s.mp4", CAND, {}, None, "o.mp4",
            fps=60, crf=20, preset="medium", is_cancelled=lambda: True,
        )


def test_preview_constants_are_low_res_and_cheap():
    assert (render.PREVIEW_W, render.PREVIEW_H) == (540, 960)
    assert render.PREVIEW_CRF == 30
    assert render.PREVIEW_PRESET == "veryfast"
