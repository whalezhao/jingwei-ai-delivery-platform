"""需求/任务编号：REQ-<前缀>-YYMM-NNN / TASK-<需求号>-N。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DevTask, Requirement


def _ym() -> str:
    return datetime.now(timezone.utc).strftime("%y%m")


async def next_requirement_code(db: AsyncSession, prefix: str) -> str:
    stem = f"REQ-{prefix}-{_ym()}-"
    # ponytail: count+1 取号，单进程写路径下足够；并发开号冲突靠 code unique 约束兜底
    n = (await db.scalar(select(func.count()).select_from(Requirement).where(Requirement.code.like(stem + "%")))) or 0
    return f"{stem}{n + 1:03d}"


async def next_task_code(db: AsyncSession, requirement: Requirement) -> str:
    stem = f"TASK-{requirement.code}-"
    n = (await db.scalar(select(func.count()).select_from(DevTask).where(DevTask.code.like(stem + "%")))) or 0
    return f"{stem}{n + 1}"


def gen_session_id() -> str:
    import uuid

    return str(uuid.uuid4())
