"""执行前预检：把「点了执行才失败」的问题提前说清楚。"""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .api.task_common import resolve_run_groups
from .config import get_settings
from .environments import (
    EnvironmentResolutionError,
    environment_for_system,
    resolve_run_environments,
)
from .modelconfig import missing_fields
from .models import (
    System,
    SystemModelConfig,
    SystemModelHealth,
    TestTask,
    WorkerHeartbeat,
)
from .quota import system_month_usage

_PROBE_TIMEOUT = 5.0


def _check(
    key: str,
    ok: bool,
    message: str,
    *,
    severity: str = "error",
    overridable: bool = False,
    system_id=None,
    system_name: str | None = None,
) -> dict:
    return {
        "key": key,
        "ok": ok,
        "severity": "ok" if ok else severity,
        "message": message,
        "overridable": overridable,
        "system_id": system_id,
        "system_name": system_name,
    }


def summarize_checks(checks: list[dict]) -> bool:
    """只有「不可覆盖的 error」才阻断执行。"""
    return not any(
        (not c["ok"]) and c["severity"] == "error" and not c["overridable"]
        for c in checks
    )


async def _probe_url(client: httpx.AsyncClient, url: str | None) -> tuple[bool, str]:
    if not url:
        return False, "没有配置被测地址"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, "被测地址必须是 http/https URL"
    try:
        # 任何 HTTP 状态都算可达：401/404 说明服务在，只是路径/鉴权不同
        await client.get(url)
        return True, "被测地址可访问"
    except (httpx.HTTPError, OSError) as exc:
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            return (
                False,
                (
                    "平台侧无法访问 localhost（执行器若使用 host 网络仍可能可达）："
                    f"{type(exc).__name__}"
                ),
            )
        return False, f"被测地址不可访问：{type(exc).__name__}"


async def run_system_preflight(
    db: AsyncSession, system: System, *, environment_name: str | None = None
) -> dict:
    """配置页的系统级预检：模型、模型健康、被测地址、Worker。"""
    settings = get_settings()
    environment_name = (environment_name or "").strip() or "默认"
    checks: list[dict] = []

    config = await db.get(SystemModelConfig, system.id)
    gaps = missing_fields(config)
    checks.append(
        _check(
            "model_config",
            not gaps,
            "模型配置完整" if not gaps else f"模型配置还差：{'、'.join(gaps)}",
            system_id=system.id,
            system_name=system.name,
        )
    )

    health = await db.get(SystemModelHealth, system.id)
    fresh_after = datetime.now(UTC) - timedelta(
        seconds=max(60, settings.model_health_interval_seconds) * 2
    )
    if health is None or health.checked_at < fresh_after:
        checks.append(
            _check(
                "model_health",
                False,
                "还没有近期的模型连通性记录，可先点「测试连接」",
                severity="warning",
                system_id=system.id,
                system_name=system.name,
            )
        )
    else:
        checks.append(
            _check(
                "model_health",
                health.status == "healthy",
                health.message or "模型健康检查未通过",
                severity="error",
                overridable=True,
                system_id=system.id,
                system_name=system.name,
            )
        )

    env = await environment_for_system(
        db, system.id, environment_name=environment_name
    )
    url = env.base_url if env is not None else None
    async with httpx.AsyncClient(
        timeout=_PROBE_TIMEOUT, trust_env=False, follow_redirects=False
    ) as client:
        ok, message = await _probe_url(client, url)
    checks.append(
        _check(
            "base_url",
            ok,
            message,
            severity="warning",
            overridable=True,
            system_id=system.id,
            system_name=system.name,
        )
    )

    stale_before = datetime.now(UTC) - timedelta(seconds=settings.worker_stale_seconds)
    online = (
        await db.execute(
            select(WorkerHeartbeat.id).where(WorkerHeartbeat.heartbeat_at >= stale_before)
        )
    ).first()
    checks.append(
        _check(
            "worker",
            online is not None,
            "测试 Worker 在线" if online is not None else "没有在线的测试 Worker，任务会一直排队",
        )
    )

    return {
        "checks": checks,
        "can_run": summarize_checks(checks),
        "environment_name": environment_name,
    }


async def run_preflight(
    db: AsyncSession, task: TestTask, *, environment_name: str | None = None
) -> dict:
    settings = get_settings()
    system_ids = set(await resolve_run_groups(db, task))
    checks: list[dict] = []
    environment_name = (
        environment_name or task.environment_name or ""
    ).strip() or "默认"

    if not system_ids:
        checks.append(_check("cases", False, "任务下没有可执行的用例"))
        return {
            "checks": checks,
            "can_run": False,
            "environment_name": environment_name,
        }

    checks.append(
        _check("cases", True, f"共 {len(system_ids)} 个系统有待执行用例")
    )

    environments = {}
    try:
        environments = await resolve_run_environments(
            db, task, system_ids, environment_name=environment_name
        )
        checks.append(
            _check(
                "environment", True, f"环境「{environment_name}」在所选系统所属项目下都存在"
            )
        )
    except EnvironmentResolutionError as exc:
        checks.append(_check("environment", False, str(exc)))

    system_rows = (
        await db.execute(
            select(System.id, System.name).where(
                System.id.in_(system_ids)
            )
        )
    ).all()
    system_names = {row.id: row.name for row in system_rows}
    # 模型配置完整性
    configs: dict = {}
    for system_id in sorted(system_ids, key=str):
        config = await db.get(SystemModelConfig, system_id)
        configs[system_id] = config
        gaps = missing_fields(config)
        checks.append(
            _check(
                "model_config",
                not gaps,
                "模型配置完整" if not gaps else f"模型配置还差：{'、'.join(gaps)}",
                system_id=system_id,
                system_name=system_names.get(system_id),
            )
        )

    # 模型健康：用最近一次巡检结果，不在这里额外烧 token
    health_rows = {
        row.system_id: row
        for row in (
            await db.execute(
                select(SystemModelHealth).where(
                    SystemModelHealth.system_id.in_(system_ids)
                )
            )
        ).scalars()
    }
    fresh_after = datetime.now(UTC) - timedelta(
        seconds=max(60, settings.model_health_interval_seconds) * 2
    )
    for system_id in sorted(system_ids, key=str):
        row = health_rows.get(system_id)
        name = system_names.get(system_id)
        if row is None or row.checked_at < fresh_after:
            checks.append(
                _check(
                    "model_health",
                    False,
                    "还没有近期的模型连通性记录，可先点「测试连接」",
                    severity="warning",
                    system_id=system_id,
                    system_name=name,
                )
            )
        elif row.status == "healthy":
            checks.append(
                _check(
                    "model_health",
                    True,
                    "模型连通正常",
                    system_id=system_id,
                    system_name=name,
                )
            )
        else:
            checks.append(
                _check(
                    "model_health",
                    False,
                    row.message or "模型健康检查未通过",
                    severity="error",
                    overridable=True,
                    system_id=system_id,
                    system_name=name,
                )
            )

    # 额度
    for system_id in sorted(system_ids, key=str):
        config = configs.get(system_id)
        quota = config.token_quota if config else None
        used = await system_month_usage(db, system_id)
        if quota and quota > 0 and used >= quota:
            checks.append(
                _check(
                    "quota",
                    False,
                    f"本月 token 已用尽（{used:,} / {quota:,}）",
                    system_id=system_id,
                    system_name=system_names.get(system_id),
                )
            )
        else:
            checks.append(
                _check(
                    "quota",
                    True,
                    f"本月已用 {used:,} tokens" + (f" / {quota:,}" if quota else "（不限）"),
                    system_id=system_id,
                    system_name=system_names.get(system_id),
                )
            )

    # 被测地址：并行探测，避免串行等待
    async with httpx.AsyncClient(
        timeout=_PROBE_TIMEOUT, trust_env=False, follow_redirects=False
    ) as client:
        async def probe(system_id):
            env = environments.get(system_id)
            url = env.base_url if env is not None else None
            ok, message = await _probe_url(client, url)
            checks.append(
                _check(
                    "base_url",
                    ok,
                    message,
                    severity="warning",
                    overridable=True,
                    system_id=system_id,
                    system_name=system_names.get(system_id),
                )
            )

        await asyncio.gather(*(probe(pid) for pid in sorted(system_ids, key=str)))

    # 执行器在线
    stale_before = datetime.now(UTC) - timedelta(seconds=settings.worker_stale_seconds)
    online = (
        await db.execute(
            select(WorkerHeartbeat.id).where(WorkerHeartbeat.heartbeat_at >= stale_before)
        )
    ).first()
    checks.append(
        _check(
            "worker",
            online is not None,
            "测试 Worker 在线" if online is not None else "没有在线的测试 Worker，任务会一直排队",
        )
    )

    return {
        "checks": checks,
        "can_run": summarize_checks(checks),
        "environment_name": environment_name,
    }
