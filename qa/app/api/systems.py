"""系统（Vega dev_system）的测试配置。

系统本身由 Vega 维护，这里只提供「测试域」视角：模型配置、健康、预检。
"""

import time
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..ai_case import call_model
from ..deps import CurrentPerson, DbSession, require_system_access
from ..model_health import describe_model_error
from ..modelconfig import missing_fields
from ..models import (
    System,
    SystemModelConfig,
    SystemModelHealth,
)
from ..preflight import run_system_preflight
from ..quota import record_usage, system_month_usage
from ..schemas import (
    ModelConfigIn,
    ModelConfigOut,
    ModelConfigTestIn,
    ModelConfigTestOut,
    PreflightOut,
    SystemHealthOut,
    SystemOut,
)

router = APIRouter(prefix="/api/systems", tags=["systems"])


def _health_out(row: SystemModelHealth | None) -> SystemHealthOut | None:
    return SystemHealthOut.model_validate(row) if row is not None else None


def _system_out(
    system: System,
    config: SystemModelConfig | None,
    health: SystemModelHealth | None,
) -> SystemOut:
    return SystemOut(
        id=system.id,
        group_id=system.group_id,
        name=system.name,
        path=system.path,
        has_model_config=not missing_fields(config),
        model_health=_health_out(health),
    )


async def _system_or_404(db, system_id: int) -> System:
    system = await db.get(System, system_id)
    if system is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "系统不存在")
    return system


@router.get("", response_model=list[SystemOut])
async def list_systems(user: CurrentPerson, db: DbSession) -> list[SystemOut]:
    systems = list((await db.execute(select(System).order_by(System.name))).scalars())
    ids = [s.id for s in systems]
    configs = {
        row.system_id: row
        for row in (
            await db.execute(
                select(SystemModelConfig).where(SystemModelConfig.system_id.in_(ids))
            )
        ).scalars()
    }
    health = {
        row.system_id: row
        for row in (
            await db.execute(
                select(SystemModelHealth).where(SystemModelHealth.system_id.in_(ids))
            )
        ).scalars()
    }
    return [
        _system_out(system, configs.get(system.id), health.get(system.id))
        for system in systems
    ]


@router.get("/{system_id}", response_model=SystemOut)
async def get_system(
    system_id: int, user: CurrentPerson, db: DbSession
) -> SystemOut:
    await require_system_access(db, system_id, user)
    system = await _system_or_404(db, system_id)
    return _system_out(
        system,
        await db.get(SystemModelConfig, system_id),
        await db.get(SystemModelHealth, system_id),
    )


async def _config_out(db, system_id: int) -> ModelConfigOut:
    config = await db.get(SystemModelConfig, system_id)
    return ModelConfigOut(
        configured=not missing_fields(config),
        missing=missing_fields(config),
        model_name=config.model_name if config else None,
        model_family=config.model_family if config else None,
        base_url=config.base_url if config else None,
        init_config_json=config.init_config_json if config else None,
        api_key_set=bool(config and config.api_key),
        reasoning_enabled=config.reasoning_enabled if config else None,
        token_quota=config.token_quota if config else None,
        month_tokens_used=await system_month_usage(db, system_id),
        model_health=_health_out(await db.get(SystemModelHealth, system_id)),
    )


@router.get("/{system_id}/model-config", response_model=ModelConfigOut)
async def get_model_config(
    system_id: int, user: CurrentPerson, db: DbSession
) -> ModelConfigOut:
    await require_system_access(db, system_id, user)
    await _system_or_404(db, system_id)
    return await _config_out(db, system_id)


@router.put("/{system_id}/model-config", response_model=ModelConfigOut)
async def update_model_config(
    system_id: int,
    body: ModelConfigIn,
    user: CurrentPerson,
    db: DbSession,
) -> ModelConfigOut:
    await require_system_access(db, system_id, user, "member")
    await _system_or_404(db, system_id)
    row = await db.get(SystemModelConfig, system_id)
    if row is None:
        row = SystemModelConfig(system_id=system_id)
        db.add(row)

    if body.model_name is not None:
        row.model_name = body.model_name.strip() or None
    if body.model_family is not None:
        row.model_family = body.model_family.strip() or None
    if body.base_url is not None:
        row.base_url = body.base_url.strip() or None
    if body.init_config_json is not None:
        row.init_config_json = body.init_config_json.strip() or None
    if body.clear_api_key:
        row.api_key = None
    elif body.api_key:
        row.api_key = body.api_key.strip()
    if body.clear_reasoning:
        row.reasoning_enabled = None
    elif body.reasoning_enabled is not None:
        row.reasoning_enabled = body.reasoning_enabled
    if body.clear_token_quota:
        row.token_quota = None
    elif body.token_quota is not None:
        row.token_quota = body.token_quota or None
    row.updated_by = user.id
    await db.commit()
    return await _config_out(db, system_id)


@router.post("/{system_id}/model-config/test", response_model=ModelConfigTestOut)
async def test_model_config(
    system_id: int,
    body: ModelConfigTestIn,
    user: CurrentPerson,
    db: DbSession,
) -> ModelConfigTestOut:
    """真实发一次最小请求；body 里填了的字段优先，其余用已保存的配置。"""
    await require_system_access(db, system_id, user, "member")
    await _system_or_404(db, system_id)
    saved = await db.get(SystemModelConfig, system_id)
    config = SystemModelConfig(
        system_id=system_id,
        model_name=(body.model_name or (saved.model_name if saved else None)),
        base_url=(body.base_url or (saved.base_url if saved else None)),
        api_key=(body.api_key or (saved.api_key if saved else None)),
        model_family=saved.model_family if saved else None,
        init_config_json=saved.init_config_json if saved else None,
        reasoning_enabled=saved.reasoning_enabled if saved else None,
    )
    gaps = missing_fields(config)
    if gaps:
        return ModelConfigTestOut(ok=False, message=f"还缺：{'、'.join(gaps)}")

    started = time.perf_counter()
    ok, message = True, "连接成功，模型已正常响应"
    try:
        _content, usage = await call_model(
            config, [{"role": "user", "content": "ping"}], timeout=20.0, max_tokens=16
        )
        await record_usage(db, system_id, "model_test", usage)
    except (httpx.HTTPError, ValueError) as exc:
        ok, message = False, describe_model_error(exc, timeout=20.0)
    latency_ms = int((time.perf_counter() - started) * 1000)

    health = await db.get(SystemModelHealth, system_id)
    if health is None:
        health = SystemModelHealth(system_id=system_id)
        db.add(health)
    health.status = "healthy" if ok else "unhealthy"
    health.model_name = config.model_name
    health.latency_ms = latency_ms
    health.message = message
    health.source = "manual"
    health.checked_at = datetime.now(UTC)
    await db.commit()
    return ModelConfigTestOut(ok=ok, message=message, latency_ms=latency_ms)


@router.get("/{system_id}/preflight", response_model=PreflightOut)
async def system_preflight(
    system_id: int,
    user: CurrentPerson,
    db: DbSession,
    environment_name: str | None = None,
) -> dict:
    await require_system_access(db, system_id, user)
    system = await _system_or_404(db, system_id)
    return await run_system_preflight(
        db, system, environment_name=environment_name
    )