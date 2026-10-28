"""LLM 解析链 + claude -p 的 agent-memory 环境组装。

解析规则（DESIGN §4.0）：
- plan 会话 / 开发任务：人员 → 研发组 → proxy 默认
- CICD 分析 / 组级分析：研发组 → proxy 默认
所有流量统一走 agent-memory proxy（anthropic Messages 协议），
p-url/p-api-key 由 proxy 决定上游协议与鉴权头，本系统不感知。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

from .config import get_settings
from .crypto import decrypt
from .models import DevGroup, Person


@dataclass
class LLMTarget:
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    effort: str | None = None  # 思考等级，链路解析后必有值（兜底 am_default_effort）
    source: str = "default"  # person / group / default


def _person_llm(p: Person) -> LLMTarget | None:
    if p and p.llm_base_url:
        secret = get_settings().secret_key
        return LLMTarget(p.llm_base_url, decrypt(secret, p.llm_api_key_enc), p.llm_model, p.llm_effort, "person")
    return None


def _group_llm(g: DevGroup) -> LLMTarget | None:
    if g and g.llm_base_url:
        secret = get_settings().secret_key
        return LLMTarget(g.llm_base_url, decrypt(secret, g.llm_api_key_enc), g.llm_model, g.llm_effort, "group")
    return None


def resolve_llm(person: Person | None, group: DevGroup | None, *, person_first: bool = True) -> LLMTarget:
    """person_first=True：人员优先（plan 会话/开发任务）；False：只用组（CICD/组级分析）。"""
    target: LLMTarget | None = None
    if person_first and person:
        target = _person_llm(person)
    if not target and group:
        target = _group_llm(group)
    if not target:
        target = LLMTarget(source="default")
    # effort 与 model/url 独立按 人员 → 组 → 兜底 解析（人员可以只选思考等级不配 LLM）
    effort = (person.llm_effort if person_first and person else None) or (group.llm_effort if group else None)
    target.effort = effort or get_settings().am_default_effort
    return target


def am_user_key(person: Person | None) -> str:
    """人员绑定的 agent-memory user_key 优先，否则平台公共 key。"""
    if person and person.am_user_key_enc:
        k = decrypt(get_settings().secret_key, person.am_user_key_enc)
        if k:
            return k
    return get_settings().am_user_key


def build_claude_env(
    target: LLMTarget,
    *,
    team_id: str | None,
    agent_id: str | None,
    user_key: str,
    conversation_id: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """组装 claude -p 的 env：base_url 指向 proxy + 自定义绑定头。

    conversation_id：proxy 的全局会话按 x-conversation-id 记忆资产选择，同一 id 只在
    首次被问「是否关联团队资产」，之后直选注入——各业务传稳定 id（会话/任务/MR）。
    """
    s = get_settings()
    headers: list[str] = []
    if team_id:
        # HTTP 头值不允许非 ASCII（claude CLI 会拒掉整个 ANTHROPIC_CUSTOM_HEADERS 直接退出 1），
        # 中文 team/agent id 用百分号编码传输，ASCII id 原样
        headers.append(f"x-team-id: {quote(team_id)}")
    if agent_id:
        headers.append(f"x-agent-id: {quote(agent_id)}")
    if conversation_id:
        headers.append("x-task-id: no-task")
        headers.append(f"x-conversation-id: {quote(conversation_id)}")
    if target.base_url:
        headers.append(f"p-url: {target.base_url}")
        if target.api_key:
            headers.append(f"p-api-key: {target.api_key}")
    # proxy 把响应的 content-encoding 头剥掉却转发压缩后的 body（上游按客户端的
    # accept-encoding 压了）。claude 客户端自带 br/zstd，遇到会压缩的自定义上游
    # （如 DeepSeek）就解析失败：claude -p 退出 1，stderr 只有一句无关的
    # unrecognized_model 告警（"JSON Parse error: Unexpected token '('"）。
    # 显式要 identity，让上游不压缩。
    headers.append("accept-encoding: identity")

    env = {
        "ANTHROPIC_BASE_URL": f"{s.am_base_url.rstrip('/')}/claude-code/{s.am_space_id}",
        "ANTHROPIC_API_KEY": user_key,
        "ANTHROPIC_AUTH_TOKEN": user_key,
        "ANTHROPIC_CUSTOM_HEADERS": "\n".join(headers),
        # 避免 runner 机器上的全局 ~/.claude 配置串味
        "CLAUDE_CONFIG_DIR": os.environ.get("VEGA_CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude-vega")),
        "DISABLE_TELEMETRY": "1",
        # claude 默认 max_tokens 128000 会超 Qwen 128144 总上下文，必须压小
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(s.claude_max_output_tokens),
        # 自定义模型名 claude CLI 不识别，不显式声明窗口会 -p 直接退出 1（unrecognized_model）
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(s.claude_max_context_tokens),
        # 思考等级随 claude_env 通道下发（解析链产物），run_claude 取走组装 --effort
        "VEGA_CLAUDE_EFFORT": target.effort or s.am_default_effort,
    }
    # 兜底层：proxy 不认 claude 原生模型名，须显式指定其默认模型
    model = target.model or s.am_default_model
    # claude 的后台调用（生成会话标题等）走 haiku/sonnet 映射，不一起改掉会 404 → claude -p 退出 1
    for k in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        env[k] = model
    if extra:
        env.update(extra)
    return env
