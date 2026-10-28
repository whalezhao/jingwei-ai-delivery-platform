"""一次性 LLM 调用（CICD 分析、成员 AI 对配、组级分析）。

统一走 agent-memory proxy 的 anthropic Messages 协议：
p-url/p-api-key 由 proxy 负责协议转换与鉴权头，本模块不感知上游类型。
"""

from __future__ import annotations

import httpx

from .config import get_settings
from .llm import LLMTarget, am_user_key
from .models import DevGroup, Person


class LLMError(RuntimeError):
    pass


async def ask_llm(
    prompt: str,
    *,
    system: str = "",
    target: LLMTarget,
    person: Person | None = None,
    group: DevGroup | None = None,
    max_tokens: int = 2048,
    timeout: float = 120.0,
) -> str:
    s = get_settings()
    headers: dict[str, str] = {
        "x-api-key": am_user_key(person),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if group and group.am_team_id:
        headers["x-team-id"] = group.am_team_id
    if target.base_url:
        headers["p-url"] = target.base_url
        if target.api_key:
            headers["p-api-key"] = target.api_key

    body: dict = {
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    if target.model:
        body["model"] = target.model

    url = f"{s.am_base_url.rstrip('/')}/claude-code/{s.am_space_id}/v1/messages"
    async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
        r = await client.post(url, json=body, headers=headers)
    if r.status_code != 200:
        raise LLMError(f"LLM 调用失败 {r.status_code}: {r.text[:300]}")
    data = r.json()
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    if not parts:
        raise LLMError(f"LLM 返回无文本块: {str(data)[:300]}")
    return "".join(parts)
