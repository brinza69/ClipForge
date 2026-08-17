"""Hands-off mode: a link turns into files without a second visit.

The pipeline has always stopped at scoring, and stopping there is right when a
person is going to look at the board — rendering is the one stage that costs
minutes and writes files. It is the wrong answer for the other use, which is
pasting a link and walking away.

The test that matters most is the OFF one: this must not change what an
ordinary run does.
"""

from __future__ import annotations

import pytest

from models import ClipModel, ClipStatus
from workers import clipper_build


class _Queue:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def enqueue(self, **kw):
        self.jobs.append(kw)
        return f"job{len(self.jobs)}"

    async def update_progress(self, *a, **k):
        return None

    def is_cancelled(self, job_id):
        return False


@pytest.fixture
async def project_with_clips():
    """Six winners and an alternative, scored apart so the order is unambiguous.

    The throwaway database lives for the whole session, so this clears its own
    rows first — otherwise the second test to use it collides on the ids the
    first one inserted.
    """
    from sqlalchemy import delete

    from database import async_session
    from models import ProjectModel

    async with async_session() as session:
        await session.execute(delete(ClipModel).where(ClipModel.project_id == "autoexp"))
        await session.execute(delete(ProjectModel).where(ProjectModel.id == "autoexp"))
        session.add(ProjectModel(id="autoexp", title="auto export",
                                 source_kind="url", status="ready"))
        for i in range(6):
            session.add(ClipModel(
                id=f"c{i}", project_id="autoexp", title=f"clip {i}",
                start_time=float(i * 10), end_time=float(i * 10 + 20),
                duration=20.0, overall_score=90.0 - i,
                status=ClipStatus.candidate.value, is_alternative=False))
        session.add(ClipModel(
            id="alt", project_id="autoexp", title="alternative",
            start_time=0.0, end_time=20.0, duration=20.0,
            overall_score=99.0,                       # would win on score alone
            status=ClipStatus.candidate.value, is_alternative=True))
        await session.commit()
    return "autoexp"


async def test_off_by_default_queues_nothing(project_with_clips):
    """The whole point of the flag. An ordinary run still ends at the board."""
    queue = _Queue()
    assert await clipper_build._auto_export(project_with_clips, {}, queue) == 0
    assert queue.jobs == []


async def test_it_queues_the_best_n_one_job_each(project_with_clips):
    queue = _Queue()
    n = await clipper_build._auto_export(project_with_clips, {"auto_export": 3}, queue)
    assert n == 3
    assert [j["clip_id"] for j in queue.jobs] == ["c0", "c1", "c2"]
    # One job per clip, not one for the batch: a failed render should cost its
    # own clip, and the board fills in as they land.
    assert len(queue.jobs) == 3
    assert all(j["job_type"] == "clipper_export" for j in queue.jobs)


async def test_alternatives_are_never_rendered(project_with_clips):
    """They exist so a person can compare two cuts of one moment. Rendering
    both is the duplication dedupe just removed — and this one outscores every
    winner, so a naive "top by score" would pick it first."""
    queue = _Queue()
    await clipper_build._auto_export(project_with_clips, {"auto_export": 10}, queue)
    assert "alt" not in [j["clip_id"] for j in queue.jobs]


async def test_asking_for_more_than_exists_queues_what_exists(project_with_clips):
    queue = _Queue()
    assert await clipper_build._auto_export(
        project_with_clips, {"auto_export": 50}, queue) == 6


async def test_an_already_exported_clip_is_not_rendered_again(project_with_clips):
    """`_write_clips` preserves exported clips across a re-score on purpose, so
    a re-run with hands-off on must not re-render what is already on disk."""
    from sqlalchemy import update

    from database import async_session

    async with async_session() as session:
        await session.execute(
            update(ClipModel).where(ClipModel.id == "c0")
            .values(status=ClipStatus.exported.value))
        await session.commit()

    queue = _Queue()
    await clipper_build._auto_export(project_with_clips, {"auto_export": 3}, queue)
    assert [j["clip_id"] for j in queue.jobs] == ["c1", "c2", "c3"]


async def test_a_nonsense_setting_is_off_rather_than_a_crash(project_with_clips):
    """It arrives from a JSON settings blob a user can edit."""
    queue = _Queue()
    for bad in ("", "lots", None, -3, [1]):
        assert await clipper_build._auto_export(
            project_with_clips, {"auto_export": bad}, queue) == 0
    assert queue.jobs == []


def test_the_config_default_is_off():
    from config import Settings

    assert Settings().clipper_auto_export == 0


def test_the_setting_survives_project_creation():
    """`_normalise_settings` keeps only keys present in the defaults, so a key
    added to the frontend and not to that dict is dropped in silence. This one
    was — the checkbox posted `auto_export` and the API threw it away, and no
    unit test of `_auto_export` could have shown it because the value never got
    that far."""
    from routers.clipper import _normalise_settings

    assert _normalise_settings({"auto_export": 5})["auto_export"] == 5
    assert _normalise_settings({})["auto_export"] == 0
    # Clamped like clip_count: unattended render time needs a ceiling.
    assert _normalise_settings({"auto_export": 999})["auto_export"] == 20
    assert _normalise_settings({"auto_export": "nonsense"})["auto_export"] == 0
