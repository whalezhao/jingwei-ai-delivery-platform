"""agent-memory 接入：hub 地址配置、面板反代与团队/专家下拉。

hub 接口（面板同款）：POST {hub}/api/v1/meta/{method}，
body 带 user_key，头带 X-Tdai-Service-Id / X-Tdai-User-Key。

面板本身没有 CORS 之外的跨域登录能力，登录态存在浏览器 localStorage；
所以面板走本服务反代到同源 /am/，前端才能用本人 user_key 免登。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person
from ..config import get_settings
from ..db import get_db
from ..llm import am_user_key
from ..models import AppSetting, Person

router = APIRouter(prefix="/api/am", tags=["agent-memory"])
panel_router = APIRouter(tags=["agent-memory-panel"])

AM_URL_KEY = "am_hub_url"
AM_PROXY_KEY = "am_proxy_url"
AM_ENABLED_KEY = "am_enabled"
# 进程启动时的 .env 兜底；集成中心清空配置时回滚到这里
_ENV_PROXY = get_settings().am_base_url


def env_proxy_url() -> str:
    return _ENV_PROXY


async def hub_base_url(db: AsyncSession) -> str:
    """集成中心配置优先，未配置回落 .env 的 VEGA_AM_HUB_BASE_URL。"""
    row = await db.get(AppSetting, AM_URL_KEY)
    if row and row.value:
        return row.value.rstrip("/")
    return get_settings().am_hub_base_url.rstrip("/")


async def apply_proxy_url(db: AsyncSession) -> None:
    """把集成中心的 proxy 地址写回进程内 settings —— claude/一次性 LLM 都从这里取，
    不用为数据库会话改它们的同步调用链（与 octo 全局 token 同一手法）。
    shortcut: 只作用于本进程，多副本部署时需每个实例重新保存或重启。"""
    row = await db.get(AppSetting, AM_PROXY_KEY)
    get_settings().am_base_url = ((row.value if row and row.value else _ENV_PROXY) or "").rstrip("/")


async def am_enabled(db: AsyncSession) -> bool:
    """集成中心开关；未开启前面板与团队/资产选择一律不暴露。"""
    row = await db.get(AppSetting, AM_ENABLED_KEY)
    return bool(row and row.value == "1")


async def _rpc(method: str, body: dict, key: str, base: str) -> dict:
    headers = {
        "X-Tdai-Service-Id": get_settings().am_space_id,
        "X-Tdai-User-Key": key,
    }
    try:
        # hub 是内网服务，别走系统 HTTP 代理（mihomo 转发该请求直接回空 502）
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            r = await client.post(
                f"{base}/api/v1/meta/{method}",
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


@router.get("/status")
async def am_status(
    _me: Person = Depends(current_person), db: AsyncSession = Depends(get_db)
) -> dict:
    """前端据此隐藏顶栏入口和所有团队/资产选择。"""
    return {"enabled": await am_enabled(db)}


@router.get("/panel-session")
async def panel_session(
    response: Response,
    me: Person = Depends(current_person),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """面板免登：本人绑定过 am user_key 才下发，未绑定只返回入口信息（面板自行登录）。

    不回落平台公共 key：那会让所有未绑定的人以同一个身份登进面板。
    """
    s = get_settings()
    response.headers["Cache-Control"] = "no-store"
    if not await am_enabled(db):
        return {"enabled": False, "bound": False, "instance_id": s.am_space_id}
    if not me.am_user_key_enc:
        return {"bound": False, "instance_id": s.am_space_id}
    key = am_user_key(me)
    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            r = await client.post(
                f"{await hub_base_url(db)}/api/v1/meta/auth/verify",
                json={"user_key": key},
                headers={"X-Tdai-Service-Id": s.am_space_id},
            )
        payload = (r.json() or {}).get("data") or {}
    except (httpx.HTTPError, ValueError):
        return {"bound": False, "instance_id": s.am_space_id}
    if r.status_code != 200 or not payload.get("valid") or not payload.get("user"):
        return {"bound": False, "instance_id": s.am_space_id}
    return {
        "bound": True,
        "instance_id": s.am_space_id,
        "user_key": key,
        "user": payload["user"],
    }


@router.get("/teams")
async def list_teams(
    me: Person = Depends(current_person), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """团队下拉：当前登录用户在 agent-memory 里可见的团队。"""
    if not await am_enabled(db):
        return []
    # hub 校验 limit<=100，超了直接 400；团队量级小，一页足够
    data = await _rpc("team/list", {"limit": 100, "offset": 0}, am_user_key(me), await hub_base_url(db))
    return [
        {"id": t.get("team_id", ""), "name": t.get("name", ""), "description": t.get("description", "")}
        for t in data.get("items", [])
    ]


async def am_names(
    db: AsyncSession, me: Person | None, team_id: str | None, agent_id: str | None
) -> tuple[str | None, str | None]:
    """团队/专家 id → 展示名，供对话 prompt 里写「绑定：团队 X，专家 Y」。

    hub 不可达（或 AM 未启用）时返回 None，调用方退回 id；网络问题不能挡住发消息。
    """
    if not (team_id or agent_id) or not await am_enabled(db):
        return None, None
    key = am_user_key(me)
    base = await hub_base_url(db)
    tname = aname = None
    try:
        if team_id:
            teams = await _rpc("team/list", {"limit": 100, "offset": 0}, key, base)
            tname = next(
                (t.get("name") for t in teams.get("items", []) if t.get("team_id") == team_id), None
            )
        if team_id and agent_id:
            agents = await _rpc(
                "agent/list",
                {"team_id": team_id, "status": "active", "limit": 100, "offset": 0},
                key,
                base,
            )
            aname = next(
                (a.get("name") for a in agents.get("items", []) if a.get("agent_id") == agent_id),
                None,
            )
    except HTTPException:  # _rpc 把连不上/非 JSON 都抛成 502
        return None, None
    return tname, aname


@router.get("/agents")
async def list_agents(
    team_id: str, me: Person = Depends(current_person), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """专家下拉：指定团队下的 active 专家。"""
    if not await am_enabled(db):
        return []
    data = await _rpc(
        "agent/list",
        {"team_id": team_id, "status": "active", "limit": 100, "offset": 0},
        am_user_key(me),
        await hub_base_url(db),
    )
    return [
        {"id": a.get("agent_id", ""), "name": a.get("name", ""), "description": a.get("description", "")}
        for a in data.get("items", [])
    ]


# ── 面板反代 ────────────────────────────────────────────────────────────
# nginx 把 /am/、/api/v1/、面板静态资源都转到本服务，这里再按配置的 hub 地址回源。
# 逐跳头不能转发；上游压缩后 httpx 会解码，Content-Encoding/Content-Length 必须去掉。

_HOP_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "content-encoding"}


async def _forward(request: Request, db: AsyncSession, path: str) -> StreamingResponse:
    base = await hub_base_url(db)
    url = f"{base}/{path.lstrip('/')}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
    client = httpx.AsyncClient(timeout=600.0, trust_env=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=await request.body()),
            stream=True,
        )
    except httpx.HTTPError as e:
        await client.aclose()
        raise HTTPException(502, f"连不上 agent-memory：{e}") from e
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_HEADERS}

    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=resp_headers)


_PANEL_PAGE = ["GET", "HEAD"]
_PANEL_API = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


async def _forward_enabled(request: Request, db: AsyncSession, path: str) -> StreamingResponse:
    if not await am_enabled(db):
        raise HTTPException(404, "agent-memory 未启用")
    return await _forward(request, db, path)


@panel_router.api_route("/am", methods=_PANEL_PAGE)
@panel_router.api_route("/am/", methods=_PANEL_PAGE)
@panel_router.api_route("/am/{path:path}", methods=_PANEL_PAGE)
async def panel_page(request: Request, path: str = "", db: AsyncSession = Depends(get_db)):
    return await _forward_enabled(request, db, path)


@panel_router.api_route("/api/v1/{path:path}", methods=_PANEL_API)
async def panel_api(request: Request, path: str, db: AsyncSession = Depends(get_db)):
    return await _forward_enabled(request, db, f"api/v1/{path}")


@panel_router.api_route("/assets/{path:path}", methods=_PANEL_PAGE)
async def panel_asset(request: Request, path: str, db: AsyncSession = Depends(get_db)):
    return await _forward_enabled(request, db, f"assets/{path}")


@panel_router.api_route("/logo.png", methods=_PANEL_PAGE)
async def panel_logo(request: Request, db: AsyncSession = Depends(get_db)):
    return await _forward_enabled(request, db, "logo.png")
