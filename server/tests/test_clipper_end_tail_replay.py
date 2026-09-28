"""EN3Tr §3 (codex-verdict-next-29 §3): the frozen replay (`scripts/rerender_pilots.py`) carries the frozen
sidecar's `end_tail.recorded`, with provenance, and the binding is recomputed on the new render. The old
`binding`/`delivered_s` are never copied; today's settings are never read; the recipe and fingerprint stay.

Real ffmpeg through the real `render_export`, as test_clipper_shared_export does for the replay itself.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from config import settings
from test_clipper_shared_export import _decision, _frozen_script, _models, encoded_source  # noqa: F401
from workers import clipper_render_output as output

EV = {"rule": "end_tail_v1", "state": "moved", "target_s": 0.4, "end_in": 1.95, "vocal_end": 1.85,
      "next_speech": 2.7, "requested_end": 2.25, "end_out": 2.25}


@pytest.fixture
def frozen(tmp_path, monkeypatch, encoded_source):
    """One normal export with EN3's record on the clip; returns (sidecar path, frozen sidecar, frozen bytes)."""
    clip, project = _models(encoded_source[True], "replay")
    clip.reasoning = {"end_tail": EV}
    work = tmp_path / project.id / "exports"
    work.mkdir(parents=True)
    out = work / f"{clip.id}.mp4"
    decision = _decision()
    decision["drop"] = []
    monkeypatch.setattr(settings, "clipper_export_crf", 26)
    monkeypatch.setattr(settings, "clipper_export_preset", "ultrafast")
    asyncio.run(output.render_export(clip, project, decision, out, src=str(encoded_source[True])))
    path = out.with_suffix(".json")
    data = path.read_bytes()
    side = json.loads(data)
    assert side["end_tail"]["binding"] == "applies"
    return path, side, data


def _replay(path, side, monkeypatch):
    before = deepcopy(side)
    # Today's setting says OFF, and the global CRF changed: neither may reach the replay.
    monkeypatch.setattr(settings, "clipper_end_tail_s", 0.0)
    monkeypatch.setattr(settings, "clipper_export_crf", 32)
    row = _frozen_script()._render(path, side, False)
    assert not row.get("refused"), row
    assert side == before, "the frozen sidecar the replay was given is never edited"
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_present_record_is_carried_with_provenance_and_rebound_on_the_new_render(frozen, monkeypatch):
    path, side, data = frozen
    mp4 = path.with_suffix(".mp4").read_bytes()
    # A stale binding in the frozen block must not survive: the new one is computed from this render.
    stale = deepcopy(side)
    stale["end_tail"].update(binding="not_corroborated", delivered_s={"end": 99.0}, why="x")
    after = _replay(path, stale, monkeypatch)
    got = after["end_tail"]
    assert got["binding"] == "applies" and got["recorded"] == EV
    assert got["delivered_s"] == {"vocal_end": 1.6, "next_speech": None, "end": 2.0}
    assert got["provenance"] == {"from": "frozen_sidecar", "sidecar": str(path),
                                 "sidecar_sha256": hashlib.sha256(data).hexdigest(),
                                 "field": "end_tail.recorded"}
    # The same recipe: the same fingerprint, the same encoder options, the same file.
    assert after["input_fingerprint"] == side["input_fingerprint"]
    assert after["render"] == side["render"]
    assert path.with_suffix(".mp4").read_bytes() == mp4


@pytest.mark.parametrize("block", ["missing", {"binding": "absent", "recorded": None}])
def test_a_legacy_sidecar_without_a_record_is_absent(frozen, monkeypatch, block):
    path, side, _ = frozen
    legacy = deepcopy(side)
    if block == "missing":
        del legacy["end_tail"]
    else:
        legacy["end_tail"] = block
    after = _replay(path, legacy, monkeypatch)
    assert after["end_tail"] == {"binding": "absent", "recorded": None}
    assert after["input_fingerprint"] == side["input_fingerprint"]


@pytest.mark.parametrize("recorded,why", [
    ({**EV, "end_out": float("nan")}, "recorded_end_not_a_finite_number"),
    ({**EV, "state": "extended"}, "unknown_state"),
    ("moved", "unknown_rule"),
    (None, "unknown_rule"),
])
def test_an_invalid_frozen_record_is_refused_not_promoted(frozen, monkeypatch, recorded, why):
    path, side, _ = frozen
    bad = deepcopy(side)
    bad["end_tail"] = {"binding": "applies", "recorded": recorded}
    got = _replay(path, bad, monkeypatch)["end_tail"]
    assert got["binding"] == "invalid_record" and got["why"] == why
    assert got["provenance"]["from"] == "frozen_sidecar" and "delivered_s" not in got


def test_a_frozen_record_for_another_end_is_not_applied(frozen, monkeypatch):
    path, side, _ = frozen
    moved = deepcopy(side)
    moved["end_tail"]["recorded"] = {**EV, "requested_end": 2.5, "end_out": 2.5}
    got = _replay(path, moved, monkeypatch)["end_tail"]
    assert got["binding"] == "end_changed_since_scoring" and got["recorded"]["end_out"] == 2.5
    assert "delivered_s" not in got
