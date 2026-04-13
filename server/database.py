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
        # Add new columns to existing SQLite DB (safe — silently skips if already present)
        _clip_migrations = [
            ("hook_text",      "TEXT"),
            ("explanation",    "TEXT"),
            ("thumbnail_path", "TEXT"),
            ("caption_style",  "TEXT"),
            ("caption_y_pct",  "REAL"),
            ("caption_align",  "VARCHAR(10)"),
            ("hook_y_pct",     "REAL"),
            ("hook_align",          "VARCHAR(10)"),
            ("caption_font_size",   "REAL"),
            ("caption_text_color",  "VARCHAR(9)"),
            ("hook_font_size",      "REAL"),
            ("hook_text_color",     "VARCHAR(9)"),
            ("hook_bg_color",       "VARCHAR(9)"),
        ]
        for col, col_type in _clip_migrations:
            try:
                await conn.execute(text(f"ALTER TABLE clips ADD COLUMN {col} {col_type}"))
            except Exception:
                pass
        # Fix any projects stuck at 'downloaded' that already have scored clips
        await conn.execute(text("""
            UPDATE projects SET status = 'ready'
            WHERE status = 'downloaded'
              AND id IN (
                SELECT DISTINCT project_id FROM clips
                WHERE hook_text IS NOT NULL
              )
        """))


async def get_session():
    """FastAPI dependency that yields a session."""
    async with async_session() as session:
        yield session