"""集成中心：全局 webhook 凭证、GitLab 事件 / 通知 / MR 评审历史查询。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import octo
from ..auth import current_person, require_admin
from ..config import get_settings
from ..crypto import encrypt
from ..db import get_db
from ..models import AppSetting, DevGroup, GitlabEvent, MrReview, NotifyLog, Person, System
from .am import (
    AM_ENABLED_KEY,
    AM_PROXY_KEY,
    AM_URL_KEY,
    apply_proxy_url,
    env_proxy_url,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

_OCTO_KEY = "octo_bot_token"
_AI_BALL_KEY = "ai_ball_enabled"


class OctoIn(BaseModel):
    bot_token: str | None = None  # 空 = 清除（回退 .env 的 VEGA_OCTO_BOT_TOKEN）


@router.get("/octo")
async def octo_config(
    db: AsyncSession = Depends(get_db), _me: Person = Depends(require_admin)
) -> dict:
    """全局 bot token 状态。bot 拉群列表只认这一个 token，和研发组无关。"""
    row = await db.get(AppSetting, _OCTO_KEY)
    env = bool(get_settings().octo_bot_token)
    return {
        "api_url": get_settings().octo_api_url,
        "has_token": bool(row and row.value) or env,
        "source": "db" if row and row.value else ("env" if env else None),
    }


@router.put("/octo")
async def set_octo(
    data: OctoIn, db: AsyncSession = Depends(get_db), _me: Person = Depends(require_admin)
) -> dict:
    token = (data.bot_token or "").strip()
    row = await db.get(AppSetting, _OCTO_KEY)
    if token:
        enc = encrypt(get_settings().secret_key, token)
        if row:
            row.value = enc
        else:
            db.add(AppSetting(key=_OCTO_KEY, value=enc))
    elif row:
        await db.delete(row)
    await db.flush()
    octo.set_global_token(token)
    await db.commit()
    env = bool(get_settings().octo_bot_token)
    return {"has_token": bool(token) or env, "source": "db" if token else ("env" if env else None)}


class AmIn(BaseModel):
    url: str | None = None  # 空 = 清除（回退 .env 的 VEGA_AM_HUB_BASE_URL）
    proxy_url: str | None = None  # 空 = 清除（回退 .env 的 VEGA_AM_BASE_URL）
    enabled: bool | None = None  # 缺省 = 不改开关


async def _am_config(db: AsyncSession) -> dict:
    row = await db.get(AppSetting, AM_URL_KEY)
    prow = await db.get(AppSetting, AM_PROXY_KEY)
    on = await db.get(AppSetting, AM_ENABLED_KEY)
    return {
        "url": row.value if row and row.value else get_settings().am_hub_base_url,
        "source": "db" if row and row.value else "env",
        "proxy_url": prow.value if prow and prow.value else env_proxy_url(),
        "proxy_source": "db" if prow and prow.value else "env",
        "enabled": bool(on and on.value == "1"),
    }


@router.get("/am")
async def am_config(
    db: AsyncSession = Depends(get_db), _me: Person = Depends(require_admin)
) -> dict:
    """agent-memory 面板/代理地址与启用开关：反代、免登校验、团队/资产选择、LLM 流量都读它。"""
    return await _am_config(db)


async def _set_url(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(AppSetting, key)
    if value:
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
    elif row:
        await db.delete(row)


@router.put("/am")
async def set_am(
    data: AmIn, db: AsyncSession = Depends(get_db), _me: Person = Depends(require_admin)
) -> dict:
    url = (data.url or "").strip().rstrip("/")
    proxy = (data.proxy_url or "").strip().rstrip("/")
    for value, label in ((url, "地址"), (proxy, "代理地址")):
        if value and not value.startswith(("http://", "https://")):
            raise HTTPException(400, f"{label}需以 http:// 或 https:// 开头")
    if data.enabled and not (url or get_settings().am_hub_base_url.strip()):
        raise HTTPException(400, "启用前请先填写面板地址")
    if data.url is not None:
        await _set_url(db, AM_URL_KEY, url)
    if data.proxy_url is not None:
        await _set_url(db, AM_PROXY_KEY, proxy)
    if data.enabled is not None:
        on = await db.get(AppSetting, AM_ENABLED_KEY)
        if data.enabled and not on:
            db.add(AppSetting(key=AM_ENABLED_KEY, value="1"))
        elif data.enabled:
            on.value = "1"
        elif on:
            await db.delete(on)
    await db.commit()
    await apply_proxy_url(db)
    return await _am_config(db)


class AiBallIn(BaseModel):
    enabled: bool


@router.get("/ai-ball")
async def ai_ball_config(
    db: AsyncSession = Depends(get_db), _me: Person = Depends(current_person)
) -> dict:
    """侧栏 AI 表情球开关。默认开，只有显式存过 "0" 才关。"""
    row = await db.get(AppSetting, _AI_BALL_KEY)
    return {"enabled": not row or row.value != "0"}


@router.put("/ai-ball")
async def set_ai_ball(
    data: AiBallIn, db: AsyncSession = Depends(get_db), _me: Person = Depends(require_admin)
) -> dict:
    row = await db.get(AppSetting, _AI_BALL_KEY)
    if data.enabled:
        if row:
            await db.delete(row)  # 默认开，删掉即恢复
    elif row:
        row.value = "0"
    else:
        db.add(AppSetting(key=_AI_BALL_KEY, value="0"))
    await db.commit()
    return {"enabled": data.enabled}


@router.get("/octo-groups")
async def octo_groups(_me: Person = Depends(require_admin)) -> dict:
    """全局 bot 所在群列表——研发组新建时不用先保存就能选群。"""
    items, err = octo.list_groups()
    return {"items": items, "error": err}


async def _paged(db: AsyncSession, stmt, order_col, page: int, size: int) -> tuple[list, int]:
    total = (await db.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (await db.scalars(stmt.order_by(order_col.desc()).offset((page - 1) * size).limit(size))).all()
    return rows, total


@router.get("/summary")
async def summary(
    db: AsyncSession = Depends(get_db), _me: Person = Depends(require_admin)
) -> dict:
    base = get_settings().api_url.rstrip("/")
    groups = (await db.scalars(select(DevGroup).order_by(DevGroup.id))).all()
    return {
        "webhook_url": f"{base}/webhooks/gitlab",
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "webhook_secret": g.webhook_secret,
                "has_octo_bot_token": bool(g.octo_bot_token_enc),
            }
            for g in groups
        ],
    }


@router.get("/events")
async def list_events(
    group_id: int | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _me: Person = Depends(current_person),
) -> dict:
    stmt = select(GitlabEvent)
    if group_id:
        stmt = stmt.where(GitlabEvent.group_id == group_id)
    term = (q or "").strip().lower()
    if term:
        # payload 是 JSON，跨库按文本查不通用；事件检索只按事件类型匹配
        stmt = stmt.where(func.lower(GitlabEvent.event_type).contains(term, autoescape=True))
    rows, total = await _paged(db, stmt, GitlabEvent.id, page, page_size)
    items = [
        {
            "id": e.id,
            "group_id": e.group_id,
            "event_type": e.event_type,
            "payload": e.payload,
            "handled_at": str(e.handled_at) if e.handled_at else None,
        }
        for e in rows
    ]
    return {"items": items, "total": total}


@router.get("/notify-logs")
async def list_notify_logs(
    group_id: int | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _me: Person = Depends(current_person),
) -> dict:
    stmt = select(NotifyLog)
    if group_id:
        stmt = stmt.where(NotifyLog.group_id == group_id)
    term = (q or "").strip().lower()
    if term:
        stmt = stmt.where(
            func.lower(NotifyLog.content).contains(term, autoescape=True)
            | func.lower(NotifyLog.reason).contains(term, autoescape=True)
        )
    rows, total = await _paged(db, stmt, NotifyLog.id, page, page_size)
    return {
        "items": [
            {
                "id": n.id,
                "group_id": n.group_id,
                "reason": n.reason,
                "ok": n.ok,
                "content": n.content,
                "mentioned_uids": n.mentioned_uids,
                "created_at": str(n.created_at or ""),
            }
            for n in rows
        ],
        "total": total,
    }


@router.get("/mr-reviews")
async def list_mr_reviews(
    group_id: int | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _me: Person = Depends(current_person),
) -> dict:
    stmt = select(MrReview, System).join(System, MrReview.system_id == System.id)
    if group_id:
        stmt = stmt.where(MrReview.group_id == group_id)
    term = (q or "").strip().lower()
    if term:
        stmt = stmt.where(
            or_(
                func.lower(MrReview.title).contains(term, autoescape=True),
                func.lower(System.path).contains(term, autoescape=True),
            )
        )
    total = (await db.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await db.execute(
            stmt.order_by(MrReview.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = [
        {
            "id": r.id,
            "project_path": p.path,
            "mr_iid": r.mr_iid,
            "title": r.title,
            "head_sha": r.head_sha,
            "verdict": r.verdict,
            "gate": r.gate,
            "flow_state": r.flow_state,
            "summary": r.summary,
            "changed_files": r.changed_files,
            "created_at": str(r.created_at or ""),
        }
        for r, p in rows
    ]
    return {"items": items, "total": total}
