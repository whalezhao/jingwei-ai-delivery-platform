"""全局运行时设置。

- max_concurrent_runs：**全局**并发上限，所有 worker 加起来最多同时跑这么多 run。
- run_shard_size：单系统任务的分片粒度，0 表示一个系统一个 run（不分片）。

放在库里而不是环境变量，是为了能在界面上改、改完 worker 自动跟上。
"""

from fastapi import APIRouter

from ..deps import CurrentPerson, DbSession, require_admin
from ..models import AppSetting
from ..schemas import SettingsIn, SettingsOut

router = APIRouter(tags=["settings"])

DEFAULT_MAX_CONCURRENT_RUNS = 4
DEFAULT_RUN_SHARD_SIZE = 4


async def _row(db) -> AppSetting:
    """单行设置：没有就建一行默认值。"""
    row = await db.get(AppSetting, 1)
    if row is None:
        row = AppSetting(
            id=1,
            max_concurrent_runs=DEFAULT_MAX_CONCURRENT_RUNS,
            run_shard_size=DEFAULT_RUN_SHARD_SIZE,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def _out(row: AppSetting) -> SettingsOut:
    return SettingsOut(
        max_concurrent_runs=row.max_concurrent_runs,
        run_shard_size=row.run_shard_size,
        ai_assess_failures_only=row.ai_assess_failures_only,
        updated_at=row.updated_at,
    )


@router.get("/api/settings", response_model=SettingsOut)
async def get_settings(user: CurrentPerson, db: DbSession) -> SettingsOut:
    return _out(await _row(db))


@router.put("/api/settings", response_model=SettingsOut)
async def update_settings(
    body: SettingsIn, user: CurrentPerson, db: DbSession
) -> SettingsOut:
    # 全局并发上限影响所有系统，只有管理员能改
    await require_admin(user)
    row = await _row(db)
    if body.max_concurrent_runs is not None:
        row.max_concurrent_runs = body.max_concurrent_runs
    if body.run_shard_size is not None:
        row.run_shard_size = body.run_shard_size
    if body.ai_assess_failures_only is not None:
        row.ai_assess_failures_only = body.ai_assess_failures_only
    row.updated_by = user.id
    await db.commit()
    await db.refresh(row)
    return _out(row)
