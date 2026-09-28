"""
ClipForge Worker - Async SQLite database setup via SQLAlchemy 2.0
"""

import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings

logger = logging.getLogger("clipforge.db")


class Base(DeclarativeBase):
    pass


_db_url = f"sqlite+aiosqlite:///{settings.db_path}"
# timeout: how long a connection waits for the write lock before raising
# "database is locked". The default is 5s, which a long pipeline write can
# exceed while the UI is polling.
engine = create_async_engine(_db_url, echo=settings.debug, connect_args={"timeout": 30})


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """WAL + a real busy timeout on every connection.

    In the default rollback-journal mode a single writer blocks every reader,
    so a running pipeline made ordinary GETs fail with "database is locked" —
    the job queue writes progress once a second while the review UI polls. WAL
    lets readers run concurrently with the one writer, which is exactly this
    app's shape: one worker process writing, several pollers reading.

    NORMAL synchronous is the standard companion to WAL: durable across a
    process crash, and only at risk from an OS-level crash — acceptable for a
    local media scratch DB, and much cheaper than FULL on every progress tick.
    """
    cursor = dbapi_connection.cursor()
    try:
        # busy_timeout first, and unconditionally: it is the one that saves us
        # if the mode switch below cannot happen.
        cursor.execute("PRAGMA busy_timeout=30000")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            # Switching journal mode needs a brief exclusive lock, so it fails
            # while another process still has the file open in the old mode
            # (e.g. an older backend build during a rolling restart). Falling
            # back to the previous mode is strictly better than refusing to
            # open the connection at all.
            logging.getLogger("clipforge.db").warning(
                "could not enable WAL; continuing in the existing journal mode"
            )
    finally:
        cursor.close()
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


SCHEMA_VERSION = "2026-09-03-1"

_REQUIRED_MIGRATED_COLUMNS = {
    "jobs": {
        "worker_id", "lease_expires_at", "last_heartbeat", "attempt_count",
        "cancellation_requested", "idempotency_key",
    },
    "projects": {
        "processing_mode", "batch_id", "batch_index", "erase_params",
        "erased_video_path", "clipper_settings", "content_type",
        "content_type_confidence", "content_type_override", "analysis_version",
        "rights_confirmed", "source_kind",
        "analysis_generation",
    },
    "transcripts": {"failed_chunks"},
    "clips": {
        "hook_text", "explanation", "thumbnail_path", "caption_preset_id",
        "reframe_mode", "reframe_data", "export_path", "caption_style",
        "caption_y_pct", "caption_align", "hook_y_pct", "hook_align",
        "caption_font_size", "caption_text_color", "caption_highlight_color",
        "caption_outline_color", "caption_y_position", "hook_font_size",
        "hook_text_color", "hook_bg_color", "hook_y_position", "hook_box_size",
        "hook_box_width", "hook_duration_seconds", "hook_x", "hook_y",
        "subtitle_x", "subtitle_y", "export_resolution", "split_mode",
        "split_parts_count", "part_label_font_size", "part_label_box_size",
        "part_label_text_color", "part_label_bg_color", "part_label_x",
        "part_label_y", "export_parts", "hook_bg_enabled", "title_text",
        "title_font_size", "title_x", "title_y", "title_box_size",
        "title_box_width", "title_bg_enabled", "creator_tag_enabled",
        "creator_tag_text", "creator_tag_x", "creator_tag_y",
        "creator_tag_opacity", "creator_tag_font_size", "drive_folder_link",
        "overall_score", "sub_scores", "score_reason", "layout_plan",
        "caption_plan", "headline_text", "content_type", "warnings",
        "dedupe_group", "is_alternative", "rank_position", "feature_vector",
        "reasoning", "review", "ranker_version", "preview_path",
        "selection_run_id", "source_has_burned_captions", "export_job_id", "preview_record",
    },
}

_REQUIRED_INDEXES = {
    "idx_jobs_lease_expires_at", "idx_jobs_active_idempotency",
    "idx_projects_batch_id",
    "idx_clips_project_rank", "idx_clip_feedback_clip", "idx_clip_feedback_event",
}


async def _verify_migrations(conn) -> None:
    """Turn previously silent migration failures into startup failures."""
    from sqlalchemy import text

    missing: list[str] = []
    for table, columns in _REQUIRED_MIGRATED_COLUMNS.items():
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        missing.extend(f"{table}.{column}" for column in sorted(columns - existing))

    result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'"))
    indexes = {row[0] for row in result.fetchall()}
    missing.extend(f"index:{name}" for name in sorted(_REQUIRED_INDEXES - indexes))
    if missing:
        message = "database migration incomplete: " + ", ".join(missing)
        logger.error(message)
        raise RuntimeError(message)

    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_meta "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    ))
    await conn.execute(text(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', :version) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    ), {"version": SCHEMA_VERSION})


async def init_db() -> None:
    """Create all tables if they do not exist."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Job ownership/lease columns. `create_all()` covers new databases;
        # these additive statements keep existing installations compatible.
        _job_migrations = [
            ("worker_id", "VARCHAR(160)"),
            ("lease_expires_at", "DATETIME"),
            ("last_heartbeat", "DATETIME"),
            ("attempt_count", "INTEGER DEFAULT 0"),
            ("cancellation_requested", "BOOLEAN DEFAULT 0"),
            ("idempotency_key", "VARCHAR(64)"),
        ]
        for col, col_type in _job_migrations:
            try:
                await conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}"))
            except Exception:
                # Existing columns are expected during every later startup.
                # The broader migration error policy is unchanged in this
                # batch; lease behavior is guarded by the runtime checks.
                pass
        try:
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS idx_jobs_lease_expires_at ON jobs(lease_expires_at)")
            )
        except Exception:
            pass

        try:
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_idempotency "
                "ON jobs(idempotency_key) "
                "WHERE idempotency_key IS NOT NULL "
                "AND status IN ('queued', 'running')"
            ))
        except Exception:
            pass

        # Column migrations for clips table
        _clip_migrations = [
            ("content_confidence", "REAL"),
            ("content_type_origin", "VARCHAR(20)"),
            ("hook_text", "TEXT"),
            ("explanation", "TEXT"),
            ("thumbnail_path", "TEXT"),
            ("caption_preset_id", "VARCHAR(50)"),
            ("reframe_mode", "VARCHAR(20)"),
            ("reframe_data", "TEXT"),
            ("export_path", "TEXT"),
            # Legacy positioning (kept for backwards compat with older clips)
            ("caption_style", "TEXT"),
            ("caption_y_pct", "REAL"),
            ("caption_align", "VARCHAR(10)"),
            ("hook_y_pct", "REAL"),
            ("hook_align", "VARCHAR(10)"),
            ("shadow_rank", "INTEGER"),
            ("shadow_run_id", "VARCHAR(64)"),
            ("selection_run_id", "VARCHAR(64)"),
        ]
        for col_name, col_type in _clip_migrations:
            try:
                await conn.execute(
                    text(f"ALTER TABLE clips ADD COLUMN {col_name} {col_type}")
                )
            except Exception:
                pass  # column already exists

        # Style override columns on clips table
        _style_migrations = [
            ("caption_font_size", "INTEGER"),
            ("caption_text_color", "VARCHAR(20)"),
            ("caption_highlight_color", "VARCHAR(20)"),
            ("caption_outline_color", "VARCHAR(20)"),
            ("caption_y_position", "VARCHAR(20)"),
            ("hook_font_size", "INTEGER"),
            ("hook_text_color", "VARCHAR(20)"),
            ("hook_bg_color", "VARCHAR(20)"),
            ("hook_y_position", "VARCHAR(20)"),
            ("hook_box_size", "INTEGER"),
            ("hook_box_width", "INTEGER"),
            ("hook_duration_seconds", "REAL"),
            ("hook_x", "INTEGER"),
            ("hook_y", "INTEGER"),
            ("subtitle_x", "INTEGER"),
            ("subtitle_y", "INTEGER"),
            ("export_resolution", "VARCHAR(20)"),
            ("split_mode", "VARCHAR(10)"),
            ("split_parts_count", "INTEGER"),
            ("part_label_font_size", "INTEGER"),
            ("part_label_box_size", "INTEGER"),
            ("part_label_text_color", "VARCHAR(20)"),
            ("part_label_bg_color", "VARCHAR(20)"),
            ("part_label_x", "INTEGER"),
            ("part_label_y", "INTEGER"),
            ("export_parts", "TEXT"),
            ("hook_bg_enabled", "BOOLEAN DEFAULT 1"),
            ("title_text", "TEXT"),
            ("title_font_size", "INTEGER"),
            ("title_x", "INTEGER"),
            ("title_y", "INTEGER"),
            ("title_box_size", "INTEGER"),
            ("title_box_width", "INTEGER"),
            ("title_bg_enabled", "BOOLEAN DEFAULT 1"),
            ("creator_tag_enabled", "BOOLEAN DEFAULT 0"),
            ("creator_tag_text", "VARCHAR(200)"),
            ("creator_tag_x", "INTEGER"),
            ("creator_tag_y", "INTEGER"),
            ("creator_tag_opacity", "REAL"),
            ("creator_tag_font_size", "INTEGER"),
            ("drive_folder_link", "TEXT"),
        ]
        for col, col_type in _style_migrations:
            try:
                await conn.execute(text(f"ALTER TABLE clips ADD COLUMN {col} {col_type}"))
            except Exception:
                pass

        # Project-level processing mode (clipping vs full_video_parts)
        try:
            await conn.execute(text("ALTER TABLE projects ADD COLUMN processing_mode VARCHAR(30)"))
        except Exception:
            pass

        # Batch-processing columns: group of projects all sharing an erase
        # rectangle, submitted together via POST /api/utilities/batch.
        _project_batch_migrations = [
            ("batch_id", "VARCHAR(12)"),
            ("batch_index", "INTEGER"),
            ("erase_params", "TEXT"),
            ("erased_video_path", "TEXT"),
        ]
        for col, col_type in _project_batch_migrations:
            try:
                await conn.execute(text(f"ALTER TABLE projects ADD COLUMN {col} {col_type}"))
            except Exception:
                pass
        try:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_projects_batch_id ON projects(batch_id)"))
        except Exception:
            pass

        # ── AI Stream Clipper ────────────────────────────────────────────────
        # Additive only: every column is nullable and unread by existing code,
        # so an older build keeps working against a migrated DB (see the
        # rollback notes in docs/plans/ai-stream-clipper-repository-audit.md).
        _clipper_project_migrations = [
            ("clipper_settings", "TEXT"),
            ("content_type", "VARCHAR(30)"),
            ("content_type_confidence", "REAL"),
            ("content_type_override", "VARCHAR(30)"),
            ("analysis_version", "VARCHAR(20)"),
            ("rights_confirmed", "BOOLEAN"),
            ("source_kind", "VARCHAR(20)"),
            ("analysis_generation", "VARCHAR(64)"),
        ]
        for col, col_type in _clipper_project_migrations:
            try:
                await conn.execute(text(f"ALTER TABLE projects ADD COLUMN {col} {col_type}"))
            except Exception:
                pass

        # The transcripts table's first added column, so it gets its own list
        # rather than being smuggled into the clips one.
        for col, col_type in [("failed_chunks", "TEXT")]:
            try:
                await conn.execute(
                    text(f"ALTER TABLE transcripts ADD COLUMN {col} {col_type}"))
            except Exception:
                pass

        _clipper_clip_migrations = [
            ("overall_score", "REAL"),
            ("sub_scores", "TEXT"),
            ("score_reason", "TEXT"),
            ("layout_plan", "TEXT"),
            ("caption_plan", "TEXT"),
            ("headline_text", "TEXT"),
            ("content_type", "VARCHAR(30)"),
            ("warnings", "TEXT"),
            ("dedupe_group", "VARCHAR(12)"),
            ("is_alternative", "BOOLEAN"),
            ("rank_position", "INTEGER"),
            ("feature_vector", "TEXT"),
            ("reasoning", "TEXT"),
            ("review", "TEXT"),
            ("ranker_version", "VARCHAR(20)"),
            ("preview_path", "TEXT"),
            ("source_has_burned_captions", "BOOLEAN"),
            ("export_job_id", "VARCHAR(12)"),
            ("preview_record", "TEXT"),
        ]
        for col, col_type in _clipper_clip_migrations:
            try:
                await conn.execute(text(f"ALTER TABLE clips ADD COLUMN {col} {col_type}"))
            except Exception:
                pass

        _clip_feedback_migrations = [
            ("origin", "VARCHAR(10)"),
        ]
        for col, col_type in _clip_feedback_migrations:
            try:
                await conn.execute(
                    text(f"ALTER TABLE clip_feedback ADD COLUMN {col} {col_type}"))
            except Exception:
                pass

        # Ranked review lists sort by (project_id, rank_position) constantly.
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_clips_project_rank ON clips(project_id, rank_position)",
            "CREATE INDEX IF NOT EXISTS idx_clip_feedback_clip ON clip_feedback(clip_id)",
            "CREATE INDEX IF NOT EXISTS idx_clip_feedback_event ON clip_feedback(event_type)",
        ):
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass

        # Fix any projects stuck at 'downloaded' that already have scored clips
        try:
            await conn.execute(text("""
                UPDATE projects SET status = 'ready'
                WHERE status = 'downloaded'
                  AND id IN (
                    SELECT DISTINCT project_id FROM clips
                    WHERE hook_text IS NOT NULL
                  )
            """))
        except Exception:
            pass


        await _verify_migrations(conn)

async def get_session():
    """FastAPI dependency that yields a session."""
    async with async_session() as session:
        yield session
