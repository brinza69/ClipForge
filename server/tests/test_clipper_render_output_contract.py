"""Export option propagation and the shared sidecar contract.

Moved with the execution/writer out of test_clipper_dynamic_export so the
changed files stay under the repository's 500-line limit. The original
counterexamples and assertions remain; real entry-point coverage lives in
test_clipper_shared_export.
"""
from pathlib import Path

import pytest

from workers import clipper_render_jobs as jobs, clipper_render_output as output

# ── the two paths take the same options ──────────────────────────────────────
#
# `dynamic_edit` became the default on 2026-08-17 and the dynamic call site was
# never given `watermark`, `drop_spans`, `has_audio` or `is_cancelled` — four
# arguments the static call beside it had always had. So the watermark silently
# vanished from every export, `trim_silence` did nothing, and a queued
# cancellation was ignored.
#
# Verified on a real render before these were written: same clip, options off
# then on, 35.83s -> 33.43s with the captions shifted by exactly the 2.40s
# removed, and "ClipForge" burned across the bottom.


def _plan():
    return {"duration": 30.0, "hits": [],
            "shots": [{"rect": {"x": 0, "y": 0, "w": 608, "h": 1080}}]}


def _cmd(**kw):
    from services.clipper.dynamic_render import build_dynamic_cmd

    return build_dynamic_cmd("src.mp4", _plan(), "c.txt", None, "out.mp4",
                             start=10.0, duration=30.0, src_w=1920, src_h=1080,
                             **kw)


def test_the_dynamic_renderer_burns_the_watermark():
    graph = _cmd(watermark="ClipForge")[_cmd(watermark="ClipForge").index("-filter_complex") + 1]
    assert "drawtext" in graph and "ClipForge" in graph
    assert "drawtext" not in _cmd()[_cmd().index("-filter_complex") + 1]


def test_the_dynamic_renderer_cuts_dead_air_and_shortens_the_output():
    """`-t` sits after `-i`, so it is an OUTPUT duration: left at the window
    length ffmpeg reads PAST the window to refill the seconds `select` dropped,
    and the dead air comes back as whatever followed it."""
    cmd = _cmd(drop_spans=[(5.0, 8.5)])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "select=" in graph and "setpts=" in graph
    assert float(cmd[cmd.index("-t") + 1]) == pytest.approx(26.5)
    assert float(_cmd()[_cmd().index("-t") + 1]) == pytest.approx(30.0)


def test_a_silent_source_survives_a_trim():
    """`-map 0:a?` tolerates a file with no audio track; `[0:a]` inside a
    filtergraph does not, and would fail the whole render."""
    cmd = _cmd(drop_spans=[(5.0, 8.5)], has_audio=False)
    assert "[0:a]" not in cmd[cmd.index("-filter_complex") + 1]
    assert "0:a?" in cmd


def test_loudness_moves_into_the_graph_only_when_the_audio_was_filtered():
    """ffmpeg will not run `-af` on a stream a complex graph produced, so with
    dead air removed the chain has to go inside. With no drops the command is
    exactly what it was before the option existed."""
    trimmed = _cmd(drop_spans=[(5.0, 8.5)])
    assert "-af" not in trimmed
    assert "loudnorm" in trimmed[trimmed.index("-filter_complex") + 1]
    assert "-af" in _cmd()


def test_a_cancelled_export_does_not_start_the_encode():
    from services.clipper.dynamic_render import render_dynamic_clip

    with pytest.raises(Exception) as caught:
        render_dynamic_clip("src.mp4", _plan(), "out.mp4", start=0.0,
                            work_dir=".", is_cancelled=lambda: True)
    assert "cancel" in type(caught.value).__name__.lower() + str(caught.value).lower()


def test_dynamic_renderer_publishes_the_final_path_atomically(tmp_path, monkeypatch):
    from services.clipper import dynamic_render
    from services.clipper.dynamic_render import render_dynamic_clip

    final = tmp_path / "clip.mp4"
    final.write_bytes(b"previous render")
    seen = {}

    def fake_run(cmd, **_kwargs):
        seen["temp"] = Path(cmd[-1])
        seen["temp"].write_bytes(b"new render" * 300)

    monkeypatch.setattr(dynamic_render, "run", fake_run)
    plan = {
        "duration": 30.0,
        "hits": [],
        "style": {},
        "shots": [{
            "t0": 0.0,
            "t1": 30.0,
            "rect": {"x": 0, "y": 0, "w": 608, "h": 1080},
            "anchor": [960, 540],
        }],
    }
    result = render_dynamic_clip(
        "source.mp4", plan, str(final), start=0.0,
        work_dir=tmp_path,
    )

    assert result["path"] == str(final)
    assert final.read_bytes() == b"new render" * 300
    assert seen["temp"] != final
    assert not seen["temp"].exists()


def test_the_export_handler_passes_all_four_to_the_dynamic_call():
    """The regression that mattered was at the CALL SITE, not in the renderer:
    every option above existed on the static side and simply was not handed
    over."""
    import inspect

    src = inspect.getsource(output.render_export)
    dynamic_call = src[src.index("dynamic_render.render_dynamic_clip,"):]
    dynamic_call = dynamic_call[:dynamic_call.index("else:")]
    for arg in ("watermark=", "drop_spans=", "has_audio=", "is_cancelled="):
        assert arg in dynamic_call, f"the dynamic call lost {arg}"


# --- Batch R0: the sidecar contract -----------------------------------------
#
# The sidecar is what the audit reads. Everything below is about it describing
# the file that was actually written — not the row the render started from.


def test_the_sidecar_names_the_renderer_that_actually_ran():
    """Stamped at write time, never backfilled, and NOT one constant for both
    paths: a static export filed under the dynamic renderer's version is
    attributable to a grammar of shots it never had. The 58 pilot exports
    predate the key and must keep reading as unavailable."""
    import inspect

    src = inspect.getsource(output._write_sidecar)
    assert "dynamic_render.RENDER_VERSION if dyn" in src
    assert "else static_render.RENDER_VERSION" in src

    from services.clipper import dynamic_render, render as static_render

    assert dynamic_render.RENDER_VERSION != static_render.RENDER_VERSION


def test_the_sidecar_carries_every_key_the_fingerprint_is_taken_over():
    """The digest is computed from the sidecar through `edit_quality`. A key the
    projection reads and the writer never writes fingerprints as `null` for
    every export — silently, and identically for all of them."""
    import inspect

    from services.clipper import edit_quality

    body = inspect.getsource(output._write_sidecar)
    body = body[body.index("body = {"):body.index('body["render_record"]')]
    for key in edit_quality.FINGERPRINT_KEYS:
        assert f'"{key}"' in body, f"the sidecar never writes {key}"


def test_the_fingerprint_is_computed_through_the_audits_own_projection():
    """Not a payload built by hand here. Two definitions drift, and then the
    check passes for a file whose plan has changed underneath it.

    AND IT IS WRITTEN AS v3 (SC batch 2, codex-verdict-next-22 Q1), with the schema declared beside it. A record with
    no schema field is read under an assumption; the writing path has no reason
    to need one, and `render_input`'s contract forbids rescuing a v2 mismatch
    with the v1 formula, which only works if new exports say what they are."""
    import inspect

    src = inspect.getsource(output._write_sidecar)
    assert "edit_quality.input_fingerprint(" in src
    assert "schema=render_input.FINGERPRINT_SCHEMA_V3" in src
    assert 'body["fingerprint_schema"] = render_input.FINGERPRINT_SCHEMA_V3' in src


def test_the_sidecar_records_the_seconds_the_render_removed():
    """Without `drop_spans` the sidecar describes a longer clip than the file,
    and every time in it — captions, shot boundaries — is on a clock the mp4
    does not keep."""
    import inspect

    assert '"drop_spans": drop' in inspect.getsource(output._write_sidecar)


def test_the_sidecar_keeps_the_stored_caption_plan_and_the_height_separately():
    """Not a pre-merged "effective" plan. Merging here would put a second copy
    of `_write_ass`'s rule in the worker, and the merged result cannot be taken
    apart again by anything that needs to know what was decided at score time
    and what was decided at render time. Since D2 an alternative with no stored
    plan records the one built for the render, unmerged, and
    `caption_plan_state.origin` says `built` rather than `stored`."""
    import inspect

    src = inspect.getsource(output._write_sidecar)
    assert '"caption_plan": decision.get("caption_plan", clip.caption_plan)' in src
    assert '"caption_plan_state": decision.get("caption_plan_state")' in src
    assert '"caption_y": caption_y' in src


def test_a_missing_source_sizes_to_none_not_zero(tmp_path):
    """Part of the fingerprint: the same path with a different file behind it is
    a different input. A source that is gone and a source that is empty are
    different facts, and 0 would spell them the same."""
    real = tmp_path / "source.mp4"
    real.write_bytes(b"x" * 17)
    assert jobs._size_bytes(real) == 17
    assert jobs._size_bytes(tmp_path / "gone.mp4") is None


