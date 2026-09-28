"""The 3b addressing pass is OFF unless asked for (codex-verdict-next-16 §3).

Off (the default): the scene pass and its cheap 3a evidence still run — each selected frame keeps its integer
PTS, time_base and legacy_index — and `scenes_addressed.state` is `not_requested`: not a refusal, and not an
empty result (zero scenes reads `not_requested` with `detected: 0`, never `ok`). A failed decode still reads
as the decode's own state. The costly per-row addresser is never called. On: unchanged behaviour.
"""
from __future__ import annotations

import pytest

from services.clipper import scene_address as S

TB = "1/10240"


def _scene(pts: list[int], state: str = "ok") -> dict:
    rows = [{"id": i, "t": round(p / 10240, 6), "legacy_index": i, "legacy_excluded": None, "proxy_pts": p,
             "time_base": TB, "slot": p // 1024, "scene_score": 0.5} for i, p in enumerate(pts)]
    return {"state": state, "reason": None, "threshold": 0.3, "time_base": TB, "rows": rows,
            "times": [r["t"] for r in rows]}


@pytest.fixture
def addresser_forbidden(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the costly per-row addresser ran while addressing is off")
    monkeypatch.setattr(S, "address_row", boom)


@pytest.fixture
def addressing(monkeypatch):
    from config import settings
    def set_(on: bool):
        monkeypatch.setattr(settings, "clipper_scene_addressing", on)
    return set_


def test_it_is_off_by_default():
    from config import Settings
    assert Settings().clipper_scene_addressing is False


def test_off_keeps_the_3a_evidence_and_says_not_requested(addressing, addresser_forbidden):
    addressing(False)
    doc = S.address_scenes("p", "proxy.mp4", _scene([10240, 20480, 30720]), {"state": "recorded"})
    assert doc["state"] == "not_requested"
    assert doc["counts"]["detected"] == 3 and doc["counts"]["not_requested"] == 3
    assert doc["counts"]["interval"] == 0 and doc["counts"]["refused"] == 0
    assert [r["proxy_pts"] for r in doc["rows"]] == [10240, 20480, 30720]
    assert {r["state"] for r in doc["rows"]} == {"not_requested"}
    assert all(r["time_base"] == TB and r["legacy_index"] is not None for r in doc["rows"])


def test_zero_scenes_off_is_not_an_empty_ok(addressing, addresser_forbidden):
    addressing(False)
    doc = S.address_scenes("p", "proxy.mp4", _scene([]), {"state": "recorded"})
    assert doc["state"] == "not_requested" and doc["counts"]["detected"] == 0


@pytest.mark.parametrize("state", ["proxy_missing", "decode_failed", "decode_mismatch"])
def test_a_failed_decode_is_not_not_requested(addressing, addresser_forbidden, state):
    addressing(False)
    scene = {"state": state, "reason": "x", "threshold": 0.3, "time_base": None, "rows": None, "times": []}
    doc = S.address_scenes("p", "proxy.mp4", scene, {"state": "recorded"})
    assert doc["state"] == state and doc["rows"] is None and doc["counts"]["detected"] is None


def test_off_still_checks_the_rows_rebuild_the_legacy_list(addressing, addresser_forbidden):
    addressing(False)
    scene = _scene([10240, 20480])
    scene["times"] = scene["times"] + [9.99]          # a legacy time no row accounts for
    doc = S.address_scenes("p", "proxy.mp4", scene, {"state": "recorded"})
    assert doc["state"] == "decode_mismatch" and doc["rows"] is None


def test_on_runs_the_pass_as_before(addressing):
    addressing(True)
    doc = S.address_scenes("p", "proxy.mp4", _scene([10240]), {"state": "provenance_missing",
                                                                 "clock": {"reasons": ["provenance_missing"]}})
    assert doc["state"] == "provenance_missing"
    assert doc["rows"][0]["state"] == "refused"


@pytest.mark.parametrize("state", ["proxy_missing", "decode_failed", "decode_mismatch"])
def test_a_failed_decode_wins_even_if_rows_came_back(addressing, addresser_forbidden, state):
    """The decode's own state decides first, whatever the rows say."""
    addressing(False)
    scene = {**_scene([10240]), "state": state, "reason": "x"}
    doc = S.address_scenes("p", "proxy.mp4", scene, {"state": "recorded"})
    assert doc["state"] == state and doc["state"] != "not_requested"
