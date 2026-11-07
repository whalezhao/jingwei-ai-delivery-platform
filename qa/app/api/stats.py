"""统计聚合。全部走已落库的 SQL，不重读产物文件。

三个贯穿整个模块的口径决定：

1. **通过率一律「先求和再相除」**（SUM(passed)/SUM(total)），不是 AVG(单次通过率)。
2. **每个通过率都带样本量 n，并给出 Wilson 置信下界。** n=1 时 100% 的下界只有
   ~0.21，这才是诚实的读法。
3. **按系统统计。** 系统之间的模型、环境、用例都不同，混在一起看没有意义。
"""

import math
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from ..deps import CurrentPerson, DbSession, require_system_access
from ..models import AiCall, CaseResult, CaseStability, Feature, Requirement, Run, System, TestCase

router = APIRouter(prefix="/api/stats", tags=["stats"])

Z_95 = 1.959964
FLAKY_WINDOW_DAYS = 14


def wilson_lower_bound(passed: int, total: int, z: float = Z_95) -> float | None:
    """Wilson score 区间下界。

    比直接用点估计诚实：n 很小时下界会显著低于通过率，避免「跑了 1 次全过」
    被读成「100% 稳定」。
    """
    if total <= 0:
        return None
    p = passed / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


def _run_scope(
    *,
    system_id: int,
    date_from: date | None,
    date_to: date | None,
    include_infra: bool = False,
):
    # 只看跑完的：queued/running 的还没结果，failed/cancelled/timed_out 的没有指标
    conditions = [Run.system_id == system_id, Run.status.in_(("succeeded", "failed"))]
    if not include_infra:
        # 排除「非用例原因」的失败（模型额度用尽、服务不可达、网络不通）
        conditions.append(Run.failure_kind.is_(None))
    if date_from is not None:
        conditions.append(
            Run.started_at >= datetime.combine(date_from, datetime.min.time(), UTC)
        )
    if date_to is not None:
        conditions.append(
            Run.started_at
            < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), UTC)
        )
    return conditions


def _terminal_scope(
    *,
    system_id: int,
    date_from: date | None,
    date_to: date | None,
):
    """所有已结束的 run —— 用于结果分布，不做通过率口径过滤。"""
    conditions = [
        Run.system_id == system_id,
        Run.status.in_(("succeeded", "failed", "cancelled", "timed_out")),
    ]
    if date_from is not None:
        conditions.append(
            Run.started_at >= datetime.combine(date_from, datetime.min.time(), UTC)
        )
    if date_to is not None:
        conditions.append(
            Run.started_at
            < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), UTC)
        )
    return conditions


async def _metrics(db: DbSession, conditions) -> dict:
    row = (
        await db.execute(
            select(
                func.count(Run.id),
                func.coalesce(func.sum(Run.total), 0),
                func.coalesce(func.sum(Run.passed), 0),
                func.coalesce(func.sum(Run.failed), 0),
                func.coalesce(func.sum(Run.passed_after_retry), 0),
                func.coalesce(func.avg(Run.duration_ms), 0),
                func.coalesce(
                    func.percentile_cont(0.5).within_group(Run.duration_ms.asc()), 0
                ),
            ).where(*conditions)
        )
    ).one()

    run_count, total, passed, failed, passed_after_retry, avg_ms, median_ms = row
    total, passed, failed, passed_after_retry = (
        int(total),
        int(passed),
        int(failed),
        int(passed_after_retry),
    )
    return {
        "runs": int(run_count),
        # n 是用例数不是 run 数 —— 通过率的分母是「跑了多少个用例」
        "sample_size": total,
        "total": total,
        "passed": passed,
        "failed": failed,
        "passed_after_retry": passed_after_retry,
        "final_pass_rate": (passed / total) if total else None,
        "first_pass_rate": ((passed - passed_after_retry) / total) if total else None,
        # 重试率单列：重试会掩盖不稳定，不能混进通过率里
        "retry_recovery_rate": (passed_after_retry / total) if total else None,
        "pass_rate_lower_bound": wilson_lower_bound(passed, total),
        "sample_sufficient": total >= 3,
        "avg_duration_ms": int(avg_ms or 0),
        "median_duration_ms": int(median_ms or 0),
    }


@router.get("/overview")
async def overview(
    user: CurrentPerson,
    db: DbSession,
    system_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    await require_system_access(db, system_id, user)
    conditions = _run_scope(
        system_id=system_id, date_from=date_from, date_to=date_to
    )
    # 被排除掉的「非用例原因」失败也要报出来 —— 静默丢数据是另一种撒谎。
    excluded_infra = (
        await db.execute(
            select(func.count(Run.id)).where(
                *_run_scope(
                    system_id=system_id,
                    date_from=date_from,
                    date_to=date_to,
                    include_infra=True,
                ),
                Run.failure_kind.is_not(None),
            )
        )
    ).scalar_one()

    metrics = await _metrics(db, conditions)

    cost = (
        await db.execute(
            select(
                func.count(AiCall.id),
                func.coalesce(func.sum(AiCall.total_tokens), 0),
                func.coalesce(func.sum(AiCall.cost_ms), 0),
                # 上游 prompt cache 命中的 token 数；冷跑也会有值
                func.coalesce(func.sum(AiCall.cached_input), 0),
            )
            .join(Run, Run.id == AiCall.run_id)
            .where(*conditions)
        )
    ).one()

    # 环比：只在给了明确日期区间时算（默认「全部时间」没有可比的上一段）
    compare = None
    if date_from is not None and date_to is not None:
        span = date_to - date_from + timedelta(days=1)
        previous_from = date_from - span
        previous_to = date_from - timedelta(days=1)
        compare = await _metrics(
            db,
            _run_scope(
                system_id=system_id,
                date_from=previous_from,
                date_to=previous_to,
            ),
        )
        compare["date_from"] = previous_from.isoformat()
        compare["date_to"] = previous_to.isoformat()

    return {
        "scope": {"system_id": system_id},
        **metrics,
        "ai_calls": int(cost[0]),
        "ai_total_tokens": int(cost[1]),
        "ai_cost_ms": int(cost[2]),
        "ai_prompt_cache_tokens": int(cost[3]),
        "excluded_infra_runs": int(excluded_infra),
        "compare": compare,
    }


@router.get("/breakdown")
async def breakdown(
    user: CurrentPerson,
    db: DbSession,
    system_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """结果分布 / 失败原因 / 失败集中度 / 耗时分布。"""
    await require_system_access(db, system_id, user)
    conditions = _terminal_scope(
        system_id=system_id, date_from=date_from, date_to=date_to
    )

    labels = {
        "all_passed": "全部通过",
        "with_failures": "含失败",
        "exec_failed": "执行失败",
        "excluded": "非用例原因",
        "interrupted": "取消/超时",
    }
    buckets = {
        key: {"key": key, "label": label, "runs": 0, "cases": 0, "failed_cases": 0}
        for key, label in labels.items()
    }

    bucket_expr = case(
        (
            Run.status == "succeeded",
            case((Run.failed == 0, "all_passed"), else_="with_failures"),
        ),
        (Run.status.in_(("cancelled", "timed_out")), "interrupted"),
        (Run.failure_kind.is_not(None), "excluded"),
        else_="exec_failed",
    )
    rows = (
        await db.execute(
            select(
                bucket_expr.label("bucket"),
                func.count(Run.id),
                func.coalesce(func.sum(Run.total), 0),
                func.coalesce(func.sum(Run.failed), 0),
            )
            .where(*conditions)
            .group_by(bucket_expr)
        )
    ).all()

    for key, runs, total, failed in rows:
        if key not in buckets:
            continue
        buckets[key]["runs"] += int(runs)
        buckets[key]["cases"] += int(total)
        buckets[key]["failed_cases"] += int(failed)

    kind_rows = (
        await db.execute(
            select(Run.failure_kind, func.count(Run.id))
            .where(*conditions, Run.failure_kind.is_not(None))
            .group_by(Run.failure_kind)
        )
    ).all()
    kind_labels = {"ai_quota": "模型额度", "ai_service": "模型服务", "network": "网络"}
    failure_kinds = [
        {"kind": kind, "label": kind_labels.get(kind, kind), "runs": int(runs)}
        for kind, runs in sorted(kind_rows, key=lambda item: item[1], reverse=True)
    ]

    top_features = (
        await db.execute(
            select(Feature.id, Feature.name, System.name, func.count(CaseResult.id))
            .join(Run, Run.id == CaseResult.run_id)
            .join(TestCase, TestCase.id == CaseResult.test_case_id)
            .join(Feature, Feature.id == TestCase.feature_id)
            .join(System, System.id == Feature.system_id)
            .where(*conditions, CaseResult.status == "failed")
            .group_by(Feature.id, Feature.name, System.name)
            .order_by(func.count(CaseResult.id).desc())
            .limit(8)
        )
    ).all()

    duration_expr = case(
        (Run.duration_ms.is_(None), "unknown"),
        (Run.duration_ms < 30_000, "0-30s"),
        (Run.duration_ms < 60_000, "30-60s"),
        (Run.duration_ms < 120_000, "1-2m"),
        (Run.duration_ms < 300_000, "2-5m"),
        else_="5m+",
    )
    duration_rows = dict(
        (
            await db.execute(
                select(duration_expr, func.count(Run.id))
                .where(*conditions)
                .group_by(duration_expr)
            )
        ).all()
    )
    duration_order = ["0-30s", "30-60s", "1-2m", "2-5m", "5m+", "unknown"]
    duration_labels = {"unknown": "未知"}
    duration_buckets = [
        {
            "bucket": key,
            "label": duration_labels.get(key, key),
            "runs": int(duration_rows.get(key, 0)),
        }
        for key in duration_order
        if duration_rows.get(key)
    ]

    return {
        "status_breakdown": list(buckets.values()),
        "failure_kinds": failure_kinds,
        "top_failing_features": [
            {
                "feature_id": str(fid),
                "feature_name": fname,
                "system_name": sname,
                "failures": int(count),
            }
            for fid, fname, sname, count in top_features
        ],
        "duration_buckets": duration_buckets,
    }


@router.get("/trend")
async def trend(
    user: CurrentPerson,
    db: DbSession,
    system_id: int,
    days: int = Query(default=30, le=365),
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    await require_system_access(db, system_id, user)
    effective_from = date_from
    if effective_from is None:
        effective_from = datetime.now(UTC).date() - timedelta(days=days)
    conditions = _run_scope(
        system_id=system_id,
        date_from=effective_from,
        date_to=date_to,
    )
    day_expr = func.date_trunc("day", Run.started_at)
    rows = (
        await db.execute(
            select(
                day_expr.label("bucket"),
                func.count(Run.id),
                func.coalesce(func.sum(Run.total), 0),
                func.coalesce(func.sum(Run.passed), 0),
                func.coalesce(func.sum(Run.failed), 0),
            )
            .where(*conditions)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()

    points = []
    for bucket_value, run_count, total, passed, failed in rows:
        total, passed = int(total), int(passed)
        points.append(
            {
                "bucket": bucket_value.isoformat() if bucket_value else None,
                "runs": int(run_count),
                "total": total,
                "passed": passed,
                "failed": int(failed),
                "pass_rate": (passed / total) if total else None,
                "pass_rate_lower_bound": wilson_lower_bound(passed, total),
            }
        )
    return points


@router.get("/failures")
async def failures(
    user: CurrentPerson,
    db: DbSession,
    system_id: int,
    days: int = Query(default=30, le=365),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=20, le=100),
) -> list[dict]:
    await require_system_access(db, system_id, user)
    conditions = _run_scope(
        system_id=system_id,
        date_from=date_from or (datetime.now(UTC).date() - timedelta(days=days)),
        date_to=date_to,
    )
    rows = (
        await db.execute(
            select(
                TestCase.id,
                TestCase.name,
                Feature.name,
                func.count(CaseResult.id).label("failures"),
            )
            .join(Run, Run.id == CaseResult.run_id)
            .join(TestCase, TestCase.id == CaseResult.test_case_id)
            .join(Feature, Feature.id == TestCase.feature_id)
            .where(*conditions, CaseResult.status == "failed")
            .group_by(TestCase.id, TestCase.name, Feature.name)
            .order_by(func.count(CaseResult.id).desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "test_case_id": str(cid),
            "case_name": name,
            "feature_name": feature_name,
            "failures": int(count),
        }
        for cid, name, feature_name, count in rows
    ]


@router.get("/flaky")
async def flaky(
    user: CurrentPerson,
    db: DbSession,
    system_id: int,
    limit: int = Query(default=50, le=200),
) -> list[dict]:
    """不稳定用例榜单。

    AI 执行器同一个用例时过时不过，必须被显式看见，而不是被平均进一个通过率里。
    """
    await require_system_access(db, system_id, user)

    window_start = datetime.now(UTC) - timedelta(days=FLAKY_WINDOW_DAYS)
    rows = (
        await db.execute(
            select(
                TestCase.id,
                TestCase.name,
                Feature.name,
                CaseStability.runs_count,
                CaseStability.passed_count,
                CaseStability.failed_count,
                CaseStability.last_status,
                CaseStability.last_run_at,
            )
            .join(TestCase, TestCase.id == CaseStability.test_case_id)
            .join(Feature, Feature.id == TestCase.feature_id)
            .where(
                Feature.system_id == system_id,
                CaseStability.flaky.is_(True),
                CaseStability.last_run_at >= window_start,
            )
            .order_by(CaseStability.failed_count.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "test_case_id": str(cid),
            "case_name": name,
            "feature_name": feature_name,
            "runs": int(runs),
            "passed": int(passed),
            "failed": int(failed),
            "flakiness": (int(failed) / int(runs)) if runs else None,
            "last_status": last_status,
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
        }
        for cid, name, feature_name, runs, passed, failed, last_status, last_run_at in rows
    ]


@router.get("/cost")
async def cost(
    user: CurrentPerson,
    db: DbSession,
    system_id: int,
    days: int = Query(default=30, le=365),
) -> dict:
    await require_system_access(db, system_id, user)
    conditions = _run_scope(
        system_id=system_id,
        date_from=datetime.now(UTC).date() - timedelta(days=days),
        date_to=None,
    )
    # 表达式必须复用同一个对象，否则 PostgreSQL 认不出 GROUP BY 与 SELECT 是同一个
    day_expr = func.date_trunc("day", Run.started_at).label("day")
    by_day = (
        await db.execute(
            select(
                day_expr,
                func.coalesce(func.sum(AiCall.total_tokens), 0),
                func.coalesce(func.sum(AiCall.cost_ms), 0),
                func.count(AiCall.id),
            )
            .join(AiCall, AiCall.run_id == Run.id)
            .where(*conditions)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()

    by_model = (
        await db.execute(
            select(
                Run.model_name,
                func.count(func.distinct(Run.id)),
                func.coalesce(func.sum(AiCall.total_tokens), 0),
                func.coalesce(func.sum(AiCall.cost_ms), 0),
            )
            .join(AiCall, AiCall.run_id == Run.id)
            .where(*conditions)
            .group_by(Run.model_name)
            .order_by(func.coalesce(func.sum(AiCall.total_tokens), 0).desc())
        )
    ).all()

    return {
        "by_day": [
            {
                "day": day.isoformat() if day else None,
                "total_tokens": int(tokens),
                "cost_ms": int(cost_ms),
                "ai_calls": int(calls),
            }
            for day, tokens, cost_ms, calls in by_day
        ],
        "by_model": [
            {
                "model": model,
                "runs": int(runs),
                "total_tokens": int(tokens),
                "cost_ms": int(cost_ms),
            }
            for model, runs, tokens, cost_ms in by_model
        ],
    }


@router.get("/requirement-coverage")
async def requirement_coverage(
    user: CurrentPerson,
    db: DbSession,
    system_id: int,
) -> dict:
    """需求 → 功能 → 用例 → 最近结果的覆盖矩阵。"""
    await require_system_access(db, system_id, user)

    features = (
        await db.execute(
            select(Feature.id, Feature.name, Feature.requirement_id, System.name)
            .join(System, System.id == Feature.system_id)
            .where(Feature.system_id == system_id)
            .order_by(Feature.name)
        )
    ).all()
    feature_ids = [row[0] for row in features]
    cases = (
        (
            await db.execute(
                select(TestCase.id, TestCase.feature_id, TestCase.name, TestCase.priority)
                .where(
                    TestCase.feature_id.in_(feature_ids), TestCase.is_active.is_(True)
                )
                .order_by(TestCase.name)
            )
        ).all()
        if feature_ids
        else []
    )
    case_ids = [row[0] for row in cases]

    latest: dict[uuid.UUID, dict] = {}
    if case_ids:
        result_rows = (
            await db.execute(
                select(
                    CaseResult.test_case_id,
                    CaseResult.status,
                    Run.failure_kind,
                    Run.started_at,
                    Run.created_at,
                )
                .join(Run, Run.id == CaseResult.run_id)
                .where(CaseResult.test_case_id.in_(case_ids))
                .distinct(CaseResult.test_case_id)
                .order_by(
                    CaseResult.test_case_id,
                    Run.started_at.desc().nullslast(),
                    Run.created_at.desc(),
                    # 同一个 YAML 可能包含多个 midscene case；只要有失败，平台用例就显示失败
                    case((CaseResult.status == "success", 1), else_=0).asc(),
                    CaseResult.id.desc(),
                )
            )
        ).all()
        for case_id, status, failure_kind, started_at, created_at in result_rows:
            latest[case_id] = {
                "status": status,
                "failure_kind": failure_kind,
                "last_run_at": started_at or created_at,
            }

    from ..coverage import compute_requirement_coverage

    requirement_titles = dict(
        (
            await db.execute(
                select(Requirement.id, Requirement.title).where(
                    Requirement.system_id == system_id
                )
            )
        ).all()
    )

    rows: list[dict] = []
    unmapped = 0
    requirement_features: dict[int, dict] = {}
    for feature_id, feature_name, requirement_id, system_name in features:
        if requirement_id is None:
            unmapped += 1
            continue
        requirement_features.setdefault(
            requirement_id,
            {
                "feature_id": feature_id,
                "feature_name": feature_name,
                "system_name": system_name,
            },
        )
        for case_id, case_feature_id, case_name, priority in cases:
            if case_feature_id != feature_id:
                continue
            result = latest.get(case_id, {})
            rows.append(
                {
                    "requirement_id": requirement_id,
                    "feature_id": feature_id,
                    "feature_name": feature_name,
                    "system_name": system_name,
                    "case_id": case_id,
                    "case_name": case_name,
                    "priority": priority,
                    "status": result.get("status"),
                    "failure_kind": result.get("failure_kind"),
                    "last_run_at": result.get("last_run_at"),
                }
            )

    data = compute_requirement_coverage(rows)
    existing = {item["requirement_id"] for item in data["requirements"]}
    for requirement_id, meta in requirement_features.items():
        if requirement_id in existing:
            continue
        data["requirements"].append(
            {
                "requirement_id": requirement_id,
                "requirement_title": requirement_titles.get(requirement_id),
                "features": [meta["feature_name"]],
                "feature_ids": [str(meta["feature_id"])],
                "systems": [meta["system_name"]],
                "case_count": 0,
                "executed": 0,
                "passed": 0,
                "failed": 0,
                "infra": 0,
                "not_run": 0,
                "coverage_rate": 0,
                "pass_rate": None,
                "status": "gap",
                "last_run_at": None,
            }
        )
    for item in data["requirements"]:
        item["requirement_title"] = requirement_titles.get(item["requirement_id"])
    data["requirements"].sort(key=lambda item: item["requirement_id"])
    data["summary"]["total"] = len(data["requirements"])
    data["summary"]["gap"] = sum(
        1 for item in data["requirements"] if item["status"] == "gap"
    )
    data["summary"]["unmapped_features"] = unmapped
    return data