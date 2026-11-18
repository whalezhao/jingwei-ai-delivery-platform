"""测试执行 → 研发组 octo 群通知（开始 / 结果，@ 系统测试负责人）。

只通知真正需要的人：系统没配测试负责人、或研发组没配 octo 群号，整条跳过。
通知失败只留痕（notify_log.ok=false），绝不影响执行结果。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import octo
from .config import get_settings
from .models import (
    DevGroup,
    NotifyLog,
    Person,
    Requirement,
    Run,
    RunBatch,
    System,
    SystemQaOwner,
    TestTask,
    VegaAppSetting,
)
from .runner.parsers import read_case_evidence

log = logging.getLogger(__name__)

_MAX_REASONS = 3
_REASON_LEN = 160
_TEXT_LIMIT = 2000

_TRIGGER = {
    "manual": "手动",
    "regression": "回归",
    "rerun": "重跑失败",
    "requirement": "需求测试",
    "deployment": "部署触发",
}
_FAILURE_KIND = {
    "ai_quota": "模型调用额度用尽",
    "ai_service": "模型服务返回错误",
    "network": "网络不可达",
}


def _decrypt(value: str | None) -> str | None:
    """Vega 的加密字段（Fernet(sha256(VEGA_SECRET_KEY))），与 api/vega/crypto.py 一致。"""
    if not value:
        return value
    key = get_settings().vega_secret_key
    if not key:
        return None
    try:
        fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))
        return fernet.decrypt(value.encode()).decode()
    except Exception:  # noqa: BLE001
        log.warning("octo token 解密失败（VEGA_SECRET_KEY 换过？）")
        return None


async def _group_token(db: AsyncSession, group: DevGroup) -> str | None:
    """组 bot 优先，其次集成中心录入的全局 token，都没有则落 env。"""
    token = _decrypt(group.octo_bot_token_enc)
    if token:
        return token
    row = await db.get(VegaAppSetting, "octo_bot_token")
    return _decrypt(row.value) if row else None


def _dur(ms: int | None) -> str:
    if not ms:
        return "—"
    seconds = round(ms / 1000)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _counts(runs: list[Run]) -> tuple[int, int, int, int, int]:
    """(总用例, 通过, 失败, 未执行, 最长耗时 ms)。分片是并行跑的，耗时取最慢一片。"""
    total = sum(r.total or 0 for r in runs)
    passed = sum(r.passed or 0 for r in runs)
    failed = sum(r.failed or 0 for r in runs)
    not_run = sum(r.not_run or 0 for r in runs)
    duration = max((r.duration_ms or 0 for r in runs), default=0)
    return total, passed, failed, not_run, duration


def _trim(text: str) -> str:
    return text if len(text) <= _TEXT_LIMIT else text[: _TEXT_LIMIT - 1] + "…"


def _failure_reasons(run: Run, limit: int) -> list[str]:
    """失败用例的 AI 判定原文：失败步骤的 error.message，取最后一条。"""
    out: list[str] = []
    if not run.result_dir:
        return out
    for item in read_case_evidence(Path(run.result_dir), limit=limit):
        reason = next(
            (s["reason"] for s in reversed(item.get("steps") or []) if s.get("reason")),
            None,
        )
        if reason:
            out.append(f"· {item.get('case') or '用例'}：{reason[: _REASON_LEN]}")
    return out


async def _targets(
    db: AsyncSession, batch: RunBatch
) -> dict[int, tuple[DevGroup, list[tuple[System, list[Run]]], list[Person]]]:
    """按研发组聚合：组、组内各系统及其 run、要 @ 的测试负责人。"""
    runs = list(
        await db.scalars(select(Run).where(Run.batch_id == batch.id).order_by(Run.created_at))
    )
    if not runs:
        return {}
    system_ids = {r.system_id for r in runs}
    systems = {
        s.id: s
        for s in (await db.scalars(select(System).where(System.id.in_(system_ids))))
    }
    owners: dict[int, list[Person]] = {}
    rows = (
        await db.execute(
            select(SystemQaOwner.system_id, Person)
            .join(Person, Person.id == SystemQaOwner.person_id)
            .where(SystemQaOwner.system_id.in_(system_ids))
            .order_by(SystemQaOwner.id)
        )
    ).all()
    for system_id, person in rows:
        owners.setdefault(system_id, []).append(person)

    # 没有测试负责人的系统不进通知；一个组里连一个这样的系统都没有就整组跳过
    # 部署触发的批次：产品（提出人）+ 开发（认领人）也要知道结果，与测试负责人三方齐发
    extra: list[Person] = []
    if batch.trigger == "deployment" and batch.task_id:
        task = await db.get(TestTask, batch.task_id)
        req = await db.get(Requirement, task.requirement_id) if task and task.requirement_id else None
        if req:
            for pid in (req.proposer_id, req.claimant_id):
                person = await db.get(Person, pid) if pid else None
                if person:
                    extra.append(person)

    # 普通批次：没有测试负责人的系统不进通知；部署批次只要三方里有人在就发
    per_group: dict[int, list[tuple[System, list[Run]]]] = {}
    mentions: dict[int, list[Person]] = {}
    for run in runs:
        system = systems.get(run.system_id)
        if system is None:
            continue
        if batch.trigger != "deployment" and system.id not in owners:
            continue
        per_group.setdefault(system.group_id, [])
        bucket = per_group[system.group_id]
        existing = next((item for item in bucket if item[0].id == system.id), None)
        if existing:
            existing[1].append(run)
        else:
            bucket.append((system, [run]))
        mentions.setdefault(system.group_id, []).extend(owners.get(system.id, []))
        mentions.setdefault(system.group_id, []).extend(extra)

    out = {}
    for group_id, bucket in per_group.items():
        group = await db.get(DevGroup, group_id)
        if group is None or not group.octo_group_no:
            continue
        # 同一个人管多个系统时只 @ 一次
        seen: set[int] = set()
        people = []
        for person in mentions.get(group_id, []):
            if person.id not in seen:
                seen.add(person.id)
                people.append(person)
        out[group_id] = (group, bucket, people)
    return out


def build_started_text(batch: RunBatch, bucket: list[tuple[System, list[Run]]]) -> str:
    names = "、".join(system.name for system, _ in bucket)
    line = f"环境：{batch.environment_name or '—'} · 触发：{_TRIGGER.get(batch.trigger, batch.trigger)}"
    return _trim(f"🧪 测试开始 · {batch.name}\n系统：{names}\n{line}")


def build_finished_text(
    batch: RunBatch, bucket: list[tuple[System, list[Run]]]
) -> tuple[str, bool]:
    """返回 (正文, 是否有失败)。失败时正文带 AI 判定的失败原因。"""
    lines: list[str] = []
    reasons: list[str] = []
    failed_any = False
    for system, runs in bucket:
        total, passed, failed, not_run, duration = _counts(runs)
        failed_any = failed_any or failed > 0 or any(
            r.status in {"failed", "timed_out"} for r in runs
        )
        bits = [f"{passed}/{total} 通过"]
        if failed:
            bits.append(f"{failed} 失败")
        if not_run:
            bits.append(f"{not_run} 未执行")
        lines.append(f"{system.name}：{'，'.join(bits)} · {_dur(duration)}")
        for run in runs:
            if run.failure_kind:
                detail = _FAILURE_KIND.get(run.failure_kind, run.failure_kind)
                reasons.append(f"· {system.name}：{detail}")
            elif (run.error_message or "").strip() and not (run.failed or 0):
                line = run.error_message.strip().splitlines()[0]
                reasons.append(f"· {system.name}：{line[:_REASON_LEN]}")
            else:
                reasons.extend(_failure_reasons(run, _MAX_REASONS - len(reasons)))
            if len(reasons) >= _MAX_REASONS:
                break
        if len(reasons) >= _MAX_REASONS:
            break

    head = "❌ 测试失败" if failed_any else "✅ 测试通过"
    body = f"{head} · {batch.name}\n" + "\n".join(lines)
    if failed_any and reasons:
        body += "\n失败原因：\n" + "\n".join(reasons[:_MAX_REASONS])
    return _trim(body), failed_any


async def _send(
    db: AsyncSession,
    group: DevGroup,
    people: list[Person],
    text: str,
    reason: str,
) -> None:
    uids = [p.octo_uid for p in people if p.octo_uid]
    token = await _group_token(db, group)
    ok = await asyncio.to_thread(octo.send_text, group.octo_group_no, text, uids, token)
    db.add(
        NotifyLog(
            group_id=group.id,
            octo_chat=group.octo_group_no,
            mentioned_uids=uids,
            content=text,
            reason=reason,
            ok=ok,
        )
    )
    await db.commit()


async def notify_batch_started(db: AsyncSession, batch: RunBatch) -> None:
    if batch.trigger == "deployment":
        # 一次部署会同时起多个批次，逐条“测试开始”会刷屏；由 vega 的一条汇总消息替代
        return
    for group, bucket, people in (await _targets(db, batch)).values():
        await _send(db, group, people, build_started_text(batch, bucket), "qa_run_started")


async def notify_batch_finished(db: AsyncSession, batch: RunBatch) -> None:
    for group, bucket, people in (await _targets(db, batch)).values():
        text, _ = build_finished_text(batch, bucket)
        await _send(db, group, people, text, "qa_run_finished")