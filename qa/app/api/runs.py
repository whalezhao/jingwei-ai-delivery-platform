import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select

from ..config import get_settings
from ..deps import CurrentPerson, DbSession, require_system_access
from ..models import AiCall, CaseResult, Run, RunBatch, TestTask
from ..runner import parsers, persist
from ..schemas import CaseResultOut, RunDetailOut, RunListOut, RunProgressOut
from ..taskstatus import ACTIVE_RUN_STATUSES, refresh_task_status
from .task_common import failed_case_ids

router = APIRouter(tags=["runs"])

REPORT_TOKEN_TTL_SECONDS = 3600


# ----------------------------------------------------------------------- 报告托管
# 报告的访问控制用「不可猜的路径」而不是签名 URL。原因是实测出来的两个硬约束：
#
# 1. midscene 的报告 HTML 不是纯静态文档，它启动时会读 localStorage，被
#    `<iframe sandbox="allow-scripts">`（不给 allow-same-origin）挡住后会直接白屏
#    （实测：divCount=1、rootLen=0，什么都没有）。
# 2. 报告的时间线截图是**独立文件**，浏览器请求它们时不会带上 index.html 的查询串
#    —— 签名只签在入口 URL 上，子资源一律 403。
#
# 所以签名 URL 这条路要打通就得引 cookie + 跨域 SameSite 问题，代价远大于收益。
# 改成：路径里带 run 的 UUID（v4，122 位熵，等同于一个 capability URL）。
# 知道链接的人能看，猜不到的人看不到 —— 和分享一个未公开链接是同一级别的保护。
#
# 如果以后报告需要更严的访问控制，正确做法是把它挪到**独立 origin**（另一个端口），
# 那里再加真正的鉴权，而不是在同一个 origin 上加签名。


def make_report_url(run_id: uuid.UUID, relative: str = "index.html") -> str:
    # Vega 前端直接打开这个地址；必须是绝对 URL，否则会解析到 Vega 自己的 origin
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/reports/{run_id}/{relative}"


def _resolve_data_path(raw: str | None) -> Path | None:
    """把数据库里存的路径落到当前文件系统上。

    历史数据存的是宿主机绝对路径（…/backend/runner/.runs/…），切到容器后同样的
    相对位置在 RUNNER_DIR 下。原路径存在就直接用，否则按 `/runner/` 后面那段重新拼。
    """
    if not raw:
        return None
    path = Path(raw)
    if path.exists():
        return path
    marker = "/runner/"
    if marker in raw:
        candidate = get_settings().runner_path / raw.split(marker, 1)[1]
        if candidate.exists():
            return candidate
    return path


async def _require_run(db, run_id: uuid.UUID, user, minimum: str = "view") -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "执行记录不存在")
    await require_system_access(db, run.system_id, user, minimum)
    return run


@router.post("/api/runs/{run_id}/rerun-failed")
async def rerun_run_failed(
    run_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> dict:
    run = await _require_run(db, run_id, user, "member")
    if run.status in ACTIVE_RUN_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "这次执行还没结束，不能重跑")
    if run.task_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "源任务已删除，无法重跑")
    task = await db.get(TestTask, run.task_id)
    if task is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "源任务已删除，无法重跑")
    case_ids, unresolved = await failed_case_ids(db, run_id=run.id)
    batch = await db.get(RunBatch, run.batch_id) if run.batch_id else None
    # batches 依赖 runs 的路径解析，这里延迟导入避免循环
    from .batches import _rerun_failed

    return await _rerun_failed(
        db,
        user,
        task,
        case_ids,
        unresolved=unresolved,
        parent_batch_id=run.batch_id,
        environment_name=run.environment_name
        or (batch.environment_name if batch else None),
    )


# ------------------------------------------------------------------------- 列表详情


@router.get("/api/runs", response_model=RunListOut)
async def list_runs(
    user: CurrentPerson,
    db: DbSession,
    system_id: int | None = None,
    task_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
    offset: int = 0,
) -> RunListOut:
    # 跨系统全量列表只给管理员；普通用户必须按系统查，权限走系统归属
    if system_id is not None:
        await require_system_access(db, system_id, user)
    elif not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "请指定系统")

    conditions = []
    if system_id is not None:
        conditions.append(Run.system_id == system_id)
    if task_id is not None:
        conditions.append(Run.task_id == task_id)
    if status_filter:
        conditions.append(Run.status == status_filter)

    total = (
        await db.execute(select(func.count(Run.id)).where(*conditions))
    ).scalar_one()
    stmt = (
        select(Run)
        .where(*conditions)
        .order_by(Run.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return RunListOut(items=list((await db.execute(stmt)).scalars()), total=total)


@router.get("/api/runs/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> RunDetailOut:
    run = await _require_run(db, run_id, user)

    stats = (
        await db.execute(
            select(
                func.count(AiCall.id),
                func.coalesce(func.sum(AiCall.total_tokens), 0),
                func.coalesce(func.sum(AiCall.cost_ms), 0),
            ).where(AiCall.run_id == run_id)
        )
    ).one()

    # 执行中时 summary.json 还不存在，进度只能从 operations.jsonl 的 settled 流水推
    progress = RunProgressOut()
    if run.status in {"preparing", "running"} and run.result_dir:
        progress = RunProgressOut(**parsers.read_progress(Path(run.result_dir), 0))

    payload = RunDetailOut.model_validate(run)
    payload.ai_calls = stats[0]
    payload.ai_total_tokens = int(stats[1])
    payload.ai_cost_ms = int(stats[2])
    resolved_report = _resolve_data_path(run.report_html_path)
    payload.report_available = bool(resolved_report and resolved_report.exists())
    payload.progress = progress
    return payload


@router.get("/api/runs/{run_id}/cases", response_model=list[CaseResultOut])
async def list_run_cases(
    run_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> list[CaseResult]:
    await _require_run(db, run_id, user)
    stmt = (
        select(CaseResult)
        .where(CaseResult.run_id == run_id)
        .order_by(CaseResult.case_index, CaseResult.name)
    )
    return list((await db.execute(stmt)).scalars())


@router.get("/api/runs/{run_id}/report-url")
async def get_report_url(run_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> dict[str, str]:
    run = await _require_run(db, run_id, user)
    resolved = _resolve_data_path(run.report_html_path)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该执行没有生成报告")
    if not resolved.exists():
        raise HTTPException(status.HTTP_410_GONE, "报告文件已被清理")
    return {"url": make_report_url(run_id)}


# 报告里的英文小标题（执行器生成）翻成中文，正文里的参数/日志保持原样
_HEADING_LABELS = {
    "# YAML execution": "# 执行报告",
    "## Model Info": "## 模型信息",
    "## Timing Summary": "## 耗时汇总",
    "## Token Usage Summary": "## Token 用量",
    "### Model Usage": "### 模型调用",
    "### Screenshots": "### 截图",
    "### Param": "### 参数",
    "### Output": "### 输出",
    "### Log": "### 日志",
    "### Reasoning Content": "### 推理过程",
    "### Recorder": "### 录制",
}


@router.get("/api/runs/{run_id}/report-markdown")
async def get_report_markdown(
    run_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> dict[str, object]:
    """报告正文（Markdown + 截图）。

    由执行器自带的 report-tool 从 HTML 报告转录；老数据没有转录文件时返回
    available=false，前端退回「原始报告」链接。
    """
    run = await _require_run(db, run_id, user)
    html_path = _resolve_data_path(run.report_html_path)
    if html_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该执行没有生成报告")

    markdown_path = html_path.parent / "markdown" / "report.md"
    if not markdown_path.exists():
        return {"available": False, "markdown": ""}

    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    # 截图是 markdown/screenshots 下的文件，换成平台托管地址（走 /reports 的路径校验）
    text = re.sub(
        r"!\[([^\]]*)\]\((?:\./)?screenshots/([^)]+)\)",
        lambda m: f"![{m.group(1)}]({make_report_url(run_id, f'markdown/screenshots/{m.group(2)}')})",
        text,
    )
    lines = [_HEADING_LABELS.get(line, line) for line in text.splitlines()]
    # 页面顶部已经有「执行报告」卡片标题，正文里这个总标题就不再重复
    if lines and lines[0].strip() == "# 执行报告":
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return {"available": True, "markdown": "\n".join(lines)}


@router.get("/api/runs/{run_id}/report-structured")
async def get_report_structured(
    run_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> dict[str, object]:
    """结构化报告：执行/步骤/截图/原始数据的层级，前端据此还原原版报告的能力。

    和 report-markdown 一样由执行器在收尾时转录；老数据没有 JSON 时 available=false，
    前端退回 Markdown 版本。
    """
    run = await _require_run(db, run_id, user)
    html_path = _resolve_data_path(run.report_html_path)
    if html_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该执行没有生成报告")

    json_path = html_path.parent / "markdown" / "report.json"
    if not json_path.exists():
        return {"available": False, "data": None}
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "data": None}
    # v1 是按执行段平铺的旧结构，前端已经只认 v2（用例 → 步骤），让老数据走 Markdown
    if not isinstance(data, dict) or data.get("version") != 2:
        return {"available": False, "data": None}
    return {"available": True, "data": data}


# ------------------------------------------------------------------------- 报告静态


@router.get("/reports/{run_id}/{relative:path}")
async def serve_report(
    run_id: uuid.UUID,
    relative: str,
    db: DbSession,
) -> FileResponse:
    """托管执行生成的 HTML 报告。

    注意**不要**给它加 `<iframe sandbox="allow-scripts">`（不给 allow-same-origin）——
    实测报告会白屏：它启动时要读 localStorage，被沙箱挡掉就整个不渲染。
    前端用的是 allow-scripts + allow-same-origin。

    安全上有两点：
    1. 只允许落在该 run 的 report 目录内 —— 否则 `../` 能读到同级的 .env 里的模型密钥。
       这是唯一一条由 URL 决定文件路径的入口，必须守死。
    2. 访问控制靠 run UUID 不可猜（见上面 make_report_url 的说明）。
    """
    run = await db.get(Run, run_id)
    html_path = _resolve_data_path(run.report_html_path) if run else None
    if run is None or html_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "报告不存在")

    # report_html_path 形如 <work_dir>/midscene_run/report/<midsceneRunId>/index.html
    report_root = html_path.parent.resolve()
    target = (report_root / relative).resolve()
    if not target.is_relative_to(report_root):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "非法路径")
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")

    if target.suffix.lower() == ".html":
        # 原始报告（新窗口打开）只改标题：执行器写死的是 "Report - Midscene.js"
        html = target.read_text(encoding="utf-8", errors="replace")
        html = re.sub(
            r"<title>.*?</title>", "<title>执行报告</title>", html, count=1, flags=re.DOTALL
        )
        # 动态改写过的响应必须禁缓存，URL 每次都一样
        return HTMLResponse(
            html, headers={"Cache-Control": "no-store, must-revalidate"}
        )
    return FileResponse(target)


# ------------------------------------------------------------------- 取消 / 重新解析


@router.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> dict[str, str]:
    run = await _require_run(db, run_id, user, "member")

    if run.status == "queued":
        # 还没被 worker 领走，直接置终态即可 —— 但任务状态是派生值，得一起重算，
        # 否则任务会一直停在「排队中」（这一条以前漏了）
        run.status = "cancelled"
        run.ended_at = datetime.now(UTC)
        await refresh_task_status(db, run.task_id)
        await db.commit()
        return {"status": run.status}

    if run.status not in {"preparing", "running"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "该执行已经结束，无法取消")

    # 子进程句柄只存在于 worker 里，所以这里只打标记，worker 轮询到后 killpg
    run.cancel_requested = True
    await db.commit()
    return {"status": "cancelling"}


@router.post("/api/runs/{run_id}/reparse")
async def reparse_run(run_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> dict[str, object]:
    """从磁盘产物重建投影。

    约 100 秒一次的 AI 执行很贵，解析逻辑改错或产物被后补时不该重跑一遍。
    """
    run = await _require_run(db, run_id, user, "member")
    if not run.result_dir:
        raise HTTPException(status.HTTP_409_CONFLICT, "该执行没有可解析的产物目录")

    result_dir = Path(run.result_dir)
    summary = parsers.parse_summary(result_dir)
    if summary.errors:
        raise HTTPException(status.HTTP_409_CONFLICT, "；".join(summary.errors))

    await persist.clear_run_projection(db, run_id)
    await persist.apply_summary(db, run, summary)

    stats_log = Path(run.work_dir) / "midscene_run" / "log" / "ai-profile-stats.log" if run.work_dir else None
    calls = parsers.parse_stats_log(stats_log) if stats_log else []
    await persist.apply_stats(db, run, calls)

    case_ids = list(
        (
            await db.execute(
                select(CaseResult.test_case_id).where(
                    CaseResult.run_id == run_id, CaseResult.test_case_id.is_not(None)
                )
            )
        ).scalars()
    )
    await persist.refresh_stability(db, case_ids)

    # 归因也要重算：解析逻辑演进后（比如新增了「非用例原因」的识别），
    # reparse 应该能修正老数据的归因，而不是只有指标被重建
    run.failure_kind = None
    if run.failed:
        stdout_path = Path(run.work_dir) / "stdout.log" if run.work_dir else None
        if stdout_path and stdout_path.exists():
            run.failure_kind = parsers.classify_failure(parsers.read_tail(stdout_path, 65_536))

    await db.commit()
    return {"cases": len(summary.cases), "ai_calls": len(calls), "failure_kind": run.failure_kind}
