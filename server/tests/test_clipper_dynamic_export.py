"""The wire between the dynamic editor and the export path.

The editor, its cameras and its renderer were complete and tested for months
while NOTHING in workers/ or routers/ imported any of them — every clip the
pipeline ever produced was a static split screen. These tests exist so that
cannot silently become true again, and so the fallbacks are the ones intended:
a clip that cannot be cut dynamically must still export, statically.
"""

from __future__ import annotations

import pytest

from workers import clipper_render_jobs as jobs


class _Clip:
    id = "c1"
    project_id = "p1"
    start_time = 100.0
    end_time = 130.0
    transcript_text = "hello there"
    headline_text = ""


class _Project:
    id = "p1"
    width = 1920
    height = 1080
    clipper_settings: dict = {}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """_dynamic_plan with the media analysis and the planner replaced."""
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"x")

    monkeypatch.setattr(jobs.storage, "paths", lambda _pid: {"proxy": proxy})
    monkeypatch.setattr(jobs.storage, "read_artifact",
                        lambda _pid, _name: {"proxy_width": 480, "proxy_height": 270})

    from services.clipper import dynamic_edit, dynamic_window

    monkeypatch.setattr(dynamic_window, "analyse_window", lambda *a, **k: {
        "faces": [{"t": 100.0, "boxes": [[10, 10, 40, 40]]}],
        "motion": [0.1, 0.2], "focus": [-1.0, 120.0], "detail": [3.0, 4.0],
        "ui": [0.0, 0.0], "band": (0.25, 1.0, 0.0, 0.8), "hop": 0.25,
    })
    return dynamic_edit, proxy


async def test_a_planned_edit_records_the_frame_it_was_measured_in(wired, monkeypatch):
    """Same lesson as the static layout plan: a plan is crops in source pixels
    and is meaningless without the frame they were measured against."""
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face"}, {"camera": "game"}], "warnings": []})

    plan = await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080)
    assert plan["src_w"] == 1920 and plan["src_h"] == 1080
    assert plan["band"] == [0.25, 1.0, 0.0, 0.8]
    assert plan["faces_seen"] == 1


async def test_a_single_shot_falls_back_to_the_static_layout(wired, monkeypatch):
    """One shot is a static crop with extra steps, and the static path does it
    better — it keeps the face band and the chat exclusion."""
    dynamic_edit, _proxy = wired
    monkeypatch.setattr(dynamic_edit, "plan_dynamic_edit", lambda *a, **k: {
        "shots": [{"camera": "face"}], "warnings": []})

    assert await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080) is None


async def test_no_proxy_means_no_dynamic_edit(monkeypatch, tmp_path):
    """The editor measures the PROXY. Without one there is nothing to plan from,
    and losing the export over it would be worse than a static render."""
    monkeypatch.setattr(jobs.storage, "paths",
                        lambda _pid: {"proxy": tmp_path / "missing.mp4"})
    assert await jobs._dynamic_plan(_Clip(), _Project(), 1920, 1080) is None


async def test_a_zero_length_window_is_refused(wired, monkeypatch):
    clip = _Clip()
    clip.end_time = clip.start_time
    assert await jobs._dynamic_plan(clip, _Project(), 1920, 1080) is None


def test_the_export_handler_reaches_the_dynamic_renderer():
    """The assertion that would have caught the whole gap: the export path has
    to name the dynamic renderer, not merely have one available in the tree."""
    import inspect

    src = inspect.getsource(jobs.handle_export)
    assert "dynamic_render" in src, "handle_export cannot reach the multi-shot renderer"
    assert "_dynamic_plan" in src, "handle_export never plans a shot list"


def test_the_multi_shot_path_is_off_by_default():
    """Every clip so far took the static path; turning this on changes what the
    deliverable looks like, so it is opted into."""
    from config import Settings

    assert Settings().clipper_dynamic_edit is False
