"""Sidecar schema v3: the source-caption treatment in the recipe (SC-addendum-v2 §5).

SC batch 2. Every sidecar written from here on declares v3, with or without a
treatment. `none` has exactly one canonical identity; a missing or malformed
identity is `unreadable` — its own state, never `none`, never a pass, in both
directions. v1/v2 records keep their own formula; comparing across schemas says
`schema_differs` first and nothing else; a v3 mismatch is never rescued by v2.
The identity is the semantic projection, not the manifest's file hash
(codex-verdict-next-10 §1), so the same recipe retried digests the same.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from services.clipper import edit_quality as eq
from services.clipper import render_input as ri
from services.clipper import source_treatment as st
from services.clipper.source_treatment_manifest import NONE_IDENTITY
from test_clipper_source_treatment_render import CLIP, Synth, encode_source
from tests.test_clipper_edit_quality import _crop_fit_fit_crop
from tests.test_clipper_export_audit import _audit_module, _write
from workers import clipper_render_output as output


@pytest.fixture(scope="module")
def renders(tmp_path_factory) -> dict:
    """Three real exports of the synthetic source: erase twice (a retry, other
    scratch, other nonce), blur once, and an untreated one."""
    import asyncio

    root = tmp_path_factory.mktemp("sc2-v3")
    src = encode_source(root / "plain.mp4")
    s = Synth(root, src)

    async def go():
        out = {}
        for name, setting, nonce in (("erase", s.setting("erase"), "one"),
                                     ("retry", s.setting("erase"), "two"),
                                     ("blur", s.setting("blur"), "three"),
                                     ("none", None, "four")):
            done = await output.render_export(
                s.clip, s.project, s.decision(setting), root / name / f"{CLIP}.mp4", src=str(src),
                destination="versioned", attempt={"job_id": "j", "nonce": nonce})
            out[name] = done["sidecar"]
        return out

    return asyncio.run(go())


def _stamp(body: dict, schema: str = ri.FINGERPRINT_SCHEMA_V3) -> dict:
    body = {**body, "fingerprint_schema": schema}
    body["input_fingerprint"] = ri.input_fingerprint(body, schema=schema)
    return body


# ── the writer ───────────────────────────────────────────────────────────────

def test_every_new_sidecar_declares_v3_and_none_is_canonical(renders):
    none = renders["none"]
    assert none["fingerprint_schema"] == ri.FINGERPRINT_SCHEMA_V3
    assert none["source_treatment_identity"] == {"schema": st.IDENTITY_SCHEMA, "treatment": "none"}
    assert set(none["source_treatment_identity"]) == {"schema", "treatment"}
    assert eq.fingerprint_verdict(none)["state"] == eq.FINGERPRINT_VALID
    for name in ("erase", "retry", "blur"):
        body = renders[name]
        assert body["fingerprint_schema"] == ri.FINGERPRINT_SCHEMA_V3
        assert ri.treatment_identity_state(body) == ri.TREATMENT_ACTIVE
        assert eq.fingerprint_verdict(body)["state"] == eq.FINGERPRINT_VALID
        # the identity comes from the record, and names no path, job or nonce
        assert body["source_treatment_identity"] == body["render_record"]["source_treatment"]["identity"]
        text = json.dumps(body["source_treatment_identity"])
        assert ".st-" not in text and "nonce" not in text and "job_id" not in text


def test_the_same_recipe_twice_digests_the_same_and_another_treatment_does_not(renders):
    erase, retry, blur = renders["erase"], renders["retry"], renders["blur"]
    assert (erase["render_record"]["source_treatment"]["manifest_sha256"]
            != retry["render_record"]["source_treatment"]["manifest_sha256"])
    assert erase["input_fingerprint"] == retry["input_fingerprint"]
    assert ri.compare_recipes(erase, retry) == {"verdict": "same_recipe",
                                                "schema": ri.FINGERPRINT_SCHEMA_V3}
    assert ri.compare_recipes(erase, blur)["verdict"] == "recipe_changed"
    assert ri.compare_recipes(erase, renders["none"])["verdict"] == "recipe_changed"


# ── none, active, unreadable ─────────────────────────────────────────────────

@pytest.fixture
def active(renders) -> dict:
    return copy.deepcopy(renders["erase"]["source_treatment_identity"])


@pytest.mark.parametrize("identity", [
    {"schema": st.IDENTITY_SCHEMA, "treatment": "none", "mask_sha256": None},
    {"schema": st.IDENTITY_SCHEMA, "treatment": "none", "per_line": []},
    {"schema": st.IDENTITY_SCHEMA, "treatment": "none", "patch_sha256": "0" * 64},
    {"schema": st.IDENTITY_SCHEMA, "treatment": "none", "params": {}},
    {"schema": st.IDENTITY_SCHEMA},
    {"treatment": "none"},
    {"schema": "clipper_source_treatment_v0", "treatment": "none"},
    {"schema": st.IDENTITY_SCHEMA, "treatment": 0},
    {"schema": st.IDENTITY_SCHEMA, "treatment": "None"},
    None, [], "none", 0,
])
def test_anything_but_the_exact_none_is_unreadable_never_none(identity):
    body = {"source_treatment_identity": identity}
    assert ri.treatment_identity_state(body) == ri.TREATMENT_UNREADABLE
    assert ri.treatment_identity_state({}) == ri.TREATMENT_UNREADABLE
    assert ri.treatment_identity_state({"source_treatment_identity": NONE_IDENTITY}) == ri.TREATMENT_NONE


@pytest.mark.parametrize("change", ["drop_patch", "extra", "empty_per_line", "bad_mask", "treatment",
                                    "patch_sha", "short_patch_sha", "schema"])
def test_an_incomplete_active_identity_is_unreadable(active, change):
    assert ri.treatment_identity_state({"source_treatment_identity": active}) == ri.TREATMENT_ACTIVE
    if change == "drop_patch":
        del active["patch"]
    elif change == "extra":
        active["manifest_sha256"] = "0" * 64
    elif change == "empty_per_line":
        active["per_line"] = []
    elif change == "bad_mask":
        active["mask_sha256"] = "abc"
    elif change == "treatment":
        active["treatment"] = "band"
    elif change == "short_patch_sha":
        active["patch"]["sha256"] = "abc"
    elif change == "schema":
        active["schema"] = "clipper_source_treatment_v0"
    else:
        active["patch"]["sha256"] = None
    assert ri.treatment_identity_state({"source_treatment_identity": active}) == ri.TREATMENT_UNREADABLE


@pytest.mark.parametrize("identity", [None, {"schema": st.IDENTITY_SCHEMA, "treatment": "none",
                                             "mask_sha256": None}])
def test_unreadable_is_its_own_row_even_when_its_digest_recomputes(renders, identity):
    """Direction one: an identity nobody can read never verifies as valid, never
    counts as `none`, and never publishes."""
    body = _stamp({**renders["none"], "source_treatment_identity": identity})
    assert body["input_fingerprint"] == ri.input_fingerprint(body, schema=ri.FINGERPRINT_SCHEMA_V3)
    got = eq.fingerprint_verdict(body)
    assert got["state"] == eq.FINGERPRINT_TREATMENT_UNREADABLE
    assert got["state"] not in (eq.FINGERPRINT_VALID, eq.FINGERPRINT_MISMATCH)
    assert ri.compare_recipes(body, renders["none"])["verdict"] == "source_treatment_unreadable"


def test_none_is_never_read_as_unreadable_and_absent_is_not_none(renders):
    """Direction two: the canonical `none` verifies and compares as itself; and
    an absent identity digests differently from `none` — neither stands in for
    the other."""
    none = renders["none"]
    assert eq.fingerprint_verdict(none)["state"] == eq.FINGERPRINT_VALID
    absent = {k: v for k, v in none.items() if k != "source_treatment_identity"}
    assert ri.input_fingerprint(absent, schema=ri.FINGERPRINT_SCHEMA_V3) != none["input_fingerprint"]
    assert eq.fingerprint_verdict(_stamp(absent))["state"] == eq.FINGERPRINT_TREATMENT_UNREADABLE


# ── schemas ──────────────────────────────────────────────────────────────────

def test_the_schema_difference_is_reported_first_and_alone(renders):
    v3 = renders["erase"]
    v2 = _stamp({k: v for k, v in v3.items() if k != "source_treatment_identity"},
                ri.FINGERPRINT_SCHEMA_V2)
    assert eq.fingerprint_verdict(v2)["state"] == eq.FINGERPRINT_VALID
    assert ri.compare_recipes(v2, v3) == {"verdict": "schema_differs",
                                          "from": ri.FINGERPRINT_SCHEMA_V2,
                                          "to": ri.FINGERPRINT_SCHEMA_V3}
    # even where the recipe ALSO differs, and even where v3's identity is unreadable
    other = _stamp({**v2, "caption_y": 0.1}, ri.FINGERPRINT_SCHEMA_V2)
    assert ri.compare_recipes(other, v3)["verdict"] == "schema_differs"
    broken = _stamp({**v3, "source_treatment_identity": None})
    assert ri.compare_recipes(v2, broken)["verdict"] == "schema_differs"
    assert ri.compare_recipes({"fingerprint_schema": "v9"}, v3)["verdict"] == "unknown_schema"


def test_a_v3_mismatch_is_not_rescued_by_v2(renders):
    v3 = copy.deepcopy(renders["erase"])
    v3["source_treatment_identity"]["frames"][1]["k_to"] += 1  # edited after the render
    assert eq.fingerprint_verdict(v3)["state"] == eq.FINGERPRINT_MISMATCH
    # the v2 formula does not cover the treatment, so it WOULD match...
    assert ri.input_fingerprint(v3, schema=ri.FINGERPRINT_SCHEMA_V2) == ri.input_fingerprint(
        renders["erase"], schema=ri.FINGERPRINT_SCHEMA_V2)
    as_v2 = _stamp(v3, ri.FINGERPRINT_SCHEMA_V2)
    assert eq.fingerprint_verdict(as_v2)["state"] == eq.FINGERPRINT_VALID
    # ...and the record that declared v3 is still a mismatch; so is a v3
    # declaration carrying a v2 digest
    assert eq.fingerprint_verdict(v3)["state"] == eq.FINGERPRINT_MISMATCH
    v2_digest = {**renders["erase"], "input_fingerprint": ri.input_fingerprint(
        renders["erase"], schema=ri.FINGERPRINT_SCHEMA_V2)}
    assert eq.fingerprint_verdict(v2_digest)["state"] == eq.FINGERPRINT_MISMATCH


def test_v1_and_v2_are_unchanged():
    assert ri.FINGERPRINT_SCHEMA_V2 == "clipper_render_input_v2"
    assert ri.FINGERPRINT_KEYS_V2 == ri.FINGERPRINT_KEYS_V1 + ("caption_identity",)
    assert ri.FINGERPRINT_KEYS_V3 == ri.FINGERPRINT_KEYS_V2 + ("source_treatment_identity",)
    assert ri.SCHEMAS == (ri.FINGERPRINT_SCHEMA_V1, ri.FINGERPRINT_SCHEMA_V2, ri.FINGERPRINT_SCHEMA_V3)
    body = {"source": "s.mp4", "source_start": 1.0, "source_end": 2.0,
            "source_treatment_identity": {"anything": True}}
    # the treatment identity is outside v1 and v2 entirely
    assert "source_treatment_identity" not in ri.render_input(body, schema=ri.FINGERPRINT_SCHEMA_V2)
    assert ri.input_fingerprint(body, schema=ri.FINGERPRINT_SCHEMA_V2) == ri.input_fingerprint(
        {k: v for k, v in body.items() if k != "source_treatment_identity"},
        schema=ri.FINGERPRINT_SCHEMA_V2)


# ── the audit gate learns v3, through main() ────────────────────────────────

def test_the_audit_passes_v3_none_and_fails_an_unreadable_identity(tmp_path, monkeypatch, capsys):
    audit = _audit_module()
    exports = tmp_path / "p1" / "exports"
    exports.mkdir(parents=True)
    good = _stamp({**_crop_fit_fit_crop(), "source_treatment_identity": dict(NONE_IDENTITY)})
    (exports / "a.mp4").write_bytes(b"x")
    _write(exports, "a", good)
    monkeypatch.setattr(audit, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["audit", "p1", "--json"])
    assert audit.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["macro"]["fingerprints"] == {eq.FINGERPRINT_VALID: 1}

    bad = _stamp({**_crop_fit_fit_crop(),
                  "source_treatment_identity": {"schema": st.IDENTITY_SCHEMA, "treatment": "none",
                                                "per_line": []}})
    (exports / "b.mp4").write_bytes(b"x")
    _write(exports, "b", bad)
    assert audit.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["macro"]["fingerprints"] == {eq.FINGERPRINT_VALID: 1,
                                               eq.FINGERPRINT_TREATMENT_UNREADABLE: 1}
    assert report["integrity_ok"] is False
    assert Path(audit.__file__).name == "audit_clipper_exports.py"
