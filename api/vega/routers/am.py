"""agent-memory 团队/专家下拉数据源：转发 hub 的 meta RPC，前端实时拉取。

hub 接口（面板同款）：POST {am_hub_base_url}/api/v1/meta/{method}，
body 带 user_key，头带 X-Tdai-Service-Id / X-Tdai-User-Key。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_person
from ..config import get_settings
from ..llm import am_user_key
from ..models import Person

router = APIRouter(prefix="/api/am", tags=["agent-memory"])


async def _rpc(method: str, body: dict, key: str) -> dict:
    s = get_settings()
    headers = {"X-Tdai-Service-Id": s.am_space_id, "X-Tdai-User-Key": key}
    try:
        # hub 是内网服务，别走系统 HTTP 代理（mihomo 转发该请求直接回空 502）
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            r = await client.post(
                f"{s.am_hub_base_url}/api/v1/meta/{method}",
                json={"user_key": key, **body},
                headers=headers,
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"连不上 agent-memory：{e}") from e
    # hub 业务错误也回 JSON {code, message}；网关 502 等会回非 JSON，别当 500 抛
    try:
        data = r.json()
    except ValueError as e:
        raise HTTPException(502, f"agent-memory {method} 返回异常（HTTP {r.status_code}）") from e
    if r.status_code != 200 or data.get("code") != 0:
        raise HTTPException(502, f"agent-memory {method} 失败：{data.get('message') or r.status_code}")
    return data.get("data") or {}


@router.get("/teams")
async def list_teams(me: Person = Depends(current_person)) -> list[dict]:
    """团队下拉：当前登录用户在 agent-memory 里可见的团队。"""
    # hub 校验 limit<=100，超了直接 400；团队量级小，一页足够
    data = await _rpc("team/list", {"limit": 100, "offset": 0}, am_user_key(me))
    return [
        {"id": t.get("team_id", ""), "name": t.get("name", ""), "description": t.get("description", "")}
        for t in data.get("items", [])
    ]


@router.get("/agents")
async def list_agents(team_id: str, me: Person = Depends(current_person)) -> list[dict]:
    """专家下拉：指定团队下的 active 专家。"""
    data = await _rpc(
        "agent/list", {"team_id": team_id, "status": "active", "limit": 100, "offset": 0}, am_user_key(me)
    )
    return [
        {"id": a.get("agent_id", ""), "name": a.get("name", ""), "description": a.get("description", "")}
        for a in data.get("items", [])
    ]
