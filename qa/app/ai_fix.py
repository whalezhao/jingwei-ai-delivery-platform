"""根据一次真实失败，生成用例修改建议（不落库）。"""

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_case import call_model, extract_yaml
from .case_versions import diff_text
from .model_health import describe_model_error
from .modelconfig import missing_fields
from .models import CaseResult, Feature, SystemModelConfig, Run, TestCase
from .quota import ensure_quota, record_usage
from .runner.parsers import read_case_evidence
from .yamlcheck import validate as validate_yaml

SUCCESS_STATUSES = {"success", "passed"}

SYSTEM_PROMPT = """你是 UI 自动化测试用例修复助手。用户会给你一份 midscene 用例 YAML，\
以及它在最近一次真实执行中的失败步骤和错误理由。请判断失败是「用例写得不对」还是\
「页面/产品问题」，然后输出一份可以直接执行的完整 YAML。

硬性要求：
- 保留 `beforeEach` 里的 `gotoUrl: ${BASE_URL}`，不要写死 URL。
- 保留每个 aiAct 的 `options.cacheable` 判断；不要因为修复而删掉缓存配置。
- 只修改导致失败的部分，不要顺手改写无关用例。
- 如果失败原因明确是产品缺陷、无法通过改用例绕过，就不要硬改，直接在说明里讲清楚。
- 最终把完整 YAML 放在 ```yaml 代码块里；代码块外写简短的原因说明。
"""


@dataclass
class FixProposal:
    run_id: uuid.UUID
    proposed_yaml: str
    explanation: str
    diff: str
    yaml_ok: bool
    yaml_errors: list[str]
    model_name: str | None


async def _case_system(db: AsyncSession, case: TestCase) -> int:
    system_id = await db.scalar(
        select(Feature.system_id).where(Feature.id == case.feature_id)
    )
    if system_id is None:
        raise ValueError("用例所属功能/系统不存在")
    return system_id


async def _latest_failure(
    db: AsyncSession, case_id: uuid.UUID, system_id: int, run_id: uuid.UUID | None
) -> tuple[CaseResult, Run] | None:
    stmt = (
        select(CaseResult, Run)
        .join(Run, Run.id == CaseResult.run_id)
        .where(
            CaseResult.test_case_id == case_id,
            CaseResult.status.not_in(SUCCESS_STATUSES),
            Run.system_id == system_id,
        )
        .order_by(Run.started_at.desc().nullslast(), Run.created_at.desc())
        .limit(1)
    )
    if run_id is not None:
        stmt = stmt.where(Run.id == run_id)
    row = (await db.execute(stmt)).first()
    return (row[0], row[1]) if row else None


def _evidence(case: TestCase, result_dir: Path | None) -> list[dict]:
    if result_dir is None or not result_dir.exists():
        return []
    items = read_case_evidence(result_dir, limit=100)
    mine = [
        item
        for item in items
        if str(item.get("source_path") or "").endswith(f"{case.id}.yaml")
        or item.get("case") == case.name
    ]
    return mine or items


def _explanation(reply: str) -> str:
    text = re.sub(r"```ya?ml.*?```", "", reply, flags=re.DOTALL | re.IGNORECASE).strip()
    return text or "模型只返回了 YAML，没有额外说明。"


async def propose_case_fix(
    db: AsyncSession,
    case: TestCase,
    *,
    run_id: uuid.UUID | None = None,
) -> FixProposal:
    system_id = await _case_system(db, case)
    failure = await _latest_failure(db, case.id, system_id, run_id)
    if failure is None:
        raise ValueError("这条用例还没有失败记录，无法生成修复建议")
    result, run = failure

    config = await db.get(SystemModelConfig, system_id)
    if missing_fields(config):
        raise ValueError("系统模型配置不完整，无法调用 AI")
    await ensure_quota(db, system_id, config)

    # 延迟导入避免 API 层与工具层循环依赖
    from .api.runs import _resolve_data_path

    result_dir = _resolve_data_path(run.result_dir)
    evidence = _evidence(case, result_dir)
    evidence_text = json.dumps(evidence, ensure_ascii=False, indent=2)[:12_000]
    prompt = (
        f"## 当前用例 YAML\n```yaml\n{case.yaml_content.strip()}\n```\n\n"
        f"## 最近一次失败\n"
        f"- 用例结果：{result.status}（尝试 {result.attempt_count} 次）\n"
        f"- 运行 ID：{run.id}\n"
        f"- 失败步骤与理由（JSON）：\n```json\n{evidence_text or '[]'}\n```\n\n"
        "请判断根因并给出修复后的完整 YAML。"
    )

    try:
        reply, usage = await call_model(
            config,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            timeout=120.0,
            max_tokens=4096,
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(describe_model_error(exc, timeout=120.0)) from exc

    await record_usage(db, system_id, "case_fix", usage)
    proposed = extract_yaml(reply)
    if not proposed:
        raise ValueError("模型没有返回 YAML，请重试或换一个模型")

    errors, _ = validate_yaml(proposed)
    return FixProposal(
        run_id=run.id,
        proposed_yaml=proposed,
        explanation=_explanation(reply),
        diff=diff_text(case.yaml_content, proposed, "当前版本", "AI 建议"),
        yaml_ok=not errors,
        yaml_errors=errors,
        model_name=config.model_name,
    )
