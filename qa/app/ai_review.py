"""AI 评估：执行结果评估。

三条原则：
1. **只用系统级模型配置**（和生成用例同一条规则），没有全局兜底；系统没配模型就不评估。
2. **结论只做建议，不阻断任何操作** —— 执行照常可以继续。
3. 结论一律落 `qa_ai_checks`，事后能追溯「当时 AI 说了什么」。

执行结果评估不在 worker 里跑：worker 是串行跑 midscene 的，一次执行几十分钟，
把 AI 调用塞进去会拖住下一个任务。所以走「worker 入队 → API 进程轮询协程处理」。
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_case import call_model
from .db import SessionLocal
from .modelconfig import missing_fields
from .models import (
    AiCheck,
    CaseResult,
    CaseStability,
    SystemModelConfig,
    Run,
    RunBatch,
    TestTask,
)
from .quota import ensure_quota, record_usage
from .runner import parsers

log = logging.getLogger("ai_review")

POLL_SECONDS = 15
_MODEL_TIMEOUT = 120.0
_MAX_FAILED_CASES = 12

# 执行评估多一个「逐条诊断」：每条失败到底该修用例、修产品还是重跑
_ASSESS_SCHEMA = """只输出一个 JSON 对象，不要代码块、不要多余解释：
{"verdict":"healthy|risky|needs_attention","headline":"一句话（30 字内）","reasons":["最多 3 条，每条讲清因果"],"advice":["最多 2 条可执行建议"],"diagnoses":[{"case":"失败用例名","cause":"一句话根因，必须引用失败步骤里看到的现象","owner":"case|product|env","action":"具体建议"}]}
读者是测试经理：不要出现工具名、字段名、英文缩写。
diagnoses 最多 5 条，只诊断事实依据足够的失败用例 —— 依据不足就不写，不要猜。
owner 三选一：case=用例本身写错了（断言/步骤/数据不对），product=产品可能有缺陷，env=环境或测试数据问题。"""


async def _config_for(db: AsyncSession, system_id: int | None) -> SystemModelConfig | None:
    if system_id is None:
        return None
    config = (
        await db.execute(
            select(SystemModelConfig).where(SystemModelConfig.system_id == system_id)
        )
    ).scalar_one_or_none()
    if config is None or missing_fields(config):
        return None
    return config


async def _config_for_batch(db: AsyncSession, batch: RunBatch) -> SystemModelConfig | None:
    """批次对应的系统：任务的系统优先，跨系统任务取第一个 run 的系统。"""
    task = await db.get(TestTask, batch.task_id) if batch.task_id else None
    if task is not None:
        config = await _config_for(db, task.system_id)
        if config is not None:
            return config
    run = (
        await db.execute(
            select(Run).where(Run.batch_id == batch.id, Run.system_id.is_not(None)).limit(1)
        )
    ).scalar_one_or_none()
    return await _config_for(db, run.system_id if run else None)


def _extract_json(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _ask(
    config: SystemModelConfig, system: str, user: str, *, json_mode: bool
) -> tuple[str, dict[str, Any] | None, dict]:
    reply, usage = await call_model(
        config,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        timeout=_MODEL_TIMEOUT,
    )
    return reply, (_extract_json(reply) if json_mode else None), usage


# ------------------------------------------------------------------ 执行结果评估


async def _batch_facts(db: AsyncSession, batch: RunBatch) -> dict[str, Any]:
    runs = list((await db.execute(select(Run).where(Run.batch_id == batch.id))).scalars())
    task = await db.get(TestTask, batch.task_id) if batch.task_id else None

    run_items = []
    failed_cases: list[dict[str, Any]] = []
    evidence: dict[str, list[dict]] = {}
    for run in runs:
        cases = (
            await db.execute(
                select(
                    CaseResult.test_case_id,
                    CaseResult.name,
                    CaseResult.status,
                    CaseResult.attempt_count,
                )
                .where(CaseResult.run_id == run.id)
                .order_by(CaseResult.case_index)
            )
        ).all()
        # 失败用例的 flaky 标记一次查完：逐个用例回表是典型的 N+1（最多 12 次）
        failed_rows = [
            row
            for row in cases
            if str(row[2]).lower() not in {"success", "passed"}
        ][: max(0, _MAX_FAILED_CASES - len(failed_cases))]
        flaky_ids = set(
            (
                await db.execute(
                    select(CaseStability.test_case_id).where(
                        CaseStability.test_case_id.in_(
                            [row[0] for row in failed_rows if row[0]]
                        ),
                        CaseStability.flaky.is_(True),
                    )
                )
            ).scalars()
        )
        for case_id, name, status, attempts in failed_rows:
            failed_cases.append(
                {
                    "case": name,
                    "status": status,
                    "attempts": attempts,
                    "known_flaky": case_id in flaky_ids,
                }
            )
        # 从产物里读失败步骤的原始理由 —— 只有计数的话，AI 只能猜，等于没有依据
        for item in (
            parsers.read_case_evidence(Path(run.result_dir)) if run.result_dir else []
        ):
            if len(evidence) >= _MAX_FAILED_CASES:
                break
            key = item.get("case") or "未命名用例"
            evidence.setdefault(key, []).extend(item.get("steps") or [])
        run_items.append(
            {
                "status": run.status,
                "total": run.total,
                "passed": run.passed,
                "failed": run.failed,
                "failure_kind": run.failure_kind,
                "duration_ms": run.duration_ms,
                "model": run.model_name,
                "error": (run.error_message or "")[:400] or None,
            }
        )

    for row in failed_cases:
        if steps := evidence.pop(row["case"] or "未命名用例", None):
            row["steps"] = steps
    # 用例名对不上（例如库里的记录被重建过）时也别丢依据，单独附上
    failed_cases.extend({"case": name, "steps": steps} for name, steps in evidence.items())

    return {
        "task": task.name if task else None,
        "task_type": task.type if task else None,
        "runs": run_items,
        "failed_cases": failed_cases,
        "trigger": batch.trigger,
        "name": batch.name,
    }


async def assess_batch(db: AsyncSession, batch: RunBatch) -> tuple[dict[str, Any], str]:
    """返回 (结论, 模型名)。"""
    config = await _config_for_batch(db, batch)
    if config is None:
        raise RuntimeError("该批次所属系统未配置模型，跳过 AI 评估")
    # 额度用尽就不调模型；run_check 会把这条 RuntimeError 记成 check 失败原因
    await ensure_quota(db, config.system_id, config)
    facts = await _batch_facts(db, batch)
    system = (
        "你是资深测试负责人，负责判断一次自动化回归结果的可信度。\n"
        "判定口径：\n"
        "- healthy：通过率高，失败集中在明确的功能问题，没有大面积重试/flaky 迹象；\n"
        "- risky：失败不少但能解释（例如集中在一个功能），或存在明显 flaky（重试后才过）；\n"
        "- needs_attention：失败面广，或失败疑似环境/模型额度问题，或大量 flaky，需要人工排查。\n"
        "事实依据：failed_cases[].steps 是失败用例实际执行过的步骤 —— action 是当时的"
        "指令，result 是执行结果，reason 是失败那一步给出的理由。reasons 里每条结论都必须"
        "引用这些具体事实（哪一步、看到什么、为什么失败），不要只复述通过率；"
        "事实不足以支撑的推测不要写，信息不够就直说信息不足。\n"
        "注意：AI 执行本身时过时不过属于常态，单次失败不等价于缺陷。\n" + _ASSESS_SCHEMA
    )
    reply, parsed, usage = await _ask(
        config, system, json.dumps(facts, ensure_ascii=False), json_mode=True
    )
    await record_usage(db, config.system_id, "assess", usage)
    if parsed is None:
        parsed = {"verdict": "needs_attention", "headline": reply[:120], "reasons": [], "advice": []}
    return parsed, (config.model_name or "-")


# ------------------------------------------------------------------ 队列与轮询


async def enqueue_batch_check(db: AsyncSession, batch_id: uuid.UUID) -> None:
    """worker 在批次收尾时调用：同一批次只入队一次。

    全绿的批次直接跳过 —— 每次评估都是一次真实的模型调用，而全部通过的批次
    没有需要诊断的东西。
    """
    failed = (
        await db.execute(
            select(func.coalesce(func.sum(Run.failed), 0)).where(
                Run.batch_id == batch_id, Run.failure_kind.is_(None)
            )
        )
    ).scalar_one()
    if int(failed) == 0:
        return

    exists = (
        await db.execute(
            select(AiCheck.id).where(
                AiCheck.entity_type == "batch", AiCheck.entity_id == batch_id
            )
        )
    ).first()
    if exists is None:
        db.add(AiCheck(entity_type="batch", entity_id=batch_id, status="pending"))


def _detail(result: dict[str, Any]) -> dict[str, Any]:
    """结论落库的统一形状。diagnoses 来自模型，形状不可全信 —— 只收 dict、最多 5 条。"""
    diagnoses = [item for item in (result.get("diagnoses") or []) if isinstance(item, dict)]
    return {
        "reasons": result.get("reasons") or [],
        "advice": result.get("advice") or [],
        "diagnoses": diagnoses[:5],
    }


async def run_check(db: AsyncSession, check: AiCheck) -> None:
    try:
        if check.entity_type != "batch":
            raise RuntimeError(f"不支持的检查类型：{check.entity_type}")
        batch = await db.get(RunBatch, check.entity_id)
        if batch is None:
            raise RuntimeError("批次已不存在")
        result, model_name = await assess_batch(db, batch)
        check.verdict = str(result.get("verdict") or "unknown")
        check.summary = str(result.get("headline") or "")
        check.detail = _detail(result)
        check.model_name = model_name
        check.status = "done"
        check.error = None
    except RuntimeError as exc:
        # 我们自己抛的、要原样给用户看的（例如「该项目未配置模型」）
        check.status = "failed"
        check.error = str(exc)[:500]
        log.warning("ai check %s 跳过：%s", check.id, exc)
    except Exception:
        # 模型/网络/数据库的意外错误：完整堆栈进日志，界面上只给一句人话，
        # 免得把服务端细节漏给普通成员
        check.status = "failed"
        check.error = "AI 调用失败，详见服务端日志"
        log.exception("ai check %s 失败", check.id)
    finally:
        check.finished_at = datetime.now(UTC)


async def assess_loop() -> None:
    """API 进程里的常驻协程：认领 pending 的检查，调模型，写回结论。"""
    while True:
        try:
            async with SessionLocal() as db:
                check = (
                    await db.execute(
                        select(AiCheck)
                        .where(AiCheck.status == "pending")
                        .order_by(AiCheck.created_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if check is not None:
                    await run_check(db, check)
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("AI 评估轮询出错")
        await asyncio.sleep(POLL_SECONDS)


async def latest_check(
    db: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> AiCheck | None:
    return (
        await db.execute(
            select(AiCheck)
            .where(AiCheck.entity_type == entity_type, AiCheck.entity_id == entity_id)
            .order_by(AiCheck.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
