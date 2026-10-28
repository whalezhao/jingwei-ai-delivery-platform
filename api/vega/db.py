from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def _engine() -> AsyncEngine:
    # sqlite 需要关 check_same_thread + busy timeout（并发写锁等待）；PG 不受影响
    url = get_settings().database_url
    args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    return create_async_engine(url, connect_args=args)


@lru_cache
def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_engine(), expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with _sessionmaker()() as session:
        yield session


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    return _sessionmaker()


def engine() -> AsyncEngine:
    return _engine()


async def init_db() -> None:
    # dev 阶段 create_all；部署前引入 Alembic（见 DESIGN.md 风险表）
    from . import models  # noqa: F401 确保模型注册

    async with _engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def backup_sqlite_db(keep: int = 7) -> str | None:
    """SQLite 一致性备份到同目录 backups/，保留最近 keep 份；PG 返回 None。

    用 sqlite3 backup API 而不是 cp：备份瞬间可能正有写入，cp 会拿到撕裂页。
    """
    import asyncio
    import datetime
    import sqlite3
    from pathlib import Path

    url = get_settings().database_url
    if not url.startswith("sqlite+aiosqlite://"):
        return None
    raw = url.removeprefix("sqlite+aiosqlite://")
    if raw.startswith("//"):  # ////绝对路径 会剩个双斜杠
        raw = raw[1:]
    src_path = Path(raw)
    if not src_path.exists():
        return None

    def _copy() -> str:
        dst_dir = src_path.parent / "backups"
        dst_dir.mkdir(exist_ok=True)
        dst = dst_dir / f"{src_path.stem}-{datetime.datetime.utcnow():%Y%m%d-%H%M%S-%f}{src_path.suffix}"
        with sqlite3.connect(str(src_path)) as s, sqlite3.connect(str(dst)) as d:
            s.backup(d)
        olds = sorted(dst_dir.glob(f"{src_path.stem}-*{src_path.suffix}"))
        for stale in olds[:-keep]:
            stale.unlink()
        return str(dst)

    return await asyncio.to_thread(_copy)
