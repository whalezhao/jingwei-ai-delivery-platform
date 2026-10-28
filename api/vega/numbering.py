"""需求/任务编号：REQ-<前缀>-YYMM-NNN / TASK-<需求号>-N。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DevTask, Requirement


def _ym() -> str:
    return datetime.now(timezone.utc).strftime("%y%m")


async def _next_suffix(db: AsyncSession, column, stem: str) -> int:
    """该前缀下已用最大序号 +1。

    不能用 count：删过一条之后 count 会退回去，新号正好撞上仍在库里的旧号
    （code 有 unique 约束），需求删除功能一上线就会复现。
    """
    top = 0
    for code in await db.scalars(select(column).where(column.like(stem + "%"))):
        tail = code[len(stem):]
        if tail.isdigit():
            top = max(top, int(tail))
    return top + 1


async def next_requirement_code(db: AsyncSession, prefix: str) -> str:
    stem = f"REQ-{prefix}-{_ym()}-"
    n = await _next_suffix(db, Requirement.code, stem)
    return f"{stem}{n:03d}"


async def next_task_code(db: AsyncSession, requirement: Requirement) -> str:
    stem = f"TASK-{requirement.code}-"
    return f"{stem}{await _next_suffix(db, DevTask.code, stem)}"


def gen_session_id() -> str:
    import uuid

    return str(uuid.uuid4())
