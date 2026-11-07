"""midscene 产物的解析。

落库的原则：产物文件是权威，DB 里的行是投影副本，所以解析必须可重入（reparse 能重建）。
所有解析都容错 —— 半写的文件、被 kill 掉的 run、字段缺失都不应该让整个 run 落库失败。
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_STATS_LINE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s*(?P<payload>.*)$")


@dataclass
class CaseOutcome:
    document_id: str
    case_id: str
    case_index: int
    source_path: str | None
    name: str | None
    status: str
    attempt_count: int
    passed_after_retry: bool


@dataclass
class SummaryData:
    midscene_run_id: str | None = None
    midscene_status: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    model_name: str | None = None
    total: int | None = None
    passed: int | None = None
    failed: int | None = None
    not_run: int | None = None
    passed_after_retry: int | None = None
    final_pass_rate: float | None = None
    first_pass_rate: float | None = None
    filtered: int | None = None
    collection_errors: int | None = None
    collection_error_messages: list[str] = field(default_factory=list)
    project_failures: int | None = None
    report_html: str | None = None
    cases: list[CaseOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class AiCall:
    occurred_at: datetime | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_input: int | None
    cost_ms: int | None
    raw: str


def find_summary(result_dir: Path) -> Path | None:
    """--result-dir 下是 <midscene_runId>/summary.json，取最新的一个。"""
    if not result_dir.exists():
        return None
    candidates = sorted(
        result_dir.glob("*/summary.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None


def parse_summary(result_dir: Path) -> SummaryData:
    path = find_summary(result_dir)
    if path is None:
        return SummaryData(errors=["未找到 summary.json"])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return SummaryData(errors=[f"summary.json 解析失败：{exc}"])

    data = SummaryData(
        midscene_run_id=doc.get("runId"),
        midscene_status=doc.get("status"),
        exit_code=doc.get("exitCode"),
        duration_ms=doc.get("durationMs"),
        report_html=doc.get("report"),
    )
    summary = doc.get("summary") or {}
    data.total = summary.get("total")
    data.passed = summary.get("passed")
    data.failed = summary.get("failed")
    data.not_run = summary.get("notRun")
    data.passed_after_retry = summary.get("passedAfterRetry")
    data.final_pass_rate = summary.get("finalPassRate")
    data.first_pass_rate = summary.get("firstPassRate")
    data.filtered = summary.get("filtered")
    data.collection_errors = summary.get("collectionErrors")
    data.project_failures = summary.get("projectFailures")

    for project in doc.get("projects") or []:
        for entry in project.get("collectionErrors") or []:
            source = entry.get("sourcePath")
            message = _read_collection_error(path.parent, entry.get("errorFile"))
            data.collection_error_messages.append(
                f"{source}: {message}" if source else message
            )

    for project in doc.get("projects") or []:
        for case in project.get("cases") or []:
            attempts = case.get("attempts") or []
            # 有重试且最终通过 —— 这个差值就是「靠重试救回来」的部分，
            # 是首次通过率与最终通过率之差的来源，必须单独记下来
            data.cases.append(
                CaseOutcome(
                    document_id=str(case.get("documentId") or ""),
                    case_id=str(case.get("caseId") or ""),
                    case_index=int(case.get("caseIndex") or 0),
                    source_path=case.get("sourcePath"),
                    name=case.get("name"),
                    status=str(case.get("status") or "unknown"),
                    attempt_count=len(attempts) or 1,
                    passed_after_retry=len(attempts) > 1 and case.get("status") == "success",
                )
            )
    return data


def _read_collection_error(summary_dir: Path, error_file: str | None) -> str:
    """summary.json 只记 collection-errors 的文件名，具体原因在旁边的 JSON 里。

    errorFile 是执行器生成的相对路径，仍然要确认解析后没跳出结果目录。
    """
    if not error_file:
        return "用例文件收集失败"
    target = (summary_dir / error_file).resolve()
    if not target.is_relative_to(summary_dir.resolve()):
        return "用例文件收集失败"
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "用例文件收集失败"
    error = doc.get("error") or {}
    return str(error.get("message") or "用例文件收集失败")


def run_outcome(
    summary: SummaryData, *, cancelled: bool, timed_out: bool
) -> tuple[str, str | None]:
    """把执行器产物映射成 run 的终态与错误信息。

    收集错误也算失败：YAML 在收集阶段就解析不出来时一条用例都没跑，
    记成 succeeded 会把「什么都没测」伪装成成功。
    """
    if cancelled:
        return "cancelled", None
    if timed_out:
        return "timed_out", None
    if summary.errors:
        return "failed", "；".join(summary.errors)
    collection_errors = summary.collection_errors or 0
    if collection_errors > 0 or (
        (summary.total or 0) == 0
        and (summary.midscene_status == "failed" or (summary.exit_code or 0) != 0)
    ):
        message = "；".join(summary.collection_error_messages)
        if not message:
            message = (
                f"{collection_errors} 个用例文件收集失败"
                if collection_errors
                else "执行器没有产出任何用例结果"
            )
        return "failed", message
    return "succeeded", None


_EVIDENCE_TEXT_LIMIT = 500


def _step_evidence(step: dict) -> dict:
    """一步执行的可引用事实：做了什么、成没成、不成的原因。"""
    payload = step.get("input") or {}
    item = {
        "step": step.get("node"),
        "action": payload.get("prompt") or payload.get("url"),
        "status": step.get("status"),
    }
    summary = (step.get("output") or {}).get("summary")
    if summary:
        item["result"] = str(summary)[:_EVIDENCE_TEXT_LIMIT]
    error = (step.get("error") or {}).get("message")
    if error:
        item["reason"] = str(error)[:_EVIDENCE_TEXT_LIMIT]
    return {k: v for k, v in item.items() if v}


def read_case_evidence(result_dir: Path, limit: int = 12) -> list[dict]:
    """失败用例的真实执行依据：每一步做了什么 + 失败那一步给出的理由。

    报告 HTML 带截图、没法直接喂模型；能引用的事实在这个 JSON 里 —— 失败步骤的
    error.message 本身就是一段带因果的说明（例如「页面上只有用户名，没有邮箱」）。
    只看失败用例：全部用例的完整流水又长又没用，评估要的是「哪里错了、为什么」。
    """
    path = find_summary(result_dir)
    if path is None:
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    items: list[dict] = []
    for project in doc.get("projects") or []:
        for case in project.get("cases") or []:
            if str(case.get("status")) == "success":
                continue
            if len(items) >= limit:
                return items
            attempts = case.get("attempts") or []
            attempt = attempts[-1] if attempts else {}
            detail: dict = {}
            if result_file := attempt.get("resultFile"):
                try:
                    detail = json.loads(
                        (path.parent / result_file).read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    detail = {}
            steps = [
                _step_evidence(step)
                for phase in ("beforeEach", "steps", "afterEach")
                for step in detail.get(phase) or []
            ]
            if len(steps) > 12:
                # 用例步骤可能很多，全喂给模型既贵又浪费注意力：留开头的导航和末尾的失败点
                steps = steps[:2] + steps[-10:]
            items.append(
                {
                    "case": case.get("name"),
                    "source_path": case.get("sourcePath"),
                    "steps": steps,
                }
            )
    return items


def parse_plan_case_count(result_dir: Path) -> int:
    """从 plan.json 数出本次计划执行多少个 case。

    plan.json 在执行开始时就写好，而 summary.json 只在结束时才写 —— 所以实时进度
    必须靠 plan.json 拿到分母。
    """
    if not result_dir.exists():
        return 0
    for path in sorted(result_dir.glob("*/plan.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        total = 0
        for project in doc.get("projects") or []:
            for document in project.get("documents") or []:
                for case in document.get("cases") or []:
                    if case.get("selected", True):
                        total += 1
        return total
    return 0


def _int_field(fields: dict[str, str], key: str) -> int | None:
    try:
        return int(fields.get(key, ""))
    except ValueError:
        return None


def _parse_stats_line(line: str) -> AiCall | None:
    match = _STATS_LINE.match(line)
    if match is None:
        return None
    try:
        occurred_at: datetime | None = datetime.fromisoformat(match.group("ts"))
    except ValueError:
        occurred_at = None

    tokens = [t.strip() for t in match.group("payload").split(", ")]
    fields: dict[str, str] = {}
    for i in range(0, len(tokens) - 1, 2):
        fields[tokens[i]] = tokens[i + 1]

    return AiCall(
        occurred_at=occurred_at,
        model=fields.get("model") or None,
        raw=line,
        prompt_tokens=_int_field(fields, "prompt-tokens"),
        completion_tokens=_int_field(fields, "completion-tokens"),
        total_tokens=_int_field(fields, "total-tokens"),
        cached_input=_int_field(fields, "cached-input"),
        cost_ms=_int_field(fields, "cost-ms"),
    )


def parse_stats_log(path: Path) -> list[AiCall]:
    """逐行解析 ai-profile-stats.log。

    行格式（实测）：
        [2026-10-16T11:14:52.501+08:00] model, mimo-v2.6-flash, mode, xiaomi-mimo,
        streaming, false, prompt-tokens, 9172, completion-tokens, 135, total-tokens, 9307,
        cached-input, 8192, cost-ms, 4432, requestId, , slot, default, ...
    值可能为空（requestId），所以按 ", " 切完按对走，落单的键直接忽略。
    """
    if not path.exists():
        return []
    calls: list[AiCall] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if (call := _parse_stats_line(line)) is not None:
            calls.append(call)
    return calls


def read_tail(path: Path, limit: int = 4000) -> str:
    """读文件尾部。执行日志可能有几 MB，只读尾部够用且不占内存。"""
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if size > limit:
                handle.seek(size - limit)
                handle.readline()  # 丢掉可能被截断的半行
            return handle.read()
    except OSError:
        return ""


def classify_failure(stdout_text: str) -> str | None:
    """判断这次失败是不是「非用例本身」的原因。

    必须区分开：上游模型 429（额度用尽）、模型配置写错、模型服务不可达、网络不通，
    这些会让用例失败，但把它们算进通过率会让统计数字崩掉，逼人去追根本不存在的 bug。
    实测踩过两次：模型账号周额度用尽、以及把 MODEL_FAMILY 填成版本不接受的值。

    返回 None 表示是真实的用例失败（或无法归因）。
    """
    text = stdout_text
    if any(
        marker in text
        for marker in ("GoUsageLimitError", "usage limit", "insufficient_quota", "额度")
    ):
        return "ai_quota"
    # 模型/连接配置写错：midscene 会抛 "Invalid MIDSCENE_MODEL_FAMILY value: xxx"
    # 之类，属于配置问题，不是用例问题
    if any(
        marker in text
        for marker in (
            "Invalid MIDSCENE_MODEL",
            "MIDSCENE_MODEL_API_KEY is required",
            "MIDSCENE_MODEL_NAME is required",
            "model config",
        )
    ):
        return "ai_config"
    if any(
        marker in text
        for marker in (
            "AI model request failed",
            "failed to call AI model service",
            "401 Unauthorized",
            "Invalid API key",
        )
    ):
        return "ai_service"
    if any(
        marker in text
        for marker in ("ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "fetch failed", "socket hang up")
    ):
        return "network"
    return None


def read_progress(result_dir: Path, expected_cases: int) -> dict[str, int]:
    """执行中的实时进度。

    summary.json 只在执行结束才写，所以进度只能靠 operations.jsonl 的流水推。

    用什么当「一个用例跑完」的标志：**每个 attempt 恰好有一条 operationKind=cleanup
    的 settled 事件**（summary.json 里 attempt.cleanup 字段印证了这一点）。不能拿
    「scope 有 settled 事件」来判，因为一个 attempt 内部会 settled 很多次，那样一开始
    就是 100%。

    分母优先用 plan.json 的 selected 用例数 —— 它也只在开始时写，正好当分母。
    """
    completed: set[str] = set()
    if result_dir.exists():
        for path in sorted(
            result_dir.glob("*/operations.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                with path.open(encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("kind") != "settled":
                            continue
                        if event.get("operationKind") != "cleanup":
                            continue
                        scope = event.get("scopeId")
                        # project-* 是项目级的收尾，不是单个用例的
                        if scope and not str(scope).startswith("project-"):
                            completed.add(str(scope))
            except OSError:
                continue
            break

    denominator = expected_cases or parse_plan_case_count(result_dir)
    done = len(completed)
    return {
        "done": done,
        "total": denominator,
        "percent": min(100, round(done * 100 / denominator)) if denominator else 0,
    }
