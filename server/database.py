"""
ClipForge Worker - Async SQLite database setup via SQLAlchemy 2.0
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


class Base(DeclarativeBase):
    pass


_db_url = f"sqlite+aiosqlite:///{settings.db_path}"
engine = create_async_engine(_db_url, echo=settings.debug)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables if they do not exist."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Column migrations for clips table
        _clip_migrations = [
            ("hook_text", "TEXT"),
            ("explanation", "TEXT"),
            ("thumbnail_path", "TEXT"),
            ("caption_preset_id", "VARCHAR(50)"),
            ("reframe_mode", "VARCHAR(20)"),
            ("reframe_data", "TEXT"),
            ("export_path", "TEXT"),
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
            ("export_resolution", "VARCHAR(20)"),
        ]
        for col, col_type in _style_migrations:
            try:
                await conn.execute(text(f"ALTER TABLE clips ADD COLUMN {col} {col_type}"))
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


async def get_session():
    """FastAPI dependency that yields a session."""
    async with async_session() as session:
        yield session