"""`clips.source_has_burned_captions` on a database that predates it.

PRP: PRPs/clipper-master-plan-2026-09-24.md §4 (B1). A scratch file only — the
production DB is never opened. The rows are written through the ORM while the
table still has every column, then the column is dropped: that is the shape of
an installation older than the column, rows included. `init_db()` then runs
twice; the second run is the ordinary next startup.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

COLUMN = "source_has_burned_captions"


async def _snapshot(eng) -> tuple[list[str], list[tuple]]:
    async with eng.connect() as conn:
        result = await conn.execute(text("SELECT * FROM clips ORDER BY id"))
        return list(result.keys()), [tuple(r) for r in result.fetchall()]


async def test_an_old_database_gets_the_column_null_and_keeps_every_other_value(tmp_path):
    import database
    from models import ClipModel, ProjectModel

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    original, database.engine = database.engine, eng
    try:
        await database.init_db()
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            s.add(ProjectModel(id="migp", title="old", source_kind="file", status="ready"))
            s.add(ClipModel(id="migc1", project_id="migp", title="kept title",
                            start_time=1.5, end_time=6.25, duration=4.75,
                            status="exported", export_path="/tmp/old.mp4",
                            headline_text="old headline", rank_position=2,
                            caption_plan={"preset_id": "clean", "y_pct": 61.5},
                            selection_run_id="oldrun000001", **{COLUMN: True}))
            s.add(ClipModel(id="migc2", project_id="migp", title="second",
                            start_time=10.0, end_time=12.0, duration=2.0,
                            status="candidate", **{COLUMN: False}))
            await s.commit()
        async with eng.begin() as conn:
            await conn.execute(text(f"ALTER TABLE clips DROP COLUMN {COLUMN}"))
        old_cols, old_rows = await _snapshot(eng)
        assert COLUMN not in old_cols and len(old_rows) == 2

        await database.init_db()
        first = await _snapshot(eng)
        await database.init_db()
        second = await _snapshot(eng)
    finally:
        database.engine = original
        await eng.dispose()

    assert first == second, "the second migration changed the table"
    cols, rows = second
    assert COLUMN in cols
    at = cols.index(COLUMN)
    assert [r[at] for r in rows] == [None, None]    # nobody has declared it here
    stripped = [tuple(v for i, v in enumerate(r) if i != at) for r in rows]
    assert [c for c in cols if c != COLUMN] == old_cols
    assert stripped == old_rows
