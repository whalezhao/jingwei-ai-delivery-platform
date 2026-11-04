"""APScheduler 定时任务。测试与本地调试用 VEGA_DISABLE_SCHEDULER=1 关闭。"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .db import sessionmaker
from .autoflow import mr_autoflow_sweep
from .jobs import (
    _group_tech_leads,
    blocked_mr_scan,
    daily_commit_stats,
    merge_poll,
    scan_projects,
    sync_members_and_match,
)
from .notify import notify
from .reports import build_code_report, report_range
from .models import DevGroup
from .queue import requeue_dead_nodes

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def _groups() -> list[DevGroup]:
    async with sessionmaker()() as s:
        return list((await s.scalars(select(DevGroup).where(DevGroup.scan_enabled.is_(True)))).all())


async def _job_scan_and_sync() -> None:
    for group in await _groups():
        async with sessionmaker()() as s:
            g = await s.get(DevGroup, group.id)
            try:
                log.info("scan %s: %s", g.name, await scan_projects(s, g))
                log.info("sync %s: %s", g.name, await sync_members_and_match(s, g))
            except Exception:  # noqa: BLE001 单组失败不拖垮其他组
                log.exception("scan/sync failed for %s", g.name)


async def _job_daily_stats() -> None:
    for group in await _groups():
        async with sessionmaker()() as s:
            g = await s.get(DevGroup, group.id)
            try:
                log.info("daily_stats %s: %s", g.name, await daily_commit_stats(s, g))
            except Exception:  # noqa: BLE001
                log.exception("daily_stats failed for %s", g.name)


async def _job_code_report(kind: str) -> None:
    """周报/月报：只读已落库的数据，不再回拉 GitLab。"""
    for group in await _groups():
        async with sessionmaker()() as s:
            g = await s.get(DevGroup, group.id)
            try:
                start, end = report_range(kind)
                text = await build_code_report(s, g, start, end, kind)
                await notify(
                    s, g, text, reason=f"code_report_{kind}", mention=await _group_tech_leads(s, g)
                )
            except Exception:  # noqa: BLE001 单组失败不拖垮其他组
                log.exception("code_report %s failed for %s", kind, g.name)


async def _job_blocked_mrs() -> None:
    for group in await _groups():
        async with sessionmaker()() as s:
            g = await s.get(DevGroup, group.id)
            try:
                log.info("blocked_mrs %s: %s", g.name, await blocked_mr_scan(s, g))
            except Exception:  # noqa: BLE001
                log.exception("blocked_mrs failed for %s", g.name)


async def _job_merge_poll() -> None:
    async with sessionmaker()() as s:
        try:
            await merge_poll(s)
        except Exception:  # noqa: BLE001
            log.exception("merge_poll failed")


async def _job_requeue_dead() -> None:
    try:
        n = await requeue_dead_nodes()
        if n:
            log.info("requeued %s tasks from dead runner nodes", n)
    except Exception:  # noqa: BLE001
        log.exception("requeue_dead_nodes failed")


async def _job_mr_autoflow() -> None:
    for group in await _groups():
        async with sessionmaker()() as s:
            g = await s.get(DevGroup, group.id)
            try:
                log.info("mr_autoflow %s: %s", g.name, await mr_autoflow_sweep(s, g))
            except Exception:  # noqa: BLE001
                log.exception("mr_autoflow failed for %s", g.name)


async def _job_egm_drift() -> None:
    from .routers.systems import egm_drift_sweep

    async with sessionmaker()() as s:
        try:
            n = await egm_drift_sweep(s)
            log.info("egm_drift queued %s project(s)", n)
        except Exception:  # noqa: BLE001
            log.exception("egm_drift failed")


async def _job_backup_db() -> None:
    try:
        from .db import backup_sqlite_db

        path = await backup_sqlite_db()
        if path:
            log.info("db backed up to %s", path)
    except Exception:  # noqa: BLE001
        log.exception("backup_sqlite_db failed")


def start_scheduler() -> None:
    scheduler.add_job(_job_scan_and_sync, "interval", minutes=30, id="scan_and_sync", max_instances=1)
    scheduler.add_job(_job_daily_stats, "cron", hour=9, minute=5, id="daily_stats", max_instances=1)
    scheduler.add_job(
        _job_code_report, "cron", day_of_week="mon", hour=9, minute=10, args=["weekly"], id="weekly_stats", max_instances=1
    )
    scheduler.add_job(
        _job_code_report, "cron", day=1, hour=9, minute=15, args=["monthly"], id="monthly_stats", max_instances=1
    )
    scheduler.add_job(
        _job_egm_drift, "cron", day_of_week="mon", hour=9, minute=17, id="egm_drift", max_instances=1
    )
    scheduler.add_job(_job_backup_db, "cron", hour=3, minute=17, id="backup_db", max_instances=1)
    scheduler.add_job(_job_blocked_mrs, "cron", hour=9, minute=0, id="blocked_mrs_9", max_instances=1)
    scheduler.add_job(_job_blocked_mrs, "interval", hours=1, id="blocked_mrs_hourly", max_instances=1)
    scheduler.add_job(_job_merge_poll, "interval", minutes=10, id="merge_poll", max_instances=1)
    scheduler.add_job(_job_mr_autoflow, "interval", minutes=5, id="mr_autoflow", max_instances=1)
    scheduler.add_job(_job_requeue_dead, "interval", minutes=1, id="requeue_dead", max_instances=1)
    scheduler.start()
    log.info("scheduler started")


async def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
    await asyncio.sleep(0)
