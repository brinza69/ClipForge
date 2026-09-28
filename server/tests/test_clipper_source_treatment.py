"""What to do with the source's own burned subtitle: resolve, the split, the gate.

SC batch 1 (SC-addendum-v2 §8 row 1, codex-verdict-next-10 §1). The module is
wired to nothing, so everything here is driven with explicit inputs: a
synthetic mask around the F4/F5 seam of b23c, and the person's answer file
reproduced BYTE FOR BYTE — its sha256 is asserted against the one recorded in
A's record, so the parser is tested on what the person actually saved, not on
a paraphrase of it.

The mask and manifest checks live in test_clipper_source_treatment_manifest.py,
which imports the builders below.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from services.clipper import source_treatment as st
from services.clipper import source_treatment_manifest as stm
from services.clipper import source_treatment_mask as smask

CLIP = "b23c14c41495"
#: The pilot source's full hash (B\gates\sc0\identity.json) — the approved
#: F4/F5 record names it, so the builders use it.
SRC_SHA = "351a96b6b462a21e95dc31466ead17b190858ac695327faedc60443a397405b4"
STEP, TB = 512, "1/15360"
#: b23c around the seam: the clip reads [7108, 7199] for these bounds.
START, END = 237.0, 239.9
K_FIRST, K_LAST = 7108, 7199
SEAM, SEAM_PTS = 7158, 3664896

#: The person's answer file, exactly as the page saved it (CRLF, no final LF).
ANSWER_BYTES = (
    "DE1 + SC, răspunsurile omului\r\n"
    "1. dea939, care variantă e mai bună: varianta nouă | de ce: -\r\n"
    "2. 3e42, te deranjează ceva la final: da | ce: e pauza putin prea scurta cand termina video ul\r\n"
    "3. b23c, linia F4/F5: c — schimbarea la 238,6 s, cum am ales pe fragmente\r\n"
    "observații: -").encode("utf-8")
ANSWER_SHA = "3a39378ddc123107858474f2371aff51c09d52a5da7cab29ef40f0aed0eed1e3"

PARAMS = {"version": "sc1-test-v1",
          "values": {"blur": {"sigma": [16, 8], "context": 64},
                     "erase": {"telea_r": [8, 4], "dilation": 17}}}


def identity(**stream) -> dict:
    """What `proxy_provenance.media_identity` returns, with a declared reuse."""
    s = {"codec_name": "h264", "width": 1920, "height": 1440, "time_base": TB,
         "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "start_time": "0.000000",
         "start_pts": 0, "nb_frames": "9000", "duration_ts": 4608000}
    s.update(stream)
    return {"path": "source.mp4", "size": 123456, "sha256": SRC_SHA, "sha256_how": "cache_reuse",
            "fingerprint": {}, "stream": s, "format": {}}


def _line(lid: str, k_on, k_off, text: str) -> dict:
    return {"id": lid, "text": text, "k_on": k_on, "k_off": k_off,
            "rect": {"x0": 786, "x1": 1775, "y0": 1320, "y1": 1387},
            "uncertainty_px": {"x": 2, "y": 2}, "karaoke": False,
            "glyph_png": {"file": f"g/{lid}.png", "sha256": hashlib.sha256(_png(lid)).hexdigest(),
                          "w": 990, "h": 68, "origin": {"x": 786, "y": 1320}},
            "style": "plain_white_soft_shadow_one_line",
            "footprint_rule": {"tophat": 15, "tophat_min": 40, "luma_min": 170, "persist": 0.5},
            "dilation": {"shape": "ellipse", "size": 17}}


def _png(lid: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + f"glyph of {lid}".encode()


def mask_doc() -> dict:
    """L0001 is on before the window (open start), b23c-L30 is the F4/F5 line
    (k 7113..7177, the seam at 7158), L0003 is still on after it (open end)."""
    frames = [(K_FIRST, 7111, "line:L0001", "viewed"), (7111, 7113, "none_observed", "viewed"),
              (7113, 7178, "line:b23c-L30", "strip+detector"), (7178, 7180, "none_observed", "viewed"),
              (7180, K_LAST + 1, "line:L0003", "strip+detector")]
    return {
        "schema": st.MASK_SCHEMA,
        "conventions": {"k": "(pts-start_pts)/pts_step", "interval": "half-open [k_on,k_off)"},
        "source": {"sha256": SRC_SHA, "sha256_how": "full_hash", "size": 123456,
                   "stream": {k: identity()["stream"][k] for k in smask.IDENTITY_STREAM_KEYS}
                   | {"pix_fmt": "yuv420p", "color_range": "tv", "color_space": "bt709"},
                   "verified_at": "2026-09-26T20:00:00Z", "verified_by": "A"},
        "clock": {"time_base": TB, "pts_step": STEP, "start_pts": 0, "fps": "30/1",
                  "cfr_check": {"how": "ffprobe -show_frames pts over window",
                                "frames": K_LAST - K_FIRST + 1, "off_step": 0}},
        "clip": {"project_id": "pilotf81b", "clip_id": CLIP, "start_time": START, "end_time": END},
        "window": {"k_first": K_FIRST, "k_last": K_LAST, "margin_frames": 2,
                   "rule": "floor(start*fps)-2 .. ceil(end*fps)+2"},
        "lines": [_line("L0001", None, 7111, "previous line"),
                  _line("b23c-L30", 7113, 7178, "not doing let's see if we could get it to focus"),
                  _line("L0003", 7180, None, "right 20K steps okay")],
        "frames": [{"k_from": a, "k_to": b, "state": s, "basis": basis} for a, b, s, basis in frames],
        "inspection": {"strips": [{"k": k, "pts": k * STEP, "read": "line:b23c-L30"}
                                  for k in (7116, 7122, 7158)],
                       "brackets": [], "uncertain": [], "no_text": []},
        "agreement": {"frames_total": K_LAST - K_FIRST + 1, "frames_agree": K_LAST - K_FIRST + 1,
                      "disagreements": []},
        "provenance": {"author": "A", "method": "annotation+template"},
    }


def write_mask(root: Path, doc: dict | None = None, *, pngs: bool = True) -> Path:
    doc = mask_doc() if doc is None else doc
    store = root / "masks"
    (store / "g").mkdir(parents=True, exist_ok=True)
    if pngs:
        for line in doc["lines"]:
            (store / "g" / f"{line['id']}.png").write_bytes(_png(line["id"]))
    data = st.canonical_bytes(doc)
    path = store / f"{hashlib.sha256(data).hexdigest()}.json"
    path.write_bytes(data)
    return path


def load(path: Path, **kw) -> dict:
    args = {"source_identity": identity(), "clip_id": CLIP, "clip_start": START, "clip_end": END}
    args.update(kw)
    return smask.load_mask(path, **args)


def answer_file(root: Path, data: bytes = ANSWER_BYTES) -> Path:
    p = root / "answer.txt"
    p.write_bytes(data)
    return p


def split_setting(mask: dict, answer_path: Path, **split_kw) -> dict:
    """Option (c) as the person chose it: blur up to the seam, erase from it.
    The authorisation only REFERS to the approved record (next-13 §1 R2)."""
    auth = {"question_id": "sc-split-line-F4F5", "answer_file": str(answer_path)}
    split = {"line_id": "b23c-L30", "k_split": SEAM, "pts_split": SEAM_PTS, "authorisation": auth}
    auth_kw = {k: split_kw.pop(k) for k in list(split_kw) if k in auth}
    auth.update(auth_kw)
    split.update(split_kw)
    return {"treatment": "erase", "decided_by": "human", "mask_sha256": mask["sha256"],
            "overrides": [{"k_from": K_FIRST, "k_to": SEAM, "treatment": "blur", "split": split}]}


@pytest.fixture
def mask(tmp_path) -> dict:
    return load(write_mask(tmp_path))


@pytest.fixture
def answer(tmp_path) -> Path:
    return answer_file(tmp_path)


def refused(got: dict, reason: str, detail: str = "") -> None:
    """A refusal is its own state: no `treatment` key for anybody to read as `none`."""
    assert got["state"] == "refused", got
    assert "treatment" not in got
    assert got["reason"] == reason, got
    assert detail in got["detail"], got


# ── defaults and who decided ─────────────────────────────────────────────────

def test_nothing_stored_is_none_by_default_and_needs_no_mask():
    got = st.resolve()
    assert got == {"schema": st.CONFIG_SCHEMA, "state": "resolved", "treatment": "none",
                   "decided_by": "default", "scope": "default", "active": False}


def test_a_persons_none_is_human_not_default_and_has_the_same_identity():
    default = st.resolve()
    human = st.resolve({"treatment": "none", "decided_by": "human"})
    assert (default["decided_by"], human["decided_by"]) == ("default", "human")
    assert human["scope"] == "project"
    ids = [stm.fingerprint_identity(r) for r in (default, human)]
    assert ids[0] == ids[1] == {"schema": st.IDENTITY_SCHEMA, "treatment": "none"}
    assert stm.treatment_identity_sha256(ids[0]) == stm.treatment_identity_sha256(ids[1])


def test_clip_beats_project_both_ways(mask):
    project = {"treatment": "erase", "decided_by": "human"}
    got = st.resolve(project, {"treatment": "none", "decided_by": "human"}, mask, PARAMS)
    assert (got["treatment"], got["scope"], got["active"]) == ("none", "clip", False)
    got = st.resolve({"treatment": "none", "decided_by": "human"},
                     {"treatment": "blur", "decided_by": "human", "mask_sha256": mask["sha256"]},
                     mask, PARAMS)
    assert (got["treatment"], got["scope"], got["active"]) == ("blur", "clip", True)
    assert {s["treatment"] for s in got["per_line"]} == {"blur"}


def test_project_inheritance_needs_the_clips_mask(mask):
    got = st.resolve({"treatment": "erase", "decided_by": "human"}, None, mask, PARAMS)
    assert (got["treatment"], got["scope"], got["mask_sha256"]) == ("erase", "project", mask["sha256"])
    refused(st.resolve({"treatment": "erase", "decided_by": "human"}), "mask_missing")


def test_open_edges_are_carried_never_invented(mask):
    got = st.resolve(None, {"treatment": "erase", "decided_by": "human",
                            "mask_sha256": mask["sha256"]}, mask, PARAMS)
    segs = {s["line_id"]: s for s in got["per_line"]}
    assert (segs["L0001"]["k_from"], segs["L0001"]["line_on"]) == (K_FIRST, st.CONTINUES)
    assert (segs["L0003"]["k_to"], segs["L0003"]["line_off"]) == (K_LAST + 1, st.CONTINUES)
    assert (segs["b23c-L30"]["line_on"], segs["b23c-L30"]["line_off"]) == (7113, 7178)


@pytest.mark.parametrize("bad", [1, True, "Erase", "erase ", "band", None])
def test_treatment_typos_are_refused_not_coerced(bad):
    refused(st.resolve({"treatment": bad, "decided_by": "human"}), "setting_invalid", "treatment")
    refused(st.resolve(None, {"treatment": bad, "decided_by": "human"}), "setting_invalid")


@pytest.mark.parametrize("who", ["default", None, True, "Human"])
def test_a_stored_value_must_be_a_persons(who):
    refused(st.resolve({"treatment": "none", "decided_by": who}), "setting_invalid", "decided_by")


def test_project_cannot_carry_overrides_and_settings_must_be_objects(mask):
    refused(st.resolve({"treatment": "erase", "decided_by": "human", "overrides": []}),
            "setting_invalid")
    refused(st.resolve("erase"), "setting_invalid")
    refused(st.resolve(None, {"treatment": "erase", "decided_by": "human",
                              "mask_sha256": mask["sha256"], "overrides": {}}, mask, PARAMS),
            "setting_invalid", "not a list")


@pytest.mark.parametrize("override,detail", [
    ({"k_from": True, "k_to": 7110, "treatment": "blur"}, "not inside"),
    ({"k_from": "7108", "k_to": 7110, "treatment": "blur"}, "not inside"),
    ({"k_from": 7110, "k_to": 7110, "treatment": "blur"}, "not inside"),
    ({"k_from": K_FIRST - 1, "k_to": 7110, "treatment": "blur"}, "not inside"),
    ({"k_from": 7108, "k_to": 7111, "treatment": 1}, "override treatment"),
    ({"k_from": 7108, "k_to": 7111, "treatment": "blur", "extra": 1}, "not an override"),
])
def test_override_typos_are_refused(mask, override, detail):
    clip = {"treatment": "erase", "decided_by": "human", "mask_sha256": mask["sha256"],
            "overrides": [override]}
    refused(st.resolve(None, clip, mask, PARAMS), "setting_invalid", detail)


def test_overlapping_overrides_are_refused(mask):
    clip = {"treatment": "erase", "decided_by": "human", "mask_sha256": mask["sha256"],
            "overrides": [{"k_from": 7108, "k_to": 7113, "treatment": "blur"},
                          {"k_from": 7111, "k_to": 7113, "treatment": "none"}]}
    refused(st.resolve(None, clip, mask, PARAMS), "setting_invalid", "overlap")


def test_the_clips_mask_hash_must_be_the_loaded_masks(mask):
    clip = {"treatment": "erase", "decided_by": "human", "mask_sha256": "cd" * 32}
    refused(st.resolve(None, clip, mask, PARAMS), "mask_hash_mismatch")


@pytest.mark.parametrize("params", [None, {}, {"version": "", "values": PARAMS["values"]},
                                    {"version": "v", "values": ["erase"]},
                                    {"version": "v", "values": {"blur": {}}}])
def test_an_active_treatment_needs_its_params(mask, params):
    clip = {"treatment": "erase", "decided_by": "human", "mask_sha256": mask["sha256"]}
    refused(st.resolve(None, clip, mask, params), "params_missing")


# ── the F4/F5 line: whole-line overrides and the one authorised split ─────────

def test_the_answer_file_is_the_one_the_person_saved(answer):
    assert hashlib.sha256(answer.read_bytes()).hexdigest() == ANSWER_SHA


def test_option_c_as_answered_resolves_to_the_seam_it_showed(mask, answer):
    got = st.resolve(None, split_setting(mask, answer), mask, PARAMS)
    assert got["state"] == "resolved", got
    runs = [(s["line_id"], s["k_from"], s["k_to"], s["treatment"]) for s in got["per_line"]]
    assert runs == [("L0001", K_FIRST, 7111, "blur"), ("b23c-L30", 7113, SEAM, "blur"),
                    ("b23c-L30", SEAM, 7178, "erase"), ("L0003", 7180, K_LAST + 1, "erase")]
    assert got["per_line"][1]["params"] == PARAMS["values"]["blur"]
    assert got["per_line"][2]["params"] == PARAMS["values"]["erase"]


def test_whole_line_options_a_and_b_need_no_answer(mask):
    base = {"treatment": "erase", "decided_by": "human", "mask_sha256": mask["sha256"]}
    a = st.resolve(None, base | {"overrides": [{"k_from": K_FIRST, "k_to": 7178,
                                                "treatment": "blur"}]}, mask, PARAMS)
    assert [s["treatment"] for s in a["per_line"] if s["line_id"] == "b23c-L30"] == ["blur"]
    b = st.resolve(None, base | {"overrides": [{"k_from": K_FIRST, "k_to": 7113,
                                                "treatment": "blur"}]}, mask, PARAMS)
    assert [s["treatment"] for s in b["per_line"] if s["line_id"] == "b23c-L30"] == ["erase"]


def test_no_answer_is_a_refusal_never_a_pick(mask):
    clip = split_setting(mask, Path("x"))
    del clip["overrides"][0]["split"]
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "no answer")


def test_missing_answer_file_is_refused(mask, tmp_path):
    clip = split_setting(mask, answer_file(tmp_path), answer_file=str(tmp_path / "gone.txt"))
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "unreadable")


def test_an_edited_answer_file_is_refused_by_the_records_hash(mask, tmp_path):
    p = answer_file(tmp_path, ANSWER_BYTES.replace(b"238,6", b"238,5"))
    refused(st.resolve(None, split_setting(mask, p), mask, PARAMS),
            "override_splits_a_line", "sha256 is not the approved record's")


def test_an_unknown_question_is_refused(mask, answer):
    clip = split_setting(mask, answer, question_id="sc-split-line-F1F2")
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "unknown question")


def test_another_line_is_refused(mask, answer):
    refused(st.resolve(None, split_setting(mask, answer, line_id="L0003"), mask, PARAMS),
            "override_splits_a_line", "split names 'L0003'")


def test_another_k_named_by_the_split_is_refused(mask, answer):
    clip = split_setting(mask, answer, k_split=7157, pts_split=7157 * STEP)
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "split names k 7157")


def test_a_split_at_another_k_than_the_record_is_refused(mask, answer):
    """The override moves the seam one frame earlier, consistently everywhere."""
    clip = split_setting(mask, answer, k_split=7157, pts_split=7157 * STEP)
    clip["overrides"][0]["k_to"] = 7157
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "about k 7158")


def test_the_answer_about_another_clip_is_refused(tmp_path, answer):
    doc = mask_doc()
    doc["clip"]["clip_id"] = "6053a598cf06"
    other = load(write_mask(tmp_path, doc), clip_id="6053a598cf06")
    refused(st.resolve(None, split_setting(other, answer), other, PARAMS),
            "override_splits_a_line", "clip 'b23c14c41495'")


def test_split_pts_must_be_the_split_frame_on_the_masks_clock(mask, answer):
    refused(st.resolve(None, split_setting(mask, answer, pts_split=SEAM_PTS + 1), mask, PARAMS),
            "split_pts_mismatch")


@pytest.mark.parametrize("split_kw,detail", [
    ({"k_split": "7158"}, "not integers"), ({"pts_split": True}, "not integers"),
    ({"extra": 1}, "not a split object"),
])
def test_split_typos_are_refused(mask, answer, split_kw, detail):
    refused(st.resolve(None, split_setting(mask, answer, **split_kw), mask, PARAMS),
            "setting_invalid", detail)


@pytest.mark.parametrize("change", [
    lambda a: "c", lambda a: dict(a, note="ok"),
    lambda a: {k: v for k, v in a.items() if k != "answer_file"},
    # a config may not carry the record's own fields, even with the right values
    lambda a: dict(a, answer_sha256=ANSWER_SHA), lambda a: dict(a, answer="c"),
    lambda a: dict(a, offered="c_switch_at_238.6.mp4"), lambda a: dict(a, clip_id=CLIP)])
def test_an_authorisation_that_is_not_one_is_refused(mask, answer, change):
    clip = split_setting(mask, answer)
    split = clip["overrides"][0]["split"]
    split["authorisation"] = change(split["authorisation"])
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "not an authorisation")


def test_a_split_where_no_line_is_split_is_refused(mask, answer):
    clip = split_setting(mask, answer)
    clip["overrides"][0]["k_to"] = 7113
    refused(st.resolve(None, clip, mask, PARAMS), "setting_invalid", "no line is split")


def test_one_override_splitting_two_lines_is_refused(mask, answer):
    clip = split_setting(mask, answer)
    clip["overrides"][0]["k_from"] = 7110
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "2 split lines")


# ── the gate ─────────────────────────────────────────────────────────────────

def _active(mask) -> dict:
    return st.resolve(None, {"treatment": "erase", "decided_by": "human",
                             "mask_sha256": mask["sha256"]}, mask, PARAMS)


def _gate(st_value, **kw):
    args = {"configured": True, "layer": "suppress", "source_has_burned_captions": True,
            "path": st.DYNAMIC}
    args.update(kw)
    return st.gate({"source_treatment": st_value}, **args)


def _gate_refuses(reason: str, st_value, **kw) -> None:
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _gate(st_value, **kw)
    assert e.value.reason == reason
    assert "treatment" not in e.value.as_dict()


@pytest.mark.parametrize("layer", ["burn", "suppress"])
def test_an_active_treatment_nobody_can_execute_is_refused_under_both_layers(mask, layer):
    _gate_refuses("treatment_not_implemented", _active(mask), layer=layer)


@pytest.mark.parametrize("layer", ["burn", "suppress"])
def test_a_refused_resolution_stays_refused_under_both_layers(layer):
    got = st.resolve({"treatment": "erase", "decided_by": "human"})
    _gate_refuses("mask_missing", got, layer=layer)


def test_a_base_treatment_with_no_line_in_the_window_is_still_refused(tmp_path):
    doc = mask_doc()
    doc["lines"] = []
    doc["frames"] = [{"k_from": K_FIRST, "k_to": K_LAST + 1, "state": "none_observed",
                      "basis": "viewed"}]
    m = load(write_mask(tmp_path, doc))
    got = st.resolve(None, {"treatment": "blur", "decided_by": "human",
                            "mask_sha256": m["sha256"]}, m, PARAMS)
    assert got["per_line"] == []
    _gate_refuses("treatment_not_implemented", got, implemented=frozenset({"erase"}))


@pytest.mark.parametrize("path,reason", [(st.STATIC, "static_path_unsupported"),
                                         ("editor_still", "path_unsupported")])
def test_only_the_dynamic_path_treats(mask, path, reason):
    _gate_refuses(reason, _active(mask), path=path, implemented=frozenset({"erase"}))


def test_an_executable_treatment_executes_under_both_layers(mask):
    for layer in ("burn", "suppress"):
        got = _gate(_active(mask), layer=layer, implemented=frozenset({"erase"}))
        assert got["state"] == "execute"


@pytest.mark.parametrize("configured", [True, None, 1])
def test_a_configured_clip_without_the_key_is_refused(configured):
    with pytest.raises(st.SourceTreatmentRefused) as e:
        st.gate({}, configured=configured, layer="suppress", source_has_burned_captions=True,
                path=st.DYNAMIC)
    assert e.value.reason == "decision_without_treatment"


def test_an_unconfigured_clip_without_the_key_is_none():
    got = st.gate({}, configured=False, layer="suppress", source_has_burned_captions=True,
                  path=st.STATIC)
    assert got["state"] == "none"


def test_burn_over_the_untouched_source_text_is_refused():
    _gate_refuses("burn_over_untreated_source_captions", st.resolve(), layer="burn")
    assert _gate(st.resolve(), layer="suppress")["state"] == "none"
    assert _gate(st.resolve(), layer="burn", source_has_burned_captions=None)["state"] == "none"
    assert _gate(st.resolve(), layer="burn", source_has_burned_captions=1)["state"] == "none"


@pytest.mark.parametrize("value", [
    "erase", {"state": "resolved"}, {"schema": st.CONFIG_SCHEMA, "state": "maybe", "active": False},
    {"schema": st.CONFIG_SCHEMA, "state": "resolved", "active": 1},
    # with a readable `treatment`, so only the state/active check sees them
    {"schema": st.CONFIG_SCHEMA, "state": "maybe", "treatment": "none", "active": False},
    {"schema": st.CONFIG_SCHEMA, "state": "resolved", "treatment": "none", "active": 0},
    {"schema": st.CONFIG_SCHEMA, "state": "refused", "reason": "none"},
    {"schema": "clipper_source_treatment_config_v0", "state": "resolved", "treatment": "none",
     "active": False},
])
def test_an_unreadable_treatment_is_refused_not_none(value):
    _gate_refuses("treatment_unreadable", value)
