"""SCB2r (codex-verdict-next-22 §1): the editor still is corroborated like the export.

`_render_frame` runs the gate's `prepare`, then builds the command and ALTERS it (frame selection,
PNG output). Its FINAL argv is checked against the manifest before it runs and the patch is hashed
again after, with the export's own verifier (`render_record.require_corroborated` / `confirm_patch`),
never a second copy of the rules. Before this, a patch replaced after its manifest was written still
produced a 288,112-byte PNG (data/claude-master-20260924/next22-check/editor-patch-result.json).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from services.clipper import dynamic_render
from services.clipper import source_treatment as st
from services.clipper import source_treatment_render as treat
from test_clipper_source_treatment_render import Synth, encode_source
from workers import clipper_preview_frame as frame_mod
from workers.clipper_preview_frame import _render_frame


@pytest.fixture(scope="module")
def src(tmp_path_factory) -> Path:
    return encode_source(tmp_path_factory.mktemp("scb2r-editor") / "plain.mp4")


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "still"
    work.mkdir(exist_ok=True)
    return work


def _refused(reason: str, s: Synth, decision: dict, tmp_path: Path, monkeypatch=None) -> None:
    """Refused with `reason`, no image left. With `monkeypatch`, also: the command never RAN — the
    check is before the execution, not only the hash after it (next-22 §1)."""
    runs = []
    if monkeypatch is not None:
        real = frame_mod.run
        monkeypatch.setattr(frame_mod, "run", lambda cmd, **kw: (runs.append(cmd), real(cmd, **kw))[1])
    work = _work(tmp_path)
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _render_frame(s.clip, s.project, decision, str(s.src), work, 1.0)
    assert e.value.reason == reason, e.value
    assert not (work / "frame.png").exists(), "a refused still left an image behind"
    if monkeypatch is not None:
        assert runs == [], "the tampered command was executed before it was refused"


def test_control_an_untouched_patch_still_renders(src, tmp_path):
    s = Synth(tmp_path, src)
    png = _render_frame(s.clip, s.project, s.decision(s.setting("erase")), str(s.src),
                        _work(tmp_path), 1.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000


def test_a_patch_replaced_after_its_manifest_is_refused(src, tmp_path, monkeypatch):
    real = treat.build_patch

    def tamper(*a, **kw):
        made = real(*a, **kw)
        path = Path(made["file"])
        path.write_bytes(bytes([16]) * path.stat().st_size)      # next22-check's own tampering
        return made

    monkeypatch.setattr(treat, "build_patch", tamper)
    s = Synth(tmp_path, src)
    _refused("patch_hash_mismatch", s, s.decision(s.setting("erase")), tmp_path, monkeypatch)


def test_a_manifest_of_another_attempt_is_refused(src, tmp_path, monkeypatch):
    real = treat.build_patch

    def elsewhere(*a, **kw):
        made = real(*a, **kw)
        path = Path(made["file"])
        other = path.with_name(f"other-{path.name}")
        shutil.copyfile(path, other)                               # same bytes, not the manifest's file
        return {**made, "file": str(other)}

    monkeypatch.setattr(treat, "build_patch", elsewhere)
    s = Synth(tmp_path, src)
    _refused("manifest_of_another_attempt", s, s.decision(s.setting("erase")), tmp_path, monkeypatch)


@pytest.mark.parametrize("change", ["seek", "overlay"])
def test_a_command_whose_seek_or_overlay_is_not_the_manifests_is_refused(src, tmp_path, monkeypatch,
                                                                        change):
    real = dynamic_render.build_dynamic_cmd

    def altered(*a, **kw):
        cmd = list(real(*a, **kw))
        if change == "seek":
            i = cmd.index("-ss")
            cmd[i + 1] = f"{float(cmd[i + 1]) + 0.5:.6f}"
        else:
            g = cmd.index("-filter_complex") + 1
            cmd[g] = "[0:v]null[pre];" + cmd[g]                    # the graph no longer opens with it
        return cmd

    monkeypatch.setattr(dynamic_render, "build_dynamic_cmd", altered)
    s = Synth(tmp_path, src)
    _refused("record_not_corroborated", s, s.decision(s.setting("erase")), tmp_path, monkeypatch)


def test_a_patch_changed_while_the_still_renders_is_refused_and_no_image_returned(src, tmp_path,
                                                                                 monkeypatch):
    real = frame_mod.run
    seen = {}

    def run_then_tamper(cmd, **kw):
        out = real(cmd, **kw)
        i = cmd.index("rawvideo")                                  # the patch input: -f rawvideo ... -i <patch>
        path = Path(cmd[cmd.index("-i", i) + 1])
        seen["patch"] = path
        path.write_bytes(bytes([200]) * path.stat().st_size)
        return out

    monkeypatch.setattr(frame_mod, "run", run_then_tamper)
    s = Synth(tmp_path, src)
    _refused("patch_hash_mismatch", s, s.decision(s.setting("erase")), tmp_path)
    assert seen, "the tampering never ran, so the test proved nothing"
