"""The source-caption mask, verified from its bytes, and the treatment manifest.

SC batch 1 (SC-addendum-v2 §2.2, §6, §8 row 1; codex-verdict-next-10 §1, §3).
Builders come from test_clipper_source_treatment.py. The decisive identity
test is `test_two_attempts_of_one_recipe_differ_in_manifest_hash_not_in_identity`:
the manifest FILE hash binds an attempt, the semantic projection does not.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from services.clipper import source_treatment as st
from services.clipper import source_treatment_manifest as stm
from test_clipper_source_treatment import (END, K_FIRST, K_LAST, PARAMS, SRC_SHA, START, identity,
                                           load, mask_doc, write_mask)


def _refuses(reason: str, detail: str, fn, *a, **kw) -> None:
    with pytest.raises(st.SourceTreatmentRefused) as e:
        fn(*a, **kw)
    assert e.value.reason == reason, e.value
    assert detail in e.value.detail, e.value


def _mask_refuses(tmp_path, doc, reason, detail="", **kw) -> None:
    _refuses(reason, detail, load, write_mask(tmp_path, doc), **kw)


# ── the mask ─────────────────────────────────────────────────────────────────

def test_a_valid_mask_loads_with_each_lines_frames(tmp_path):
    m = load(write_mask(tmp_path))
    assert m["window"] == (K_FIRST, K_LAST)
    assert m["line_runs"] == {"L0001": (K_FIRST, 7111), "b23c-L30": (7113, 7178),
                              "L0003": (7180, K_LAST + 1)}


def test_bytes_that_are_not_canonical_are_refused(tmp_path):
    data = json.dumps(mask_doc(), indent=1, sort_keys=True).encode("utf-8")
    p = write_mask(tmp_path).parent / f"{hashlib.sha256(data).hexdigest()}.json"
    p.write_bytes(data)
    _refuses("mask_not_canonical", "", load, p)


def test_a_mask_not_named_by_its_hash_is_refused(tmp_path):
    p = write_mask(tmp_path)
    q = p.with_name("mask.json")
    q.write_bytes(p.read_bytes())
    _refuses("mask_hash_mismatch", "hashes to", load, q)


def test_missing_or_garbled_mask_is_unreadable(tmp_path):
    _refuses("mask_unreadable", "", load, tmp_path / "nothing.json")
    p = tmp_path / "x.json"
    p.write_bytes(b"{not json")
    _refuses("mask_unreadable", "", load, p)


def test_another_schema_is_refused(tmp_path):
    doc = mask_doc()
    doc["schema"] = "a_whole_clip_reading_v1"
    _mask_refuses(tmp_path, doc, "mask_invalid", "schema")


@pytest.mark.parametrize("ident,detail", [
    (None, "no source identity"),
    ({**identity(), "sha256": "cd" * 32}, "mask was made on"),
    ({**identity(), "size": 1}, "mask was made on"),
    (identity(width=1280), "stream width"),
    (identity(start_pts=512), "stream start_pts"),
])
def test_a_mask_of_another_source_is_refused(tmp_path, ident, detail):
    _mask_refuses(tmp_path, mask_doc(), "mask_source_mismatch", detail, source_identity=ident)


def test_the_mask_must_have_hashed_its_source_in_full(tmp_path):
    doc = mask_doc()
    doc["source"]["sha256_how"] = "cache_reuse"
    _mask_refuses(tmp_path, doc, "mask_invalid", "in full")


def test_a_mask_of_another_clip_is_refused(tmp_path):
    _mask_refuses(tmp_path, mask_doc(), "mask_clip_mismatch", "", clip_id="6053a598cf06")


def test_vfr_is_refused(tmp_path):
    doc = mask_doc()
    doc["source"]["stream"]["avg_frame_rate"] = "2997/100"
    _mask_refuses(tmp_path, doc, "source_clock_refused", "VFR",
                  source_identity=identity(avg_frame_rate="2997/100"))


@pytest.mark.parametrize("key,value,detail", [
    ("time_base", "1/30000", "not the stream's"),
    ("start_pts", 7, "not the stream's"),
    ("pts_step", 256, "is not one frame"),
    ("pts_step", True, "not an integer"),
    ("fps", "0/1", "positive rational"),
])
def test_a_clock_that_is_not_the_streams_frame_step_is_refused(tmp_path, key, value, detail):
    doc = mask_doc()
    doc["clock"][key] = value
    reason = "mask_invalid" if key in ("fps",) or value is True else "source_clock_refused"
    _mask_refuses(tmp_path, doc, reason, detail)


@pytest.mark.parametrize("key,value,detail", [("off_step", 3, "off the pts step"),
                                              ("frames", 91, "exactly the window")])
def test_a_cfr_check_that_failed_or_missed_the_window_is_refused(tmp_path, key, value, detail):
    doc = mask_doc()
    doc["clock"]["cfr_check"][key] = value
    _mask_refuses(tmp_path, doc, "source_clock_refused", detail)


def test_a_strip_off_the_pts_step_is_refused(tmp_path):
    doc = mask_doc()
    doc["inspection"]["strips"][1]["pts"] += 17
    _mask_refuses(tmp_path, doc, "source_clock_refused", "off the step")


@pytest.mark.parametrize("start,end", [(236.9, END), (START, 240.0)])
def test_a_trim_outside_the_window_is_refused(tmp_path, start, end):
    _mask_refuses(tmp_path, mask_doc(), "mask_window_does_not_cover_clip", "the clip reads",
                  clip_start=start, clip_end=end)


def test_a_trim_inside_the_window_and_the_stream_clamp_are_accepted(tmp_path):
    p = write_mask(tmp_path)
    assert load(p, clip_start=238.0, clip_end=239.0)["window"] == (K_FIRST, K_LAST)
    # 240.5 s reads past the window, but the stream ends at k 7199.
    assert load(p, clip_end=240.5, source_identity=identity(nb_frames="7200"))


def test_the_window_edge_is_read_from_the_seconds_text_not_the_binary_float(tmp_path):
    """237.1 s is 7113 frames; the binary float is 7112.999…. The window's first
    frame is 7111 exactly — one frame less of margin and the mask is refused."""
    doc = mask_doc()
    doc["window"]["k_first"] = 7111
    doc["clock"]["cfr_check"]["frames"] = K_LAST - 7111 + 1
    doc["frames"] = [{"k_from": 7111, "k_to": 7113, "state": "none_observed", "basis": "viewed"}] \
        + doc["frames"][2:]
    p = write_mask(tmp_path, doc)
    assert load(p, clip_start=237.1)["window"] == (7111, K_LAST)
    _refuses("mask_window_does_not_cover_clip", "", load, p, clip_start=237.0)


@pytest.mark.parametrize("change,detail", [
    (lambda d: d["window"].update(margin_frames=3), "margin_frames"),
    (lambda d: d["window"].update(k_first=-1), "window ["),
    (lambda d: d["window"].update(k_last=K_FIRST - 1), "window ["),
])
def test_window_declarations_are_checked(tmp_path, change, detail):
    doc = mask_doc()
    change(doc)
    _mask_refuses(tmp_path, doc, "mask_invalid", detail)


@pytest.mark.parametrize("start,end,detail", [(True, END, "clip bound"), (START, float("nan"), "clip bound"),
                                              (END, START, "clip bounds")])
def test_clip_bounds_are_checked(tmp_path, start, end, detail):
    _mask_refuses(tmp_path, mask_doc(), "mask_invalid", detail, clip_start=start, clip_end=end)


def test_unknown_frames_in_the_window_are_refused_with_the_denominator(tmp_path):
    doc = mask_doc()
    doc["frames"][1]["state"] = "unknown"
    _mask_refuses(tmp_path, doc, "unknown_frames_in_window", f"2 of {K_LAST - K_FIRST + 1}")


def test_an_unviewed_detector_only_run_is_refused(tmp_path):
    doc = mask_doc()
    doc["frames"][2]["basis"] = "detector_only"
    _mask_refuses(tmp_path, doc, "unverified_run", "65 of")


@pytest.mark.parametrize("change,detail", [
    (lambda f: f[0].update(k_from=K_FIRST + 1), "does not continue"),
    (lambda f: f[1].update(k_from=7112), "does not continue"),
    (lambda f: f[-1].update(k_to=K_LAST), "frames end at"),
    (lambda f: f[1].update(state="text"), "state"),
    (lambda f: f[1].update(state="line:L0099"), "names no line"),
    (lambda f: f[1].update(basis="guessed"), "basis"),
    (lambda f: f[1].update(k_to=True), "not an integer"),
    (lambda f: f.clear(), "non-empty"),
])
def test_frames_must_cover_exactly_the_window(tmp_path, change, detail):
    doc = mask_doc()
    change(doc["frames"])
    _mask_refuses(tmp_path, doc, "mask_invalid", detail)


def test_a_line_in_two_separate_runs_is_refused(tmp_path):
    doc = mask_doc()
    doc["frames"][0:2] = [
        {"k_from": K_FIRST, "k_to": 7110, "state": "line:L0001", "basis": "viewed"},
        {"k_from": 7110, "k_to": 7111, "state": "none_observed", "basis": "viewed"},
        {"k_from": 7111, "k_to": 7113, "state": "line:L0001", "basis": "viewed"}]
    _mask_refuses(tmp_path, doc, "mask_invalid", "two separate runs")


def test_adjacent_runs_of_one_line_with_different_bases_are_one_run(tmp_path):
    doc = mask_doc()
    doc["frames"][2:3] = [{"k_from": 7113, "k_to": 7150, "state": "line:b23c-L30", "basis": "viewed"},
                          {"k_from": 7150, "k_to": 7178, "state": "line:b23c-L30",
                           "basis": "strip+detector"}]
    assert load(write_mask(tmp_path, doc))["line_runs"]["b23c-L30"] == (7113, 7178)


@pytest.mark.parametrize("lid,k_on,k_off", [("b23c-L30", 7114, 7178), ("L0001", 7109, 7111),
                                            ("L0003", 7180, 7190)])
def test_frames_must_agree_with_each_lines_own_bounds(tmp_path, lid, k_on, k_off):
    """Including an open edge: L0001's `k_on: null` means its run reaches the
    window's first frame; giving it an observed start inside the window is a
    contradiction, not a refinement."""
    doc = mask_doc()
    line = next(x for x in doc["lines"] if x["id"] == lid)
    line.update(k_on=k_on, k_off=k_off)
    _mask_refuses(tmp_path, doc, "mask_invalid", f"{lid}: frames give")


@pytest.mark.parametrize("field,value,detail", [
    ("karaoke", True, "karaoke"), ("karaoke", 0, "karaoke"), ("karaoke", None, "karaoke"),
    ("k_on", True, "not an integer"), ("k_on", "7113", "not an integer"),
    ("k_off", 7113, "is not before"), ("id", "L0001", "repeated"),
    ("rect", {"x0": 787, "x1": 1775, "y0": 1320, "y1": 1387}, "even-aligned"),
    ("rect", {"x0": 786, "x1": 1776, "y0": 1320, "y1": 1387}, "even-aligned"),
    ("rect", {"x0": 786, "x1": 1921, "y0": 1320, "y1": 1387}, "outside"),
    ("glyph_png", {"file": "g/b23c-L30.png", "sha256": "short"}, "glyph_png file/sha256"),
])
def test_line_fields_are_checked(tmp_path, field, value, detail):
    doc = mask_doc()
    doc["lines"][1][field] = value
    _mask_refuses(tmp_path, doc, "mask_invalid", detail)


def test_lines_and_strips_must_be_lists(tmp_path):
    doc = mask_doc()
    doc["lines"] = {}
    _mask_refuses(tmp_path / "a", doc, "mask_invalid", "lines is not a list")
    doc = mask_doc()
    doc["inspection"]["strips"] = None
    _mask_refuses(tmp_path / "b", doc, "mask_invalid", "inspection.strips")


def test_a_line_observed_to_start_before_the_window_is_clipped_to_it(tmp_path):
    """An OBSERVED k_on before the window is not an open edge: the line's run
    starts at the window's first frame and its own k_on is kept as observed."""
    doc = mask_doc()
    doc["lines"][0]["k_on"] = 7000
    m = load(write_mask(tmp_path, doc))
    assert m["line_runs"]["L0001"] == (K_FIRST, 7111)
    got = st.resolve(None, {"treatment": "erase", "decided_by": "human",
                            "mask_sha256": m["sha256"]}, m, PARAMS)
    assert got["per_line"][0]["line_on"] == 7000


def test_a_missing_glyph_png_is_refused(tmp_path):
    p = write_mask(tmp_path)
    (p.parent / "g" / "b23c-L30.png").unlink()
    _refuses("glyph_missing", "b23c-L30", load, p)


def test_an_altered_png_under_an_unchanged_mask_json_is_refused(tmp_path):
    p = write_mask(tmp_path)
    json_before = p.read_bytes()
    (p.parent / "g" / "L0003.png").write_bytes(b"\x89PNG\r\n\x1a\nretouched")
    assert p.read_bytes() == json_before
    _refuses("glyph_hash_mismatch", "L0003", load, p)


def test_a_glyph_outside_the_store_is_refused_not_looked_up(tmp_path):
    doc = mask_doc()
    doc["lines"][0]["glyph_png"]["file"] = "../elsewhere/L0001.png"
    _mask_refuses(tmp_path, doc, "mask_invalid", "outside the mask store")


# ── the manifest and the fingerprint's projection ────────────────────────────

OVERLAY = ("[2:v]setparams=range=tv:colorspace=bt709[stp];[0:v][stp]overlay=0:0:eof_action=pass"
           ":repeatlast=0:format=yuv420,format=yuv420p")


def _resolved(mask, overrides=None, params=PARAMS) -> dict:
    clip = {"treatment": "erase", "decided_by": "human", "mask_sha256": mask["sha256"]}
    if overrides:
        clip["overrides"] = overrides
    got = st.resolve(None, clip, mask, params)
    assert got["state"] == "resolved", got
    return got


def _attempt(root: Path, resolved: dict, mk: dict, *, job="job-1", nonce="n-1",
             patch=b"raw yuv420p patch", how="full_hash", generator="gen-a", **doc_kw):
    scratch = root / f"scratch-{job}-{nonce}"
    scratch.mkdir(parents=True, exist_ok=True)
    pf = scratch / "patch.yuv"
    pf.write_bytes(patch)
    doc = {"schema": st.MANIFEST_SCHEMA, "attempt": {"job_id": job, "nonce": nonce},
           "source": {"sha256": SRC_SHA, "sha256_how": how, "stream": mk["doc"]["source"]["stream"]},
           "mask": {"sha256": mk["sha256"]},
           "glyphs": [{"line_id": lid, "png_sha256": x["glyph_png"]["sha256"]}
                      for lid, x in sorted(mk["lines"].items())],
           "params": {"version": resolved["params_version"], "values": resolved["params"]},
           "window": resolved["window"], "pts_offset": -18, "frames": stm.treated_frames(resolved),
           "patch": {"file": str(pf), "sha256": hashlib.sha256(patch).hexdigest(), "bytes": len(patch),
                     "frames": K_LAST - K_FIRST + 2, "pix_fmt": "yuv420p", "w": 1920, "h": 1440,
                     "tags": "tv/bt709", "spare_trailing_frame": True},
           "overlay_filter": OVERLAY,
           "generator": {"module_sha256": generator, "ffmpeg_build": "7.1", "opencv_version": "4.10"}}
    doc.update(doc_kw)
    data = st.canonical_bytes(doc)
    mf = scratch / "treatment_manifest.json"
    mf.write_bytes(data)
    return {"path": mf, "sha": hashlib.sha256(data).hexdigest(), "scratch": scratch,
            "attempt": {"job_id": job, "nonce": nonce}, "doc": doc, "patch": pf}


def _verify(a: dict, resolved: dict, mask: dict, **kw) -> dict:
    args = {"expected_sha256": a["sha"], "attempt": a["attempt"], "scratch_dir": a["scratch"],
            "resolved": resolved, "mask": mask}
    args.update(kw)
    return stm.verify_manifest(a["path"], **args)


@pytest.fixture
def m(tmp_path) -> dict:
    return load(write_mask(tmp_path))


def test_two_attempts_of_one_recipe_differ_in_manifest_hash_not_in_identity(tmp_path, m):
    r = _resolved(m)
    one = _attempt(tmp_path, r, m)
    two = _attempt(tmp_path, r, m, job="job-2", nonce="n-2", how="cache_reuse", generator="gen-b")
    assert one["sha"] != two["sha"]
    v1, v2 = _verify(one, r, m), _verify(two, r, m)
    assert (v1["manifest_sha256"], v2["manifest_sha256"]) == (one["sha"], two["sha"])
    assert v1["identity"] == v2["identity"]
    assert v1["identity_sha256"] == v2["identity_sha256"]
    text = st.canonical_bytes(v1["identity"]).decode()
    for absent in ("job-1", "n-1", "scratch", "full_hash", "gen-a", one["sha"], "verified_at"):
        assert absent not in text


@pytest.mark.parametrize("vary", ["treatment", "frame", "param", "version", "patch", "offset"])
def test_a_relevant_change_changes_the_identity(tmp_path, m, vary):
    r = _resolved(m)
    base = _verify(_attempt(tmp_path / "a", r, m), r, m)["identity_sha256"]
    r2, kw = r, {}
    if vary == "treatment":
        r2 = _resolved(m, [{"k_from": 7180, "k_to": K_LAST + 1, "treatment": "blur"}])
    elif vary == "frame":
        r2 = _resolved(m, [{"k_from": 7111, "k_to": 7113, "treatment": "none"},
                           {"k_from": 7113, "k_to": 7178, "treatment": "none"}])
    elif vary == "param":
        r2 = _resolved(m, params={"version": "sc1-test-v1",
                                  "values": {"erase": {"telea_r": [8, 5], "dilation": 17}}})
    elif vary == "version":
        r2 = _resolved(m, params={"version": "sc1-test-v2", "values": PARAMS["values"]})
    elif vary == "patch":
        kw = {"patch": b"another raw patch"}
    else:
        kw = {"pts_offset": -17}
    got = _verify(_attempt(tmp_path / "b", r2, m, **kw), r2, m)["identity_sha256"]
    assert got != base


def test_binding_another_attempts_manifest_is_refused_even_for_the_same_recipe(tmp_path, m):
    r = _resolved(m)
    one, two = _attempt(tmp_path, r, m), _attempt(tmp_path, r, m, job="job-2", nonce="n-2")
    _refuses("manifest_of_another_attempt", "this attempt is", _verify, one, r, m,
             attempt=two["attempt"], scratch_dir=two["scratch"])
    # Same attempt label, but the patch it names lives in the other attempt's dir.
    _refuses("manifest_of_another_attempt", "scratch dir", _verify, one, r, m,
             scratch_dir=two["scratch"])


def test_the_manifest_the_decision_did_not_carry_is_refused(tmp_path, m):
    r = _resolved(m)
    a = _attempt(tmp_path, r, m)
    _refuses("manifest_hash_mismatch", "", _verify, a, r, m, expected_sha256="00" * 32)
    a["path"].unlink()
    _refuses("manifest_missing", "", _verify, a, r, m)


def test_a_manifest_that_is_not_canonical_is_refused(tmp_path, m):
    r = _resolved(m)
    a = _attempt(tmp_path, r, m)
    data = json.dumps(a["doc"], indent=1).encode()
    a["path"].write_bytes(data)
    _refuses("manifest_invalid", "canonical", _verify, a, r, m,
             expected_sha256=hashlib.sha256(data).hexdigest())
    a["path"].write_bytes(b"\xff")
    _refuses("manifest_invalid", "", _verify, a, r, m, expected_sha256=hashlib.sha256(b"\xff").hexdigest())


@pytest.mark.parametrize("key,value,reason", [
    ("schema", "clipper_source_treatment_manifest_v0", "manifest_invalid"),
    ("mask", {"sha256": "cd" * 32}, "manifest_mask_mismatch"),
    ("source", {"sha256": "cd" * 32, "stream": {}}, "mask_source_mismatch"),
    ("glyphs", [], "glyph_hash_mismatch"),
    ("params", {"version": "sc1-test-v2", "values": PARAMS["values"]}, "manifest_params_mismatch"),
    ("window", {"k_first": K_FIRST, "k_last": K_LAST + 1}, "manifest_frames_mismatch"),
    ("frames", [], "manifest_frames_mismatch"),
])
def test_a_manifest_not_bound_to_this_resolution_is_refused(tmp_path, m, key, value, reason):
    r = _resolved(m)
    a = _attempt(tmp_path, r, m, **{key: value})
    _refuses(reason, "", _verify, a, r, m)


def test_a_manifest_whose_source_stream_differs_is_refused(tmp_path, m):
    r = _resolved(m)
    stream = dict(m["doc"]["source"]["stream"], width=1280)
    a = _attempt(tmp_path, r, m, source={"sha256": SRC_SHA, "stream": stream})
    _refuses("mask_source_mismatch", "", _verify, a, r, m)


def test_the_resolution_must_name_the_loaded_mask(tmp_path, m):
    r = _resolved(m)
    a = _attempt(tmp_path, r, m)
    _refuses("manifest_mask_mismatch", "", _verify, a, dict(r, mask_sha256="cd" * 32), m)


def test_a_patch_altered_after_the_manifest_is_refused(tmp_path, m):
    r = _resolved(m)
    a = _attempt(tmp_path, r, m)
    a["patch"].write_bytes(b"raw yuv420p patcH")
    _refuses("patch_hash_mismatch", "", _verify, a, r, m)
    a["patch"].write_bytes(b"raw yuv420p patch" + b"\0")
    _refuses("patch_hash_mismatch", "", _verify, a, r, m)
    a["patch"].unlink()
    _refuses("patch_missing", "", _verify, a, r, m)


def test_a_patch_whose_byte_count_is_wrong_is_refused(tmp_path, m):
    r = _resolved(m)
    a = _attempt(tmp_path, r, m)
    doc = copy.deepcopy(a["doc"])
    doc["patch"]["bytes"] += 1
    data = st.canonical_bytes(doc)
    a["path"].write_bytes(data)
    _refuses("patch_hash_mismatch", "", _verify, a, r, m, expected_sha256=hashlib.sha256(data).hexdigest())


def test_none_has_the_canonical_identity_and_active_needs_a_manifest(m):
    assert stm.fingerprint_identity(st.resolve()) == stm.NONE_IDENTITY
    assert set(stm.NONE_IDENTITY) == {"schema", "treatment"}
    _refuses("manifest_missing", "", stm.fingerprint_identity, _resolved(m))


def test_treated_frames_cover_the_window_and_name_the_seam(m, tmp_path):
    from test_clipper_source_treatment import answer_file, split_setting
    r = st.resolve(None, split_setting(m, answer_file(tmp_path)), m, PARAMS)
    runs = [(f["k_from"], f["k_to"], f["treatment"]) for f in stm.treated_frames(r)]
    assert runs == [(K_FIRST, 7111, "blur"), (7111, 7113, None), (7113, 7158, "blur"),
                    (7158, 7178, "erase"), (7178, 7180, None), (7180, K_LAST + 1, "erase")]
