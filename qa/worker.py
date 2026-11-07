"""执行队列的消费者。

刻意做成**独立进程**而不是 FastAPI 的 startup 后台任务：`uvicorn --reload` 在代码
变更时会重启进程，正在跑的一次执行（可能几十分钟）会被无声杀掉。

并发模型：
- 每个 worker 进程有多个槽位，一个槽位跑一个 run（一个系统 / 一个浏览器）；
- 全局并发上限 `max_concurrent_runs` 由数据库里的 AppSetting 控制，所有 worker 共享；
- 每个 worker 通过 `worker_heartbeats` 心跳证明自己活着，恢复逻辑只清理失联 worker 的 run，
  不会因为新 worker 启动就误杀其他 worker 正在跑的任务。

    uv run python worker.py
"""

import asyncio
import json
import logging
import os
import signal
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select, update

from app.config import get_settings
from app.db import SessionLocal, engine
from app.environments import ensure_default_environment, load_environment_variables
from app.modelconfig import missing_fields
from app.models import (
    AppSetting,
    CaseResult,
    Feature,
    System,
    SystemEnvironment,
    SystemModelConfig,
    Run,
    RunBatch,
    TaskCase,
    TestCase,
    TestTask,
    WorkerHeartbeat,
)
from app.runner import execute as exec_mod
from app.runner import parsers, persist, staging
from app.taskstatus import ACTIVE_RUN_STATUSES, refresh_task_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

IDLE_POLL_SECONDS = 2.0
# 每个 worker 进程最多开几个槽位（实际能领到多少活还受全局 max_concurrent_runs 限制）
WORKER_CONCURRENCY = max(1, int(os.environ.get("WORKER_CONCURRENCY", "4")))
WORKER_ID = uuid.uuid4().hex
HEARTBEAT_SECONDS = 10
# 超过这个时间没心跳就认为 worker 失联；留出 3 个心跳周期的余量
WORKER_STALE_SECONDS = max(30, int(os.environ.get("WORKER_STALE_SECONDS", "90")))
# 认领闸门的 advisory lock key：让「数活跃 run + 认领」在一个锁里完成
CLAIM_LOCK_KEY = 0x4153544551  # "ASTEQ"
_stopping = asyncio.Event()


# 系统级配置 → midscene 环境变量的字段映射
SYSTEM_MODEL_ENV_KEYS = {
    "model_name": "MIDSCENE_MODEL_NAME",
    "model_family": "MIDSCENE_MODEL_FAMILY",
    "base_url": "MIDSCENE_MODEL_BASE_URL",
    "api_key": "MIDSCENE_MODEL_API_KEY",
    "init_config_json": "MIDSCENE_MODEL_INIT_CONFIG_JSON",
}


def system_model_overrides(config: SystemModelConfig | None) -> dict[str, str]:
    """把系统级模型配置转成环境变量。

    逐字段覆盖：只填了模型名的系统会继续沿用全局的 api_key，全部留空就等于没配。
    """
    if config is None:
        return {}
    overrides: dict[str, str] = {}
    for field, env_key in SYSTEM_MODEL_ENV_KEYS.items():
        value = getattr(config, field, None)
        if value:
            overrides[env_key] = value
    # 布尔值要单独处理：False 在这条链路上是有意义的取值，不能被 `if value` 当成空跳过
    if config.reasoning_enabled is not None:
        overrides["MIDSCENE_MODEL_REASONING_ENABLED"] = (
            "true" if config.reasoning_enabled else "false"
        )
    return overrides


def _build_env(
    runner_env: dict[str, str],
    staged: staging.StagedRun,
    runner_root: Path,
    system_overrides: dict[str, str] | None = None,
    secret_env: dict[str, str] | None = None,
    system_id: int | None = None,
) -> dict[str, str]:
    """组装子进程环境。

    顺序很重要，后面的赢：
        父进程环境（node 需要 PATH/HOME）
      → runner/.env（全局默认）
      → 系统级模型配置（本次执行所属系统的覆盖）
      → 环境密钥（YAML 里用 ${{KEY}} 引用）
      → 本次执行的固定值（RUN_DIR / HEADLESS）

    必须显式注入：实测 `midscene-test` 根本不加载 .env，不注入连模型都调不通。
    """
    env = dict(os.environ)
    env.update(runner_env)
    # 执行器/浏览器不需要这些；别把数据库密码和平台签名密钥带进子进程
    for secret_key in (
        "DATABASE_URL",
        "SECRET_KEY",
        "ENVIRONMENT_SECRET_KEY",
        "POSTGRES_PASSWORD",
    ):
        env.pop(secret_key, None)
    env.update(system_overrides or {})
    env.update(secret_env or {})
    # 每个 run 一个独立的 MIDSCENE_RUN_DIR —— log 因此天然只含本次执行，
    # token 统计不需要记 offset、也不用按时间窗猜归属
    env["MIDSCENE_RUN_DIR"] = staging.runner_relative_run_dir(staged, runner_root)
    # 缓存目录不能跟着 MIDSCENE_RUN_DIR 走，否则每次执行都是冷缓存
    env["QA_CACHE_DIR"] = str(staging.shared_cache_dir(runner_root, system_id))
    env.setdefault("HEADLESS", "true")

    # 复刻参考实现的做法：每次执行换一个上游会话头，避免所有 run 挤在同一个会话里
    raw = env.get("MIDSCENE_MODEL_INIT_CONFIG_JSON")
    if raw:
        try:
            config = json.loads(raw)
            headers = config.setdefault("defaultHeaders", {})
            headers["x-opencode-session"] = f"ses_{uuid.uuid4().hex[:13]}"
            env["MIDSCENE_MODEL_INIT_CONFIG_JSON"] = json.dumps(config, ensure_ascii=False)
        except (json.JSONDecodeError, AttributeError):
            # 用户手写的 init config 不是合法 JSON，保持原样别搞坏
            log.warning("MIDSCENE_MODEL_INIT_CONFIG_JSON 不是合法 JSON，按原样传递")
    return env


OCCUPYING_STATUSES = ("preparing", "running", "parsing")


async def _global_limit(db) -> int:
    row = await db.get(AppSetting, 1)
    value = row.max_concurrent_runs if row is not None else WORKER_CONCURRENCY
    return max(1, min(32, int(value)))


async def claim_next(db) -> Run | None:
    """取下一个排队中的 run 并占位。

    全局并发闸门用 PostgreSQL advisory lock 串行化「数活跃 run + 认领」，
    否则多个 worker 可能同时读到「还没满」而一起超领。锁随事务提交/回滚释放。
    """
    await db.execute(select(func.pg_advisory_xact_lock(CLAIM_LOCK_KEY)))
    limit = await _global_limit(db)
    active = (
        await db.execute(
            select(func.count(Run.id)).where(Run.status.in_(OCCUPYING_STATUSES))
        )
    ).scalar_one()
    if active >= limit:
        return None

    stmt = (
        select(Run)
        .where(Run.status == "queued")
        .order_by(Run.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        return None
    run.status = "preparing"
    run.started_at = datetime.now(UTC)
    run.owner_worker_id = WORKER_ID
    # 任务状态是 run 的聚合值：认领时就要跟着变成「执行中」，否则列表里会一直显示
    # 「排队中」直到这次执行彻底结束
    await refresh_task_status(db, run.task_id)
    await db.commit()
    return run


async def collect_cases(db, run: Run) -> list[tuple[uuid.UUID, str]]:
    """这个 run 实际要执行哪些用例。

    task_cases 是任务创建时的快照（权威），再按 run 所属系统过滤 —— YAML 里的
    gotoUrl/BASE_URL 是系统级的，一个 run 只跑一个系统的用例。
    """
    if run.task_id is None:
        return []
    stmt = (
        select(TestCase.id, TestCase.yaml_content)
        .join(TaskCase, TaskCase.case_id == TestCase.id)
        .join(Feature, Feature.id == TestCase.feature_id)
        .where(TaskCase.task_id == run.task_id, TestCase.is_active.is_(True))
        .order_by(TaskCase.position, TestCase.name)
    )
    if run.system_id is not None:
        stmt = stmt.where(Feature.system_id == run.system_id)
    if run.case_ids is not None:
        # 分片 run 只跑它分到的那批用例；空列表表示这一片没有用例，直接跳过
        case_ids: list[uuid.UUID] = []
        for raw in run.case_ids:
            try:
                case_ids.append(uuid.UUID(str(raw)))
            except (TypeError, ValueError):
                continue
        if not case_ids:
            return []
        stmt = stmt.where(TestCase.id.in_(case_ids))
    return [(row[0], row[1]) for row in (await db.execute(stmt)).all()]




async def notify_batch_finished(db, run: Run) -> None:
    """本次 run 收尾后，如果它所属批次的所有 run 都进终态了，就入队一次 AI 评估。

    批次状态是**现算的、不落库**，所以「批次完成」这个时刻没有别的地方能判定。
    幂等靠 `completed_notified_at` 的条件更新认领：最后两个 run 可能同时看到
    「全部终态」，只有一个能把 NULL 写成时间戳，另一个 rowcount=0 直接退出。
    """
    if run.batch_id is None:
        return
    statuses = list(
        (await db.execute(select(Run.status).where(Run.batch_id == run.batch_id))).scalars()
    )
    if any(s in ACTIVE_RUN_STATUSES for s in statuses):
        return

    claimed = await db.execute(
        update(RunBatch)
        .where(
            RunBatch.id == run.batch_id,
            RunBatch.completed_notified_at.is_(None),
        )
        .values(completed_notified_at=datetime.now(UTC))
    )
    if claimed.rowcount == 0:
        return

    # AI 评估入队（不在这里调模型 —— worker 要赶紧去跑下一个任务）
    from app.ai_review import enqueue_batch_check

    await enqueue_batch_check(db, run.batch_id)
    # 先把 AI 排队的写入落库，再做聚合报告 —— 报告合并要读各 run 的产物，
    # 失败了也只影响展示层，不能回滚前面已经完成的入队
    await db.commit()
    try:
        await _merge_batch_report(run.batch_id)
    except Exception:
        # 聚合报告是展示层增强，任何异常都不能让批次收尾逻辑整体失败
        log.exception("批次聚合报告生成失败（不影响执行结果）")


def _report_json_is_v2(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("version") == 2


async def _merge_batch_report(batch_id: uuid.UUID) -> None:
    """批次收尾后，把这次执行下所有 run 的报告合并成一份任务级聚合报告。

    产物写在第一个 run 的工作目录下（`<work_dir>/batch-report/`），这样已有的
    保留策略和任务删除都会连带清掉它，不需要再维护一套生命周期。
    """
    runner_root = get_settings().runner_path
    script = runner_root / "scripts" / "merge-reports.mjs"
    if not script.exists():
        return

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Run, System.name)
                .outerjoin(System, System.id == Run.system_id)
                .where(Run.batch_id == batch_id)
                .order_by(Run.created_at)
            )
        ).all()

    manifest: list[dict] = []
    host_dir: Path | None = None
    for run, system_name in rows:
        if not run.report_html_path:
            continue
        report = Path(run.report_html_path)
        if not report.exists():
            continue
        manifest.append(
            {
                "runId": str(run.id),
                "projectName": system_name or "",
                "status": run.status,
                "startedAt": run.started_at.isoformat() if run.started_at else None,
                "durationMs": run.duration_ms,
                "shardIndex": run.shard_index,
                "shardCount": run.shard_count,
                "htmlPath": str(report),
            }
        )
        if host_dir is None and run.work_dir:
            host_dir = Path(run.work_dir)

    if not manifest or host_dir is None:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(script),
            json.dumps(manifest, ensure_ascii=False),
            str(host_dir),
            cwd=str(runner_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("批次聚合报告生成超时：%s", batch_id)
        return
    except OSError as exc:
        log.warning("批次聚合报告生成失败（无法启动 node）：%s", exc)
        return

    if proc.returncode != 0:
        log.warning(
            "批次聚合报告生成失败（exit=%s）：%s",
            proc.returncode,
            (stderr or b"").decode(errors="replace")[-500:],
        )
        return
    log.info("批次 %s 聚合报告已生成：%s", batch_id, (stdout or b"").decode(errors="replace").strip()[-300:])


async def _render_report_markdown(runner_root: Path, report_path: Path) -> None:
    """把 HTML 报告转成 Markdown + 截图 + v2 结构化数据。

    转换交给执行器自己的 @midscene/core（node 子进程），平台不重复实现解析逻辑。
    失败只记日志 —— API 会把它当成正常降级，前端退回 Markdown。
    """
    if report_path.suffix.lower() != ".html":
        return
    markdown_dir = report_path.parent / "markdown"
    target = markdown_dir / "report.md"
    target_json = markdown_dir / "report.json"
    if target.exists() and _report_json_is_v2(target_json):
        return
    script = runner_root / "scripts" / "report-to-markdown.mjs"
    if not script.exists():
        return

    markdown_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(script),
            str(report_path),
            str(markdown_dir),
            cwd=str(runner_root),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("报告 Markdown 生成超时：%s", report_path)
        return
    except OSError as exc:
        log.warning("报告 Markdown 生成失败（无法启动 node）：%s", exc)
        return

    if proc.returncode != 0 or not target.exists():
        log.warning(
            "报告 Markdown 生成失败（exit=%s）：%s",
            proc.returncode,
            (stderr or b"").decode(errors="replace")[-500:],
        )


async def process(run_id: uuid.UUID) -> None:
    """准备 → 执行 → 落库。

    刻意拆成两段短事务：midscene 一次跑十几分钟，如果把 session 一直开着，
    并发槽位会长时间占着数据库连接（8 个槽位就是 8 条连接全程空等），
    高峰期容易把连接池打满。执行期间不放 session，跑完再开一段收尾。
    """
    settings = get_settings()
    runner_root = settings.runner_path

    # ---------------------------------------------------------------- 准备（短事务）
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None:
            log.error("run %s 不存在", run_id)
            return

        try:
            runner_env = staging.load_runner_env(runner_root)
        except FileNotFoundError as exc:
            await _fail(db, run, str(exc))
            return

        cases = await collect_cases(db, run)
        if not cases:
            await _fail(db, run, "任务下没有可执行的用例")
            return

        system = await db.get(System, run.system_id)
        task = await db.get(TestTask, run.task_id) if run.task_id else None
        retry = task.retry_count if task else 0

        # 模型一律来自系统级配置，没有全局兜底。
        # 入队时已经拦过一道，但配置可能在排队期间被改掉，这里再确认一次 ——
        # 否则会带着空配置去调 midscene，报出一堆看不懂的错。
        model_config = await db.get(SystemModelConfig, run.system_id)
        gaps = missing_fields(model_config)
        if gaps:
            await _fail(
                db,
                run,
                f"系统「{system.name if system else run.system_id}」的大模型配置不完整，"
                f"缺少：{'、'.join(gaps)}",
            )
            return
        overrides = system_model_overrides(model_config)

        env_row = None
        if run.environment_id:
            env_row = await db.get(SystemEnvironment, run.environment_id)
        if env_row is None and run.system_id:
            env_name = (run.environment_name or "默认").strip() or "默认"
            env_row = (
                await db.execute(
                    select(SystemEnvironment).where(
                        SystemEnvironment.system_id == run.system_id,
                        SystemEnvironment.name == env_name,
                    )
                )
            ).scalar_one_or_none()
            if env_row is None and env_name == "默认":
                env_row = await ensure_default_environment(db, run.system_id)

        plain_vars: dict[str, str] = {}
        secret_vars: dict[str, str] = {}
        if env_row is not None:
            try:
                plain_vars, secret_vars = await load_environment_variables(db, env_row.id)
            except RuntimeError as exc:
                await _fail(db, run, str(exc))
                return
            run.environment_id = env_row.id
            run.environment_name = env_row.name
            run.environment_base_url = env_row.base_url
        base_url = env_row.base_url if env_row is not None else None

        try:
            staged = staging.stage(
                runner_root=runner_root,
                run_id=run.id,
                cases=cases,
                base_url=base_url,
                retry=retry,
                timeout_ms=settings.case_timeout_ms,
                variables=plain_vars,
            )
        except OSError as exc:
            await _fail(db, run, f"准备工作目录失败：{exc}")
            return

        run.work_dir = str(staged.work_dir)
        run.result_dir = str(staged.result_dir)
        env_label = env_row.name if env_row is not None else "默认"
        # 指纹要含系统级覆盖和环境变量名 —— 换了模型/环境就是换了配置，趋势图上的跳变要能归因
        run.env_hash = staging.env_fingerprint(
            {
                **runner_env,
                **overrides,
                "QA_ENV_NAME": env_label if env_row is not None else "",
                "QA_BASE_URL": base_url or "",
            },
            variables={**plain_vars, **{key: "" for key in secret_vars}},
        )
        run.status = "running"
        await db.commit()

        # session 马上要关，后面只用这些不可变的值
        log_base_url = base_url or "-"
        case_count = len(cases)

    log.info(
        "run %s 开始执行：%d 个用例，环境=%s，base_url=%s，模型=%s（%s配置）",
        run_id,
        case_count,
        env_label,
        log_base_url,
        overrides.get("MIDSCENE_MODEL_NAME") or runner_env.get("MIDSCENE_MODEL_NAME") or "-",
        "系统级" if overrides else "全局",
    )

    # 用例数 × 单用例超时，再留 1.5 倍余量和 5 分钟固定开销
    timeout_ms = int(settings.case_timeout_ms * case_count * 1.5) + 300_000

    async def poll_cancel() -> bool:
        async with SessionLocal() as check_db:
            flag = (
                await check_db.execute(
                    select(Run.cancel_requested).where(Run.id == run_id)
                )
            ).scalar_one_or_none()
            return bool(flag)

    outcome = await exec_mod.execute(
        runner_root=runner_root,
        staged=staged,
        env=_build_env(
            runner_env, staged, runner_root, overrides, secret_vars, system_id=run.system_id
        ),
        timeout_ms=timeout_ms,
        poll_cancel=poll_cancel,
    )

    # ---------------------------------------------------------------- 收尾（短事务）
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None:
            # 执行期间记录被删了（正常不会发生：删任务会跳过执行中的任务）
            log.warning("run %s 在收尾时已不存在，跳过落库", run_id)
            return

        run.status = "parsing"
        await db.commit()

        if outcome.spawn_error:
            await _fail(db, run, outcome.spawn_error, staged)
            return

        summary = parsers.parse_summary(staged.result_dir)
        # 先清后建，保证 reparse 与首次解析结果一致（AI 调用是逐行追加的，不清会翻倍）
        await persist.clear_run_projection(db, run.id)
        await persist.apply_summary(db, run, summary)
        calls = parsers.parse_stats_log(staged.stats_log)
        await persist.apply_stats(db, run, calls)
        await persist.refresh_stability(db, _case_ids(summary))

        # 把 midscene 落在全局目录的最终报告搬进 run 私有目录
        report = staging.relocate_report(runner_root, staged, summary.midscene_run_id)
        report = report or _report_html(staged)
        if report:
            run.report_html_path = str(report)

        run.status, error_message = parsers.run_outcome(
            summary, cancelled=outcome.cancelled, timed_out=outcome.timed_out
        )
        if error_message:
            run.error_message = error_message
        run.ended_at = datetime.now(UTC)
        if run.status in {"failed", "timed_out", "cancelled"} and outcome.stdout_tail:
            run.error_message = (run.error_message or "") + "\n" + outcome.stdout_tail[-2000:]

        # 区分「用例失败」和「非用例原因导致的失败」。上游模型额度用尽会让整批用例
        # 记成 failed，若照单全收，通过率会因为额度问题崩掉 —— 统计口径要能排除这些。
        if run.failed or run.status in {"failed", "timed_out"}:
            kind = parsers.classify_failure(parsers.read_tail(staged.stdout_path, 65_536))
            if kind:
                run.failure_kind = kind
                detail = {
                    "ai_quota": "模型调用额度用尽",
                    "ai_service": "模型服务返回错误",
                    "network": "网络不可达",
                }.get(kind, kind)
                run.error_message = f"[非用例原因] {detail}" + (
                    f"\n{run.error_message}" if run.error_message else ""
                )
                log.warning("run %s 归因为 %s，不计入通过率", run.id, kind)

        case_ids = [c.test_case_id for c in (await _case_rows(db, run.id)) if c.test_case_id]
        await persist.refresh_stability(db, case_ids)
        await refresh_task_status(db, run.task_id)
        # 先把终态落库：后面的 AI 入队哪怕炸了，也不能让这次 run 卡在 parsing
        await db.commit()

        # 报告转 Markdown（纯展示层，失败不影响执行结果）
        if run.report_html_path:
            await _render_report_markdown(runner_root, Path(run.report_html_path))

        try:
            await notify_batch_finished(db, run)
            await db.commit()
        except Exception:
            await db.rollback()
            log.exception("AI 评估入队失败（不影响本次执行结果）")

        log.info(
            "run %s 结束：status=%s passed=%s/%s 耗时=%sms AI调用=%d",
            run.id,
            run.status,
            run.passed,
            run.total,
            run.duration_ms,
            len(calls),
        )


async def _case_rows(db, run_id: uuid.UUID):
    return (await db.execute(select(CaseResult).where(CaseResult.run_id == run_id))).scalars()


def _case_ids(summary: parsers.SummaryData) -> list[uuid.UUID]:
    """从 summary 里反查出我们自己的用例 id。

    midscene 的 caseId/documentId 是内容哈希，两份内容相同的用例会撞成同一个值，
    所以只能靠 sourcePath（= 暂存文件名 = 用例 UUID）反查。
    """
    ids: list[uuid.UUID] = []
    for outcome in summary.cases:
        if outcome.source_path and (case_id := staging.case_id_from_source_path(outcome.source_path)):
            ids.append(case_id)
    return ids


def _report_html(staged: staging.StagedRun) -> Path | None:
    """在 run 私有目录里找报告。

    优先 midscene 的 e2e 报告（自带截图时间线）；被 kill 掉、没生成 e2e 报告时
    退而用 playwright 的实时报告。
    """
    for pattern in ("midscene-e2e-*/index.html", "playwright-*.html"):
        matches = sorted(
            staged.report_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if matches:
            return matches[0]
    return None


async def _fail(db, run: Run, message: str, staged=None) -> None:
    run.status = "failed"
    run.error_message = message
    run.ended_at = datetime.now(UTC)
    if staged is not None:
        run.work_dir = str(staged.work_dir)
        run.result_dir = str(staged.result_dir)
    await refresh_task_status(db, run.task_id)
    # 同样先落终态，再发通知；通知失败不能把失败也吞成「卡住」
    await db.commit()
    try:
        await notify_batch_finished(db, run)
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception("AI 评估入队失败（不影响本次执行结果）")
    log.error("run %s 失败：%s", run.id, message)


def _install_signal_handlers() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stopping.set)
        except NotImplementedError:
            pass


async def _register_worker() -> None:
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        row = await db.get(WorkerHeartbeat, WORKER_ID)
        if row is None:
            db.add(
                WorkerHeartbeat(
                    id=WORKER_ID,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    slots=WORKER_CONCURRENCY,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
        else:
            row.hostname = socket.gethostname()
            row.pid = os.getpid()
            row.slots = WORKER_CONCURRENCY
            row.heartbeat_at = now
        await db.commit()
    log.info("worker %s 已注册（%s，pid=%s）", WORKER_ID[:8], socket.gethostname(), os.getpid())


async def _heartbeat_once() -> None:
    async with SessionLocal() as db:
        row = await db.get(WorkerHeartbeat, WORKER_ID)
        now = datetime.now(UTC)
        if row is None:
            db.add(
                WorkerHeartbeat(
                    id=WORKER_ID,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    slots=WORKER_CONCURRENCY,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
        else:
            row.heartbeat_at = now
            row.slots = WORKER_CONCURRENCY
        await db.commit()


async def heartbeat_loop() -> None:
    while not _stopping.is_set():
        try:
            await _heartbeat_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker 心跳写入失败")
        try:
            await asyncio.wait_for(_stopping.wait(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            pass


async def _deregister_worker() -> None:
    try:
        async with SessionLocal() as db:
            row = await db.get(WorkerHeartbeat, WORKER_ID)
            if row is not None:
                await db.delete(row)
                await db.commit()
    except Exception:
        log.exception("worker 注销失败（心跳过期后会被自动清理）")


async def recover_interrupted(db) -> int:
    """只清理「拥有者已失联」的 run。

    多 worker 下不能像以前那样把全库 running 都标失败 —— 那会在新 worker 启动时
    误杀其他 worker 正在跑的任务。判断依据是 worker_heartbeats：拥有者不在存活
    窗口内，才允许恢复。老数据没有 owner，按 started_at 是否超过失联窗口处理。
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=WORKER_STALE_SECONDS)
    alive = select(WorkerHeartbeat.id).where(WorkerHeartbeat.heartbeat_at >= cutoff)
    owned_by_dead = and_(
        Run.owner_worker_id.is_not(None),
        Run.owner_worker_id.notin_(alive),
    )
    legacy_stale = and_(
        Run.owner_worker_id.is_(None),
        or_(Run.started_at.is_(None), Run.started_at < cutoff),
    )
    stuck = list(
        (
            await db.execute(
                select(Run.id).where(
                    Run.status.in_(OCCUPYING_STATUSES),
                    or_(owned_by_dead, legacy_stale),
                )
            )
        ).scalars()
    )

    # 顺手清掉一天前的历史心跳，表不会无限长
    await db.execute(
        delete(WorkerHeartbeat).where(
            WorkerHeartbeat.heartbeat_at < datetime.now(UTC) - timedelta(days=1)
        )
    )
    if not stuck:
        await db.commit()
        return 0

    affected = list(
        (
            await db.execute(select(Run.task_id).where(Run.id.in_(stuck)).distinct())
        ).scalars()
    )
    await db.execute(
        update(Run)
        .where(Run.id.in_(stuck))
        .values(
            status="failed",
            ended_at=datetime.now(UTC),
            error_message="worker 失联，本次执行被中断",
        )
    )
    for task_id in affected:
        await refresh_task_status(db, task_id)
    await db.commit()
    return len(stuck)


async def read_global_limit() -> int:
    """读全局并发上限；读不到就退回环境变量/默认值。

    每 10 秒读一次，界面上改了不用重启 worker。
    """
    try:
        async with SessionLocal() as db:
            row = await db.get(AppSetting, 1)
    except Exception:
        log.exception("读取并发配置失败，沿用默认值")
        return WORKER_CONCURRENCY
    value = row.max_concurrent_runs if row is not None else WORKER_CONCURRENCY
    return max(1, min(32, int(value)))


async def worker_loop(slot: int, stop_evt: asyncio.Event) -> None:
    """一个并发槽位：认领一个 run、跑完、再认领下一个。

    多个槽位靠 `claim_next` 的 `FOR UPDATE SKIP LOCKED` 互不抢活 —— 同一个任务
    按分片拆成多个 run 时，这里就能同时跑起来。
    """
    while not _stopping.is_set() and not stop_evt.is_set():
        try:
            async with SessionLocal() as db:
                run = await claim_next(db)
            if run is None:
                try:
                    await asyncio.wait_for(_stopping.wait(), timeout=IDLE_POLL_SECONDS)
                except TimeoutError:
                    pass
                continue
            log.info("[%s slot %d] 认领 run %s", WORKER_ID[:8], slot, run.id)
            await process(run.id)
        except asyncio.CancelledError:
            break
        except Exception:
            # 单个 run 炸掉不能让 worker 死掉，否则队列后面全卡住
            log.exception("[%s slot %d] 处理队列时发生未预期异常，2 秒后继续", WORKER_ID[:8], slot)
            await asyncio.sleep(2)


async def supervise_slots() -> None:
    """按全局上限和我这个 worker 的槽位上限增减并发槽位。

    扩容立即生效；缩容只是给多出来的槽位**打个停止标记**，它会先把手上这个 run
    跑完再退出 —— 不能为了缩容把正在跑的测试杀掉。

    同时每 60 秒做一次失联 worker 的 run 恢复。
    """
    slots: dict[int, tuple[asyncio.Task, asyncio.Event]] = {}
    next_slot = 1
    last_recovery = 0.0
    try:
        while not _stopping.is_set():
            now = time.monotonic()
            if now - last_recovery >= 60:
                try:
                    async with SessionLocal() as db:
                        recovered = await recover_interrupted(db)
                    if recovered:
                        log.warning("恢复了 %d 个失联 worker 的 run", recovered)
                except Exception:
                    log.exception("失联 run 恢复失败")
                last_recovery = now

            desired = min(await read_global_limit(), WORKER_CONCURRENCY)
            while len(slots) < desired:
                stop_evt = asyncio.Event()
                task = asyncio.create_task(worker_loop(next_slot, stop_evt))
                slots[next_slot] = (task, stop_evt)
                next_slot += 1
                log.info("并发槽位扩到 %d", len(slots))
            while len(slots) > desired:
                slot_id = max(slots)
                _, stop_evt = slots.pop(slot_id)
                stop_evt.set()
                log.info("并发槽位缩到 %d（槽位 %d 会在当前 run 结束后退出）", len(slots), slot_id)
            # 等 10 秒或等到停止信号，别用 sleep：否则退出要等满 10 秒
            try:
                await asyncio.wait_for(_stopping.wait(), timeout=10)
            except TimeoutError:
                pass
    finally:
        for _, stop_evt in slots.values():
            stop_evt.set()
        await asyncio.gather(*(task for task, _ in slots.values()), return_exceptions=True)


async def main() -> None:
    _install_signal_handlers()
    settings = get_settings()
    await _register_worker()
    heartbeat_task = asyncio.create_task(heartbeat_loop())
    try:
        log.info(
            "worker %s 启动，runner=%s，每进程槽位=%d，全局上限=%d（在「测试任务」页可调，10 秒内生效）",
            WORKER_ID[:8],
            settings.runner_path,
            WORKER_CONCURRENCY,
            await read_global_limit(),
        )

        async with SessionLocal() as db:
            recovered = await recover_interrupted(db)
            if recovered:
                log.warning("清理了 %d 个失联 worker 的 run", recovered)

        await supervise_slots()
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await _deregister_worker()
        log.info("worker %s 停止", WORKER_ID[:8])
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
