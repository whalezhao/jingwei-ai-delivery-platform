"""执行产物的定期清理。

每次执行都在 runner/.runs/<run_id>/ 留一份完整产物（截图内联的报告几 MB）。之前只有
一个手动脚本，实际没人跑 —— 连续跑几天就堆到几百 MB。现在把它变成 API 进程里的
常驻协程：启动先清一次，之后每 24 小时一次。

保留策略（默认值可在 .env 覆盖）：
- 每个任务保留最近 N 个批次的产物（同一批次的分片 run 一起保留）；
- 没有任务归属的 run 只保留最近 N 天。

删的只是文件，数据库里的指标不受影响 —— 产物是原始凭证，统计全走 SQL。
"""

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import SessionLocal
from .models import Run

log = logging.getLogger("retention")

TERMINAL = ("succeeded", "failed", "cancelled", "timed_out")
INTERVAL_SECONDS = 24 * 3600


async def collect_artifacts(
    db: AsyncSession, runner_root: Path, keep_days: int, keep_per_task: int
) -> list[tuple[Path, str]]:
    """返回 (要删的工作目录, 原因)。两种规则取并集。"""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    runs = list(
        (
            await db.execute(
                select(Run).where(Run.status.in_(TERMINAL)).order_by(Run.created_at.desc())
            )
        ).scalars()
    )

    kept_batches: dict[str, int] = {}
    seen_batches: dict[str, set[str]] = {}
    kept_legacy_runs: dict[str, int] = {}
    victims: dict[Path, str] = {}

    for run in runs:
        if not run.work_dir:
            continue
        work_dir = Path(run.work_dir)
        if not work_dir.is_relative_to(runner_root) or not work_dir.exists():
            continue

        if run.task_id is not None:
            key = str(run.task_id)
            if run.batch_id is not None:
                # 分片后一个批次会有多个 run；按「批次」计数，避免把同一批的
                # 后续分片当成超限产物删掉，导致聚合报告缺块。
                batch_key = str(run.batch_id)
                seen = seen_batches.setdefault(key, set())
                if batch_key not in seen:
                    seen.add(batch_key)
                    kept_batches[key] = kept_batches.get(key, 0) + 1
                if kept_batches[key] <= keep_per_task:
                    continue
                victims[work_dir] = (
                    f"超出任务保留批次数（每任务保留最近 {keep_per_task} 个批次）"
                )
                continue

            # 老数据可能没有 batch，仍按 run 计数
            kept_legacy_runs[key] = kept_legacy_runs.get(key, 0) + 1
            if kept_legacy_runs[key] <= keep_per_task:
                continue
            victims[work_dir] = f"超出任务保留条数（每任务保留最近 {keep_per_task} 次）"
            continue

        if run.started_at is not None and run.started_at < cutoff:
            victims[work_dir] = f"早于 {keep_days} 天"

    return sorted(victims.items(), key=lambda item: str(item[0]))


async def purge_artifacts_once() -> tuple[int, int]:
    """执行一次清理，返回 (命中数, 实际删除数)。"""
    settings = get_settings()
    if not settings.artifact_cleanup_enabled:
        return (0, 0)

    async with SessionLocal() as db:
        victims = await collect_artifacts(
            db,
            settings.runner_path,
            settings.artifact_retention_days,
            settings.artifact_keep_per_task,
        )

    removed = 0
    for path, _reason in victims:
        # collect_artifacts 已经校验过落在 runner 目录内，这里再判一次不亏
        if path.is_relative_to(settings.runner_path):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return (len(victims), removed)


async def cleanup_loop() -> None:
    """启动即清一次，之后每 24 小时一次。异常不能把循环打死。"""
    while True:
        try:
            matched, removed = await purge_artifacts_once()
            if removed:
                log.info("清理执行产物：命中 %d 个目录，删除 %d 个", matched, removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("执行产物清理失败")
        await asyncio.sleep(INTERVAL_SECONDS)
