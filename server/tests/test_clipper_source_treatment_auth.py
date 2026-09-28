"""The split's approval, bound to a record the config cannot write, and a gate
that never executes an incomplete object (codex-verdict-next-13 §1 R1–R3).

Codex reproduced three counterexamples with the builders in
test_clipper_source_treatment.py; each is reproduced here and must now refuse:
R1 the inverted pair (erase before k 7158, blur after) with the authentic
answer; R2 an answer replaced and its hash recomputed in the config; R3 an
active `{schema, state, treatment, active}` with no params reaching `execute`.
"""

from __future__ import annotations

import hashlib
from fractions import Fraction
from pathlib import Path

import pytest

from services.clipper import source_treatment as st
from test_clipper_source_treatment import (ANSWER_BYTES, ANSWER_SHA, CLIP, K_FIRST,
                                           PARAMS, SEAM, SEAM_PTS, SRC_SHA, STEP, _gate, _line,
                                           answer_file, identity, load, mask_doc, refused,
                                           split_setting, write_mask)

QID = "sc-split-line-F4F5"


@pytest.fixture
def mask(tmp_path) -> dict:
    return load(write_mask(tmp_path))


@pytest.fixture
def answer(tmp_path) -> Path:
    return answer_file(tmp_path)


def approve(monkeypatch, answer_path: Path, **change) -> None:
    """Stand in ANOTHER approved record: the pilot's, bound to this file's hash
    and changed as named. For the checks of a record against its own file."""
    rec = dict(st.QUESTIONS[QID], **change)
    rec["answer_sha256"] = hashlib.sha256(answer_path.read_bytes()).hexdigest()
    monkeypatch.setitem(st.QUESTIONS, QID, rec)


def _split(mask: dict, answer: Path, k_from: int, k_to: int, t: str, k: int = SEAM) -> dict:
    return {"k_from": k_from, "k_to": k_to, "treatment": t,
            "split": {"line_id": "b23c-L30", "k_split": k, "pts_split": k * STEP,
                      "authorisation": {"question_id": QID, "answer_file": str(answer)}}}


def _clip(mask: dict, base: str, *overrides: dict) -> dict:
    return {"treatment": base, "decided_by": "human", "mask_sha256": mask["sha256"],
            "overrides": list(overrides)}


# ── the approved record ──────────────────────────────────────────────────────

def test_the_approved_record_is_what_the_person_answered():
    assert st.QUESTIONS[QID] == {
        "label": "linia F4/F5", "split_answer": "c", "offered": "c_switch_at_238.6.mp4",
        "answer_sha256": ANSWER_SHA, "clip_id": CLIP, "source_sha256": SRC_SHA,
        "line_id": "b23c-L30", "k_split": SEAM, "pts_split": SEAM_PTS,
        "before": "blur", "after": "erase"}
    assert SEAM_PTS == SEAM * STEP and Fraction(SEAM_PTS, 15360) == Fraction("238.6")


# ── R1: the treatment on each side is part of the approval ───────────────────

def test_the_inverted_pair_is_refused_with_the_authentic_answer(mask, answer):
    """Codex's R1: base blur, override erase, answer and k 7158 untouched."""
    clip = split_setting(mask, answer)
    clip["treatment"], clip["overrides"][0]["treatment"] = "blur", "erase"
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line",
            "resolves to 'erase' before k 7158 and 'blur' from it")


@pytest.mark.parametrize("base,before", [("erase", "erase"), ("none", "blur"), ("erase", "none"),
                                         ("blur", "blur")])
def test_a_side_that_is_not_what_the_person_chose_is_refused(mask, answer, base, before):
    clip = _clip(mask, base, _split(mask, answer, K_FIRST, SEAM, before))
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "the person chose")


@pytest.mark.parametrize("after", ["blur", "none"])
def test_a_second_override_cannot_change_the_approved_side(mask, answer, after):
    clip = _clip(mask, "erase", _split(mask, answer, K_FIRST, SEAM, "blur"),
                 _split(mask, answer, SEAM, 7178, after))
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "the person chose")


def test_a_split_that_treats_nothing_is_refused(mask, answer):
    clip = _clip(mask, "none", _split(mask, answer, K_FIRST, SEAM, "none"))
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line",
            "None before k 7158")


def test_the_approved_sides_from_two_overrides_resolve(mask, answer):
    clip = _clip(mask, "none", _split(mask, answer, K_FIRST, SEAM, "blur"),
                 _split(mask, answer, SEAM, 7178, "erase"))
    got = st.resolve(None, clip, mask, PARAMS)
    assert [(s["line_id"], s["k_from"], s["k_to"], s["treatment"]) for s in got["per_line"]] == [
        ("L0001", K_FIRST, 7111, "blur"), ("b23c-L30", 7113, SEAM, "blur"),
        ("b23c-L30", SEAM, 7178, "erase")]


# ── R2: a config cannot supply its own approval ──────────────────────────────

@pytest.mark.parametrize("with_hash", [False, True])
def test_a_replaced_answer_with_its_hash_recomputed_is_refused(mask, tmp_path, with_hash):
    """Codex's R2: 238,6 -> 238,5 and the seam moved consistently to k 7155."""
    p = answer_file(tmp_path, ANSWER_BYTES.replace(b"238,6", b"238,5"))
    clip = split_setting(mask, p, k_split=7155, pts_split=7155 * STEP)
    clip["overrides"][0]["k_to"] = 7155
    if with_hash:
        auth = clip["overrides"][0]["split"]["authorisation"]
        auth["answer_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line",
            "not an authorisation" if with_hash else "about k 7158")


@pytest.mark.parametrize("old,new", [(b"238,6", b"238,5"), (b"observa\xc8\x9bii: -",
                                                           b"observa\xc8\x9bii: ok")])
def test_any_other_answer_bytes_are_refused_by_the_records_hash(mask, tmp_path, old, new):
    p = answer_file(tmp_path, ANSWER_BYTES.replace(old, new))
    assert p.read_bytes() != ANSWER_BYTES
    refused(st.resolve(None, split_setting(mask, p), mask, PARAMS), "override_splits_a_line",
            "sha256 is not the approved record's")


def test_another_clip_with_the_same_prefix_is_refused(tmp_path, answer):
    doc = mask_doc()
    doc["clip"]["clip_id"] = "b23c00000000"
    other = load(write_mask(tmp_path, doc), clip_id="b23c00000000")
    refused(st.resolve(None, split_setting(other, answer), other, PARAMS),
            "override_splits_a_line", "the mask is 'b23c00000000'")


def test_the_same_clip_id_on_another_source_is_refused(tmp_path, answer):
    doc = mask_doc()
    doc["source"]["sha256"] = "ab" * 32
    other = load(write_mask(tmp_path, doc), source_identity=identity() | {"sha256": "ab" * 32})
    refused(st.resolve(None, split_setting(other, answer), other, PARAMS),
            "override_splits_a_line", f"about source {SRC_SHA}")


def test_the_same_choice_attached_to_another_line_is_refused(tmp_path, answer):
    """The mask's F4/F5 run carries another id; the split names it consistently."""
    doc = mask_doc()
    doc["lines"][1] = _line("b23c-L31", 7113, 7178, doc["lines"][1]["text"])
    doc["frames"][2]["state"] = "line:b23c-L31"
    for strip in doc["inspection"]["strips"]:
        strip["read"] = "line:b23c-L31"
    m = load(write_mask(tmp_path, doc))
    refused(st.resolve(None, split_setting(m, answer, line_id="b23c-L31"), m, PARAMS),
            "override_splits_a_line", "about line 'b23c-L30', not 'b23c-L31'")


# ── a record is checked against its own file ─────────────────────────────────

def test_a_record_whose_file_records_another_answer_is_refused(mask, tmp_path, monkeypatch):
    p = answer_file(tmp_path, ANSWER_BYTES.replace(b"F4/F5: c", b"F4/F5: b"))
    approve(monkeypatch, p)
    refused(st.resolve(None, split_setting(mask, p), mask, PARAMS),
            "override_splits_a_line", "records 'b'")


def test_a_record_at_another_k_than_the_person_read_is_refused(mask, answer, monkeypatch):
    """Only the answer's 238,6 s, read back through the clock, catches a record
    whose k and pts were moved one frame earlier together."""
    approve(monkeypatch, answer, k_split=7157, pts_split=7157 * STEP)
    clip = split_setting(mask, answer, k_split=7157, pts_split=7157 * STEP)
    clip["overrides"][0]["k_to"] = 7157
    refused(st.resolve(None, clip, mask, PARAMS), "override_splits_a_line", "238.6")


def test_a_record_whose_file_is_about_another_clip_is_refused(mask, tmp_path, monkeypatch):
    p = answer_file(tmp_path, ANSWER_BYTES.replace(b"3. b23c,", b"3. 6053,"))
    approve(monkeypatch, p)
    refused(st.resolve(None, split_setting(mask, p), mask, PARAMS),
            "override_splits_a_line", "file's answer is about clip '6053'")


@pytest.mark.parametrize("data,detail", [
    (ANSWER_BYTES + b"\r\n4. b23c, linia F4/F5: c \xe2\x80\x94 238,6 s", "2 answer lines"),
    (ANSWER_BYTES.replace(b"linia F4/F5", b"linia F1/F2"), "0 answer lines"),
    (ANSWER_BYTES.replace(b"238,6 s", b"238,6 s sau 238,7 s"), "2 times"),
    (b"\xff" + ANSWER_BYTES, "not UTF-8"),
])
def test_an_answer_that_does_not_read_unambiguously_is_refused(mask, tmp_path, monkeypatch,
                                                               data, detail):
    p = answer_file(tmp_path, data)
    approve(monkeypatch, p)
    refused(st.resolve(None, split_setting(mask, p), mask, PARAMS),
            "override_splits_a_line", detail)


# ── R3: the gate refuses an incomplete or inconsistent structure ─────────────

ALL = frozenset({"blur", "erase"})


def _active(mask) -> dict:
    return st.resolve(None, {"treatment": "erase", "decided_by": "human",
                             "mask_sha256": mask["sha256"]}, mask, PARAMS)


def _seg(key, value):
    def change(a):
        a["per_line"][0] = dict(a["per_line"][0], **{key: value})
    return change


def _drop(key):
    return lambda a: a.pop(key)


def _set(key, value):
    return lambda a: a.__setitem__(key, value)


def _erase_params(value):
    """The erase params replaced in `params` AND in every segment, so only the
    params check itself can see it."""
    def change(a):
        a["params"] = {"erase": value}
        a["per_line"] = [dict(s, params=value) for s in a["per_line"]]
    return change


BROKEN = {
    "params missing": _drop("params"), "params None": _set("params", None),
    "params empty": _set("params", {}), "params list": _set("params", ["erase"]),
    "params value empty": _erase_params({}), "params value not a dict": _erase_params("telea"),
    "params unknown treatment": _set("params", dict(PARAMS["values"], band={"y": 16})),
    "treatment unknown": _set("treatment", "band"), "treatment int": _set("treatment", 1),
    "treatment None": _set("treatment", None), "treatment list": _set("treatment", ["erase"]),
    "base without params": _set("treatment", "blur"),
    "no mask_sha256": _drop("mask_sha256"), "no params_version": _drop("params_version"),
    "no window": _drop("window"), "no per_line": _drop("per_line"),
    "mask_sha256 None": _set("mask_sha256", None), "mask_sha256 short": _set("mask_sha256", "cd"),
    "params_version empty": _set("params_version", ""), "params_version int": _set("params_version", 1),
    "per_line not a list": _set("per_line", "all"), "per_line None": _set("per_line", None),
    "segment not a dict": lambda a: a["per_line"].__setitem__(0, "erase"),
    "segment an int": lambda a: a["per_line"].__setitem__(0, 7113),
    "segment missing a key": lambda a: a["per_line"][0].pop("line_off"),
    "segment treatment not in params": _seg("treatment", "blur"),
    "segment treatment unhashable": _seg("treatment", ["erase"]),
    "segment params differ": _seg("params", {"telea_r": [9, 4], "dilation": 17}),
    "segment empty": _seg("k_to", K_FIRST), "segment k bool": _seg("k_from", True),
}


@pytest.mark.parametrize("layer", ["burn", "suppress"])
@pytest.mark.parametrize("name", sorted(BROKEN))
def test_an_incomplete_active_structure_is_refused_before_the_executor(mask, layer, name):
    got = _active(mask)
    assert _gate(got, layer=layer, implemented=ALL)["state"] == "execute"
    BROKEN[name](got)
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _gate(got, layer=layer, implemented=ALL)
    assert e.value.reason == "treatment_unreadable"


@pytest.mark.parametrize("layer", ["burn", "suppress"])
@pytest.mark.parametrize("implemented", [frozenset(), ALL])
def test_codexs_bare_active_object_is_refused(layer, implemented):
    bare = {"schema": st.CONFIG_SCHEMA, "state": "resolved", "treatment": "erase", "active": True}
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _gate(bare, layer=layer, implemented=implemented)
    assert e.value.reason == "treatment_unreadable"


@pytest.mark.parametrize("layer", ["burn", "suppress"])
@pytest.mark.parametrize("extra", [{"treatment": "erase"}, {"params": PARAMS["values"]},
                                   {"per_line": []}])
def test_an_inactive_structure_that_hides_a_treatment_is_refused(layer, extra):
    value = st.resolve() | extra
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _gate(value, layer=layer, source_has_burned_captions=False, implemented=ALL)
    assert e.value.reason == "treatment_unreadable"


@pytest.mark.parametrize("layer", ["burn", "suppress"])
def test_base_none_with_an_active_override_still_executes(mask, layer):
    got = st.resolve(None, {"treatment": "none", "decided_by": "human", "mask_sha256": mask["sha256"],
                            "overrides": [{"k_from": K_FIRST, "k_to": 7111, "treatment": "blur"}]},
                     mask, PARAMS)
    assert (got["treatment"], got["active"], set(got["params"])) == ("none", True, {"blur"})
    assert _gate(got, layer=layer, implemented=frozenset({"blur"}))["state"] == "execute"
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _gate(got, layer=layer)
    assert e.value.reason == "treatment_not_implemented"


@pytest.mark.parametrize("layer", ["burn", "suppress"])
def test_an_active_override_with_no_line_and_no_params_is_refused(mask, layer):
    """base none, one override on no-text frames only: nothing in `per_line`,
    so emptied params are caught by the params check alone."""
    got = st.resolve(None, {"treatment": "none", "decided_by": "human", "mask_sha256": mask["sha256"],
                            "overrides": [{"k_from": 7111, "k_to": 7113, "treatment": "blur"}]},
                     mask, PARAMS)
    assert (got["active"], got["per_line"], set(got["params"])) == (True, [], {"blur"})
    got["params"] = {}
    with pytest.raises(st.SourceTreatmentRefused) as e:
        _gate(got, layer=layer, implemented=ALL)
    assert e.value.reason == "treatment_unreadable"
