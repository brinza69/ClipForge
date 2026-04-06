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
        # Add new columns to existing SQLite DB
        for col in ["hook_text", "explanation", "thumbnail_path"]:
            try:
                await conn.execute(text(f"ALTER TABLE clips ADD COLUMN {col} TEXT"))
            except Exception:
                pass


async def get_session():
    """FastAPI dependency that yields a session."""
    async with async_session() as session:
        yield session