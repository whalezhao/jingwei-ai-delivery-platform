"""部署后自动触发需求测试：事件匹配、调用 QA 内部接口、汇总通知。

匹配口径（与 CD-AUTOTEST.md 第 5 节一致）：
- 只处理系统内 status=online 的需求
- 末次合入时间晚于部署时间 → 跳过（下次部署再测）
- 该需求在同一环境、合入之后已有部署触发的批次 → 跳过
- 其余逐个调 QA 发起，返回的 4xx 记成跳过原因，5xx/连不上记成错误
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import DeployEvent, DevGroup, DevTask, Requirement, System, utcnow
from .notify import merge_mentions, notify, project_owners, qa_owners

log = logging.getLogger(__name__)

# 跳过原因 → 汇总消息里的短标签
SKIP_LABEL = {
    "no_cases": "无用例",
    "running": "进行中",
    "already_tested": "已测过",
    "merged_after_deploy": "部署后合入",
    "no_model": "模型未配",
    "quota": "额度不足",
    "qa_rejected": "QA 拒绝",
}


async def last_merge_at(db: AsyncSession, requirement_id: int) -> datetime | None:
    """该需求全部任务的最后合入时间；旧数据 merged_at 为空时用 updated_at 兜底。"""
    ts = await db.scalar(
        select(func.max(DevTask.merged_at)).where(DevTask.requirement_id == requirement_id)
    )
    if ts is not None:
        return ts
    return await db.scalar(
        select(func.max(DevTask.updated_at)).where(DevTask.requirement_id == requirement_id)
    )


async def already_tested(
    db: AsyncSession, requirement_id: int, environment: str, since: datetime
) -> bool:
    """该需求在同一环境、since 之后是否已有部署触发的批次。

    QA 表与 vega 同库，但只读、不建 ORM 映射（create_all 会和 QA 迁移抢建表）。
    shortcut: SQLite 测试库没有 qa_* 表，查不到按“没测过”处理。
    """
    try:
        row = await db.execute(
            text(
                "SELECT 1 FROM qa_run_batches b JOIN qa_test_tasks t ON t.id = b.task_id "
                "WHERE t.requirement_id = :rid AND b.trigger = 'deployment' "
                "AND b.environment_name = :env AND b.created_at >= :since LIMIT 1"
            ),
            {"rid": requirement_id, "env": environment, "since": since},
        )
        return row.first() is not None
    except Exception:  # noqa: BLE001 测试库/表缺失
        return False


def _skip_reason(detail: str) -> str:
    if "用例" in detail or "功能" in detail:
        return "no_cases"
    if "进行中" in detail:
        return "running"
    if "模型" in detail:
        return "no_model"
    if "额度" in detail:
        return "quota"
    return "qa_rejected"


async def call_qa_run(requirement_id: int, environment: str, deploy_event_id: int) -> tuple[str, str | None]:
    """调 QA 内部接口发起测试。返回 (ok|skip|error, 原因)。"""
    s = get_settings()
    url = f"{s.qa_base_url.rstrip('/')}/internal/requirements/{requirement_id}/run"
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            r = await client.post(
                url,
                json={"environment_name": environment, "deploy_event_id": deploy_event_id},
                headers={"X-Vega-Service": s.qa_service_token},
            )
    except httpx.HTTPError as e:
        return "error", f"QA 不可达：{e}"
    if r.status_code == 201:
        return "ok", None
    detail = ""
    try:
        detail = str((r.json() or {}).get("detail") or "")
    except ValueError:
        pass
    if r.status_code == 409:
        return "skip", "already_tested" if "已发起" in detail else "running"
    if r.status_code == 400:
        return "skip", _skip_reason(detail)
    return "error", f"QA {r.status_code} {detail[:80]}"


async def _done(db: AsyncSession, ev: DeployEvent, result: dict) -> dict:
    ev.result = result
    ev.handled_at = utcnow()
    await db.commit()
    return result


async def _notify_summary(
    db: AsyncSession, group: DevGroup, system: System, ev: DeployEvent, result: dict
) -> None:
    triggered: list[str] = result["triggered"]
    skipped: dict[str, list[str]] = result["skipped"]
    bits = "、".join(triggered[:8]) + ("…" if len(triggered) > 8 else "")
    skip_text = "，".join(
        f"{SKIP_LABEL.get(k, k)} {len(v)}"
        for k, v in skipped.items()
        if k in ("no_cases", "running")
    )
    lines = [
        f"🚀 {ev.environment} 部署（{ev.sha[:8]}）已触发 {len(triggered)} 个需求测试",
        f"需求：{bits}",
    ]
    if skip_text:
        lines.append(f"跳过：{skip_text}")
    people = merge_mentions(*(await qa_owners(db, system.id)), *(await project_owners(db, system.id)))
    await notify(db, group, "\n".join(lines), reason="deploy_autotest", mention=people)


async def _notify_error(
    db: AsyncSession, group: DevGroup, system: System, ev: DeployEvent, errors: list[str]
) -> None:
    people = merge_mentions(*(await qa_owners(db, system.id)), *(await project_owners(db, system.id)))
    await notify(
        db,
        group,
        f"⚠️ {ev.environment} 部署（{ev.sha[:8]}）触发的测试未成功发起：\n" + "\n".join(errors[:5]),
        reason="deploy_autotest_failed",
        mention=people,
    )


async def handle_deploy_event(db: AsyncSession, event_id: int) -> dict:
    """匹配一次部署事件：触发待测需求，回填 result 并按矩阵推送通知。"""
    ev = await db.get(DeployEvent, event_id)
    if ev is None or ev.handled_at is not None:
        return {"skipped": "handled"}
    system = await db.get(System, ev.system_id)
    group = await db.get(DevGroup, system.group_id) if system else None
    if not system or not group:
        return await _done(db, ev, {"skipped": "system_missing"})

    mapping = {
        m.get("gitlab"): m.get("qa")
        for m in (system.deploy_env_map or [])
        if m.get("gitlab") and m.get("qa")
    }
    qa_env = mapping.get(ev.environment)
    if not qa_env:
        return await _done(db, ev, {"skipped": "env_not_mapped"})

    triggered: list[str] = []
    skipped: dict[str, list[str]] = {}
    errors: list[str] = []
    reqs = (
        await db.scalars(
            select(Requirement).where(
                Requirement.system_id == system.id, Requirement.status == "online"
            )
        )
    ).all()
    for req in reqs:
        merged_at = await last_merge_at(db, req.id)
        if merged_at and merged_at > ev.deployed_at:
            skipped.setdefault("merged_after_deploy", []).append(req.code)
            continue
        if await already_tested(db, req.id, qa_env, merged_at or ev.deployed_at):
            skipped.setdefault("already_tested", []).append(req.code)
            continue
        status, reason = await call_qa_run(req.id, qa_env, ev.id)
        if status == "ok":
            triggered.append(req.code)
        elif status == "skip":
            skipped.setdefault(reason or "qa_rejected", []).append(req.code)
        else:
            errors.append(f"{req.code}：{reason}")

    result = {
        "environment": ev.environment,
        "qa_env": qa_env,
        "triggered": triggered,
        "skipped": skipped,
        "errors": errors,
    }
    if triggered:
        await _notify_summary(db, group, system, ev, result)
    if errors:
        await _notify_error(db, group, system, ev, errors)
    if errors and not triggered and not skipped:
        # 全线失败（QA 挂了/密钥错）：不置 handled，留给 deploy_poll 重试
        ev.result = result
        await db.commit()
        return result
    return await _done(db, ev, result)