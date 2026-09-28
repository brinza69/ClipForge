"""The gate on every render path, driven by explicit decisions (SC-addendum-v2 §4).

SC batch 2 — the part of `…_gate.py` that needs no persisted configuration
(the router/handler half is batch 3). `render_export` and the editor still
(`clipper_preview_frame._render_frame`) re-run the gate on the decision about to
be encoded; each refusal is its own state, publishes nothing, leaves no patch
behind, and never touches the clip's current export. Every execution-side
refusal is tested under BOTH layers: an active treatment that cannot be
executed is refused with `suppress` too (codex-verdict-next-10 §1).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from services.clipper import source_treatment as st
from services.clipper import source_treatment_render as treat
from services.clipper import storage
from test_clipper_source_treatment import write_mask
from test_clipper_source_treatment_render import (CLIP, PARAMS_VALUES, PROJECT, Synth,  # noqa: F401
                                                  encode_source, mask_doc, params_record,
                                                  sources)
from test_clipper_source_treatment_witness import SPLIT_CLIP, approved, split_override  # noqa: F401
from workers import clipper_render_output as output
from workers.clipper_preview_frame import _render_frame

LAYERS = pytest.mark.parametrize("burn", [True, False], ids=["burn", "suppress"])


@pytest.fixture(scope="module")
def src(tmp_path_factory) -> Path:
    return encode_source(tmp_path_factory.mktemp("sc2-gate") / "plain.mp4")


@pytest.fixture
def original():
    """The clip's CURRENT export — what no treated render may ever touch."""
    out = storage.export_path(PROJECT, CLIP)
    out.parent.mkdir(parents=True, exist_ok=True)
    for suffix, data in ((".mp4", b"original mp4"), (".json", b'{"original": true}'),
                         (".ass", b"original ass")):
        out.with_suffix(suffix).write_bytes(data)
    before = _hashes(out.parent)
    yield out
    assert _hashes(out.parent) == before, "the current export changed"
    for suffix in (".mp4", ".json", ".ass"):
        out.with_suffix(suffix).unlink(missing_ok=True)


def _hashes(d: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir() if p.is_file()}


def _versioned(n: int = 1) -> Path:
    return storage.paths(PROJECT)["exports_dir"] / f"sct{n}" / f"{CLIP}.mp4"


async def _refuses(reason: str, s: Synth, decision: dict, out: Path | None = None, **kw) -> None:
    out = out or _versioned()
    kw.setdefault("destination", "versioned")
    before = {p: p.read_bytes() for p in (out, out.with_suffix(".json")) if p.exists()}
    with pytest.raises(st.SourceTreatmentRefused) as e:
        await output.render_export(s.clip, s.project, decision, out, src=str(s.src), **kw)
    assert e.value.reason == reason, e.value
    # nothing written — and where the original sits, its bytes are what they were
    assert {p: p.read_bytes() for p in (out, out.with_suffix(".json")) if p.exists()} == before
    assert not list(out.parent.glob(".*st-*"))


def _still_refuses(reason: str, s: Synth, decision: dict, tmp_path: Path) -> None:
    work = tmp_path / "still"
    work.mkdir(exist_ok=True)
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _render_frame(s.clip, s.project, decision, str(s.src), work, 1.0)
    assert e.value.reason == reason, e.value
    assert not (work / "frame.png").exists()


# ── execution fails or cannot be proven: refused on both paths, both layers ──

@LAYERS
async def test_patch_generation_failing_is_refused(src, tmp_path, monkeypatch, original, burn):
    s = Synth(tmp_path, src)

    def broken(*a, **kw):
        raise st.SourceTreatmentRefused("patch_build_failed", "simulated")

    monkeypatch.setattr(treat, "write_patch", broken)
    d = s.decision(s.setting("erase"), burn=burn)
    await _refuses("patch_build_failed", s, d)
    _still_refuses("patch_build_failed", s, d, tmp_path)


@LAYERS
@pytest.mark.parametrize("states, reason", [
    ({(62, 80): {"state": "unknown"}}, "unknown_frames_in_window"),
    ({(62, 80): {"basis": "detector_only"}}, "unverified_run"),
])
async def test_a_mask_that_cannot_be_trusted_is_refused(src, tmp_path, original, burn, states, reason):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"), burn=burn)
    bad = write_mask(tmp_path / "bad", mask_doc(s.ident, states=states))
    d["source_treatment_inputs"]["mask_path"] = str(bad)
    await _refuses(reason, s, d)
    _still_refuses(reason, s, d, tmp_path)


@LAYERS
async def test_a_png_hash_wrong_is_refused(src, tmp_path, original, burn):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("blur"), burn=burn)
    (s.mask_path.parent / "g" / "L1.png").write_bytes(b"another picture")
    await _refuses("glyph_hash_mismatch", s, d)
    _still_refuses("glyph_hash_mismatch", s, d, tmp_path)


@LAYERS
async def test_the_static_path_is_refused(src, tmp_path, original, burn):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"), burn=burn, dynamic=False)
    await _refuses("static_path_unsupported", s, d)
    _still_refuses("static_path_unsupported", s, d, tmp_path)


def test_the_static_builder_itself_takes_no_patch():
    from services.clipper import render

    with pytest.raises(st.SourceTreatmentRefused) as e:
        render.build_render_cmd("s.mp4", {"start": 0, "end": 1}, {}, None, "o.mp4", fps=30, crf=18,
                                preset="ultrafast", source_patch={"file": "p.yuv"})
    assert e.value.reason == "static_path_unsupported"


@LAYERS
async def test_params_this_executor_cannot_run_are_refused(src, tmp_path, original, burn):
    s = Synth(tmp_path, src)
    values = {**PARAMS_VALUES, "erase": {**PARAMS_VALUES["erase"], "method": "opencv_inpaint_ns"}}
    s.params_path = params_record(tmp_path / "ns", values, version="sc-ns-v1")
    d = s.decision(s.setting("erase"), burn=burn)
    await _refuses("treatment_not_implemented", s, d)
    _still_refuses("treatment_not_implemented", s, d, tmp_path)


@LAYERS
async def test_a_params_record_that_changed_is_refused(src, tmp_path, original, burn):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"), burn=burn)
    s.params_path.write_bytes(s.params_path.read_bytes().replace(b"17", b"19"))
    await _refuses("params_missing", s, d)


@pytest.mark.parametrize("gone", ["record", "sha256"])
async def test_a_params_record_that_cannot_be_read_is_refused(src, tmp_path, original, gone):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    (s.params_path if gone == "record" else Path(f"{s.params_path}.sha256")).unlink()
    await _refuses("params_missing", s, d)


# ── a decision that is not what the loaded mask resolves to ──────────────────

@LAYERS
@pytest.mark.parametrize("edit", ["drop_segment", "window", "treatment"])
async def test_a_decision_changed_since_it_was_resolved_is_refused(src, tmp_path, original, burn, edit):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"), burn=burn)
    r = d["source_treatment"]
    if edit == "drop_segment":
        r["per_line"] = [g for g in r["per_line"] if g["line_id"] != "L4"]
    elif edit == "window":
        r["window"] = {"k_first": 4, "k_last": 79}
    else:
        r["per_line"][0]["treatment"], r["per_line"][0]["params"] = "blur", PARAMS_VALUES["blur"]
        r["params"] = {**r["params"], "blur": PARAMS_VALUES["blur"]}
    await _refuses("decision_mask_mismatch", s, d)
    _still_refuses("decision_mask_mismatch", s, d, tmp_path)


async def test_a_trim_outside_the_masks_window_is_refused(src, tmp_path, original):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    s.clip.start_time = 0.0  # the stored decision predates the trim
    await _refuses("mask_window_does_not_cover_clip", s, d)


async def test_an_active_decision_without_its_inputs_is_refused(src, tmp_path, original):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    del d["source_treatment_inputs"]
    await _refuses("treatment_inputs_missing", s, d)


# ── configured clips, the burn guard ─────────────────────────────────────────

@pytest.mark.parametrize("where", ["clip", "project"])
async def test_a_script_built_decision_without_the_key_is_refused_when_configured(
        src, tmp_path, original, where):
    s = Synth(tmp_path, src)
    d = s.decision(None)
    del d["source_treatment"], d["source_treatment_inputs"]
    setting = {"treatment": "erase", "decided_by": "human"}
    if where == "clip":
        s.clip.source_caption_treatment = setting
    else:
        s.project.clipper_settings = {"source_caption_treatment": setting}
    await _refuses("decision_without_treatment", s, d)
    _still_refuses("decision_without_treatment", s, d, tmp_path)


async def test_burning_over_the_untouched_source_text_is_refused(src, tmp_path, original):
    s = Synth(tmp_path, src)
    s.clip.source_has_burned_captions = True
    await _refuses("burn_over_untreated_source_captions", s, s.decision(None, burn=True))
    _still_refuses("burn_over_untreated_source_captions", s, s.decision(None, burn=True), tmp_path)
    done = await output.render_export(s.clip, s.project, s.decision(None), tmp_path / "ok.mp4",
                                      src=str(s.src))  # suppress: nothing to treat, nothing refused
    assert done["sidecar"]["source_treatment"]["treatment"] == "none"


# ── never a normal export, never over the original ──────────────────────────

async def test_a_treated_render_is_never_a_normal_export(src, tmp_path, original):
    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    exports = storage.paths(PROJECT)["exports_dir"]
    # handle_export's own call shape: no destination, a staging name beside the export
    await _refuses("treated_export_not_promoted", s, d, exports / f".{CLIP}.job-1-abcd1234.mp4",
                   destination=None)
    # replan_and_rerender.py's default `out` is exports_dir/<clip>.mp4 — the published file
    await _refuses("treated_export_not_promoted", s, d, storage.export_path(PROJECT, CLIP),
                   destination=None)
    for out in (storage.export_path(PROJECT, CLIP), exports / f".{CLIP}.job-1-abcd1234.mp4",
                exports / "sct1" / "deeper" / f"{CLIP}.mp4", exports / "sct0" / f"{CLIP}.mp4",
                exports / "sctx" / f"{CLIP}.mp4", exports / "other" / "sct2" / f"{CLIP}.mp4"):
        with pytest.raises(st.SourceTreatmentRefused) as e:
            treat.check_destination(out, s.project, destination="versioned")
        assert e.value.reason == "destination_not_versioned", out


async def test_the_versioned_directory_renders_and_leaves_the_original_alone(src, tmp_path, original):
    s = Synth(tmp_path, src)
    out = _versioned(1)
    done = await output.render_export(s.clip, s.project, s.decision(s.setting("erase")), out,
                                      src=str(s.src), destination="versioned")
    assert out.exists() and done["sidecar"]["source_treatment_identity"]["treatment"] == "erase"
    assert not list(out.parent.glob(".*st-*"))
    for p in (out, out.with_suffix(".json")):
        p.unlink()


# ── the executor's own refusals ──────────────────────────────────────────────

@LAYERS
@pytest.mark.parametrize("treatment, key, value", [
    ("blur", "context_edge", "wrap"), ("blur", "sigma", [0, 8]),
    ("erase", "opencv_version", "3.4"), ("erase", "dilation", {"shape": "ellipse", "size": 16}),
])
async def test_params_outside_what_the_executor_runs_are_refused(src, tmp_path, original, burn,
                                                                  treatment, key, value):
    s = Synth(tmp_path, src)
    values = {**PARAMS_VALUES, treatment: {**PARAMS_VALUES[treatment], key: value}}
    s.params_path = params_record(tmp_path / "odd", values, version="sc-odd-v1")
    await _refuses("treatment_not_implemented", s, s.decision(s.setting(treatment), burn=burn))


@LAYERS
@pytest.mark.parametrize("doc_kw, reason", [
    ({"line_extra": {"footprint_rule": {"luma_min": 170, "persist": 0.5, "tophat": 13,
                                        "tophat_min": 40}}}, "mask_invalid"),
    ({"stream_extra": {"color_range": "pc"}}, "treatment_not_implemented"),
])
async def test_a_mask_the_params_record_does_not_fit_is_refused(src, tmp_path, original, burn,
                                                                doc_kw, reason):
    from services.clipper.proxy_provenance import media_identity

    s = Synth(tmp_path, src, doc=mask_doc(media_identity(src), **doc_kw))
    await _refuses(reason, s, s.decision(s.setting("erase"), burn=burn))


async def test_a_split_whose_answer_is_gone_by_execution_is_refused(sources, tmp_path, original,
                                                                     approved):
    s = Synth(tmp_path, sources["plain"], clip_id=SPLIT_CLIP)
    d = s.decision(s.setting("erase", split_override(approved)))
    assert d["source_treatment"]["state"] == "resolved"
    approved.write_bytes(b"")  # the person's answer file no longer says what it said
    await _refuses("override_splits_a_line", s, d, tmp_path / "split" / f"{SPLIT_CLIP}.mp4")


def test_a_clock_with_no_room_for_the_quarter_step_margin_is_refused():
    with pytest.raises(st.SourceTreatmentRefused) as e:
        treat.pts_offset({"time_base": "1/60", "pts_step": 2, "start_pts": 0}, 10, "0.000", "0")
    assert e.value.reason == "source_clock_refused"


def test_a_graph_that_does_not_open_with_the_split_is_not_prefixed():
    with pytest.raises(ValueError):
        treat.prefixed("[0:v]scale=2:2[vout]", "[1:v]null[stsrc]")


def test_decoded_frames_off_the_expected_pts_run_are_refused(src, tmp_path):
    from services.clipper import source_treatment_patch as stp

    clock = {"time_base": "1/15360", "pts_step": 256, "start_pts": 0}  # half the real step
    with pytest.raises(st.SourceTreatmentRefused) as e:
        list(stp._decode(str(src), clock, 20, 10, (0, 0, 64, 64), tmp_path / "d.log"))
    assert e.value.reason == "patch_build_failed"


def test_a_truncated_frame_from_the_decoder_is_refused(tmp_path, monkeypatch):
    import io

    from services.clipper import source_treatment_patch as stp

    class Truncating:
        def __init__(self, *a, **kw):
            self.stdout = io.BytesIO(b"\0" * 30)  # one 4x4 frame (24 bytes) and 6 more

        def wait(self):
            return 0

    monkeypatch.setattr(stp.subprocess, "Popen", Truncating)
    clock = {"time_base": "1/15360", "pts_step": 512, "start_pts": 0}
    with pytest.raises(st.SourceTreatmentRefused) as e:
        list(stp._decode("x.mp4", clock, 0, 2, (0, 0, 4, 4), tmp_path / "d.log"))
    assert e.value.reason == "patch_build_failed" and "truncated" in e.value.detail


@pytest.mark.parametrize("treatment", ["blur", "erase"])
def test_a_decode_that_came_up_short_is_refused(src, tmp_path, monkeypatch, treatment):
    import numpy as np

    from services.clipper import source_treatment_patch as stp

    s = Synth(tmp_path, src)
    d = s.decision(s.setting(treatment))
    r = d["source_treatment"]
    geom = stp.geometry(s.mask(), r["per_line"], r["params"])

    def short(src_, clock, k_from, count, box, log):
        w, h = box[2] - box[0], box[3] - box[1]
        for i in range(max(0, count - 5)):
            yield (k_from + i, np.zeros((h, w), np.uint8), np.zeros((h // 2, w // 2), np.uint8),
                   np.zeros((h // 2, w // 2), np.uint8))

    monkeypatch.setattr(stp, "_decode", short)
    with pytest.raises(st.SourceTreatmentRefused) as e:
        stp.write_patch(str(src), s.mask(), r["per_line"], r["params"], geom, tmp_path / "p.yuv")
    assert e.value.reason == "patch_build_failed"


def test_an_executed_patch_without_a_corroborated_record_writes_no_identity():
    for record in ({}, {"source_treatment": {"corroborated": False}},
                   {"source_treatment": {"corroborated": True, "identity": {"x": 1},
                                         "patch": {"sha256_before": "a", "sha256_after": "b"}}}):
        with pytest.raises(st.SourceTreatmentRefused) as e:
            treat.sidecar_identity(record, {"file": "p.yuv"})
        assert e.value.reason == "record_not_corroborated"
    assert treat.sidecar_identity({}, None) == {"schema": st.IDENTITY_SCHEMA, "treatment": "none"}


@pytest.mark.parametrize("rewrite", ["indented", "mutable", "no_version"])
async def test_a_params_record_that_is_not_immutable_and_canonical_is_refused(src, tmp_path, original,
                                                                               rewrite):
    import json as _json

    s = Synth(tmp_path, src)
    d = s.decision(s.setting("erase"))
    doc = _json.loads(s.params_path.read_bytes())
    if rewrite == "indented":
        data = _json.dumps(doc, indent=1, sort_keys=True).encode()
    else:
        doc.pop("immutable") if rewrite == "mutable" else doc.pop("version")
        data = st.canonical_bytes(doc)
    s.params_path.write_bytes(data)  # re-stamped: the .sha256 is not what refuses it
    Path(f"{s.params_path}.sha256").write_text(hashlib.sha256(data).hexdigest(), encoding="utf-8")
    await _refuses("params_missing", s, d)


def test_a_footprint_decode_that_came_up_short_is_refused(src, tmp_path, monkeypatch):
    from services.clipper import source_treatment_patch as stp

    s = Synth(tmp_path, src)
    r = s.decision(s.setting("erase"))["source_treatment"]
    geom = stp.geometry(s.mask(), r["per_line"], r["params"])
    monkeypatch.setattr(stp, "_decode", lambda *a, **kw: iter(()))
    with pytest.raises(st.SourceTreatmentRefused) as e:
        stp._footprints(str(src), s.mask(), r["per_line"], r["params"], geom["context"],
                        tmp_path / "f.log")
    assert e.value.reason == "patch_build_failed" and "frames decoded" in e.value.detail

