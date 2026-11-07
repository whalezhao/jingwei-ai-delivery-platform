"""执行器与队列状态（只读，任意登录用户可见）。"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from ..config import get_settings
from ..deps import CurrentPerson, DbSession
from ..models import AppSetting, Run, WorkerHeartbeat
from ..schemas import WorkerOut, WorkerStatusOut

router = APIRouter(tags=["workers"])

_OCCUPYING = ("preparing", "running", "parsing")
_TERMINAL = ("succeeded", "failed", "cancelled", "timed_out")


@router.get("/api/workers/status", response_model=WorkerStatusOut)
async def worker_status(user: CurrentPerson, db: DbSession) -> WorkerStatusOut:
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.worker_stale_seconds)

    rows = list(
        (
            await db.execute(
                select(WorkerHeartbeat).order_by(WorkerHeartbeat.heartbeat_at.desc())
            )
        ).scalars()
    )
    active_by_owner = {
        owner: count
        for owner, count in (
            await db.execute(
                select(Run.owner_worker_id, func.count(Run.id))
                .where(Run.status.in_(_OCCUPYING), Run.owner_worker_id.is_not(None))
                .group_by(Run.owner_worker_id)
            )
        ).all()
    }
    workers = [
        WorkerOut(
            id=row.id,
            hostname=row.hostname,
            pid=row.pid,
            slots=row.slots,
            started_at=row.started_at,
            heartbeat_at=row.heartbeat_at,
            age_seconds=max(0, int((now - row.heartbeat_at).total_seconds())),
            active_runs=active_by_owner.get(row.id, 0),
        )
        for row in rows
        if row.heartbeat_at >= cutoff
    ]

    queued = int(
        (await db.execute(select(func.count(Run.id)).where(Run.status == "queued"))).scalar()
        or 0
    )
    active = int(
        (
            await db.execute(
                select(func.count(Run.id)).where(Run.status.in_(_OCCUPYING))
            )
        ).scalar()
        or 0
    )
    oldest = (
        await db.execute(
            select(func.min(Run.created_at)).where(Run.status == "queued")
        )
    ).scalar()
    avg_duration = (
        await db.execute(
            select(func.avg(Run.duration_ms)).where(
                Run.status.in_(_TERMINAL), Run.duration_ms.is_not(None)
            )
        )
    ).scalar()
    setting = await db.get(AppSetting, 1)

    return WorkerStatusOut(
        workers=workers,
        stale_workers=sum(1 for row in rows if row.heartbeat_at < cutoff),
        queued_runs=queued,
        active_runs=active,
        max_concurrent_runs=int(setting.max_concurrent_runs) if setting else 4,
        worker_stale_seconds=settings.worker_stale_seconds,
        avg_duration_ms=int(avg_duration) if avg_duration is not None else None,
        oldest_queue_seconds=(
            max(0, int((now - oldest).total_seconds())) if oldest else None
        ),
    )
