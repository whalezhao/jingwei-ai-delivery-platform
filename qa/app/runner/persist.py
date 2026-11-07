"""把 midscene 产物投影进数据库。

产物文件是权威，DB 里的行是可重建的投影 —— 所以这里的函数都必须是幂等的，
reparse 时先删后建能得到一致结果。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AiCall as AiCallRow
from ..models import CaseResult, CaseStability, Run
from .parsers import AiCall, SummaryData
from .staging import case_id_from_source_path

FLAKY_WINDOW_DAYS = 14


async def apply_summary(db: AsyncSession, run: Run, data: SummaryData) -> None:
    run.midscene_run_id = data.midscene_run_id
    run.midscene_status = data.midscene_status
    run.exit_code = data.exit_code
    run.duration_ms = data.duration_ms
    run.total = data.total
    run.passed = data.passed
    run.failed = data.failed
    run.not_run = data.not_run
    run.passed_after_retry = data.passed_after_retry
    run.final_pass_rate = data.final_pass_rate
    run.first_pass_rate = data.first_pass_rate
    run.filtered = data.filtered
    run.collection_errors = data.collection_errors
    run.project_failures = data.project_failures

    # caseId/documentId 是内容哈希，不能当身份；暂存文件名才是我们的用例 UUID
    for outcome in data.cases:
        test_case_id = (
            case_id_from_source_path(outcome.source_path) if outcome.source_path else None
        )
        stmt = insert(CaseResult).values(
            run_id=run.id,
            test_case_id=test_case_id,
            midscene_document_id=outcome.document_id,
            midscene_case_id=outcome.case_id,
            source_path=outcome.source_path,
            case_index=outcome.case_index,
            name=outcome.name,
            status=outcome.status,
            attempt_count=outcome.attempt_count,
            passed_after_retry=outcome.passed_after_retry,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_case_results_identity",
                set_={
                    "test_case_id": test_case_id,
                    "name": outcome.name,
                    "status": outcome.status,
                    "attempt_count": outcome.attempt_count,
                    "passed_after_retry": outcome.passed_after_retry,
                },
            )
        )


async def apply_stats(db: AsyncSession, run: Run, calls: list[AiCall]) -> None:
    for call in calls:
        db.add(
            AiCallRow(
                run_id=run.id,
                occurred_at=call.occurred_at,
                model=call.model,
                prompt_tokens=call.prompt_tokens,
                completion_tokens=call.completion_tokens,
                total_tokens=call.total_tokens,
                cached_input=call.cached_input,
                cost_ms=call.cost_ms,
                raw=call.raw,
            )
        )
    if calls:
        # 模型名落到 run 上，趋势图才能标注「这里换了模型」
        models = {c.model for c in calls if c.model}
        run.model_name = ",".join(sorted(models)) if models else None


async def clear_run_projection(db: AsyncSession, run_id: uuid.UUID) -> None:
    """reparse 前清掉旧投影。AI 调用是逐行追加的，必须先删否则会翻倍。"""
    await db.execute(delete(AiCallRow).where(AiCallRow.run_id == run_id))
    await db.execute(delete(CaseResult).where(CaseResult.run_id == run_id))


async def refresh_stability(db: AsyncSession, case_ids: list[uuid.UUID]) -> None:
    """按用例重算稳定性。flaky = 窗口内既过又不过。

    这是这套系统对 AI 执行器随机性的核心应对：不稳的用例要被显式看见，而不是被
    平均进一个通过率里。
    """
    if not case_ids:
        return
    window_start = datetime.now(UTC) - timedelta(days=FLAKY_WINDOW_DAYS)

    stmt = (
        select(
            CaseResult.test_case_id,
            func.count(CaseResult.id),
            func.count(CaseResult.id).filter(CaseResult.status == "success"),
            func.count(CaseResult.id).filter(CaseResult.status == "failed"),
            func.max(Run.started_at),
        )
        .join(Run, Run.id == CaseResult.run_id)
        .where(
            CaseResult.test_case_id.in_(case_ids),
            Run.status.in_(("succeeded", "failed")),
            Run.started_at >= window_start,
        )
        .group_by(CaseResult.test_case_id)
    )

    for case_id, runs_count, passed, failed, last_run_at in (await db.execute(stmt)).all():
        last_status = (
            await db.execute(
                select(CaseResult.status)
                .join(Run, Run.id == CaseResult.run_id)
                .where(CaseResult.test_case_id == case_id, Run.started_at.is_not(None))
                .order_by(Run.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        values = {
            "test_case_id": case_id,
            "runs_count": runs_count,
            "passed_count": passed,
            "failed_count": failed,
            "flaky": passed > 0 and failed > 0,
            "last_status": last_status,
            "last_run_at": last_run_at,
        }
        upsert = insert(CaseStability).values(**values)
        await db.execute(
            upsert.on_conflict_do_update(
                index_elements=[CaseStability.test_case_id],
                set_={k: v for k, v in values.items() if k != "test_case_id"},
            )
        )
