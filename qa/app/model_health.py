"""模型连通性巡检与错误翻译。"""

import asyncio
import logging
import time

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_case import call_model
from .config import get_settings
from .db import SessionLocal
from .modelconfig import configured_system_ids_stmt, missing_fields
from .models import SystemModelConfig, SystemModelHealth
from .quota import record_usage

log = logging.getLogger("model_health")

_ADVISORY_LOCK = 823754


def describe_model_error(exc: Exception, *, timeout: float) -> str:
    """把测试连接/巡检时的异常翻译成能直接指认问题的中文。"""
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        hint = {
            401: "API Key 被拒绝",
            403: "API Key 没有访问权限",
            404: "接口路径不存在，确认地址是否以 /v1 结尾",
        }.get(status_code, "模型服务返回错误")
        detail = (exc.response.text or "").strip().replace("\n", " ")[:200]
        return f"{hint}（HTTP {status_code}）" + (f"：{detail}" if detail else "")
    if isinstance(exc, httpx.TimeoutException):
        return f"请求超时（{timeout:.0f}s），模型服务或网络不可达"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "对端在返回 HTTP 响应前断开连接，模型服务或网络不可达"
    if isinstance(exc, httpx.TransportError):
        return f"网络连接失败：{exc.__class__.__name__}"
    return f"测试失败：{exc}"


async def _upsert(
    db: AsyncSession,
    system_id: int,
    *,
    status: str,
    model_name: str | None,
    latency_ms: int | None,
    message: str | None,
    source: str,
) -> tuple[SystemModelHealth, str]:
    row = await db.get(SystemModelHealth, system_id)
    previous = row.status if row else "unknown"
    if row is None:
        row = SystemModelHealth(system_id=system_id)
        db.add(row)
    row.status = status
    row.model_name = model_name
    row.latency_ms = latency_ms
    row.message = message
    row.source = source
    return row, previous


async def probe_system(
    db: AsyncSession,
    system_id: int,
    *,
    source: str = "periodic",
    timeout: float | None = None,
    commit: bool = True,
) -> SystemModelHealth:
    """真实发一次最小 chat 请求，并把结果写入 qa_system_model_health。"""
    settings = get_settings()
    timeout = timeout or settings.model_health_timeout_seconds
    config = await db.get(SystemModelConfig, system_id)
    if missing_fields(config):
        row, _ = await _upsert(
            db,
            system_id,
            status="unknown",
            model_name=config.model_name if config else None,
            latency_ms=None,
            message="模型配置不完整",
            source=source,
        )
        if commit:
            await db.commit()
        return row

    started = time.perf_counter()
    try:
        _content, usage = await call_model(
            config,
            [{"role": "user", "content": "ping"}],
            timeout=timeout,
            max_tokens=16,
        )
        await record_usage(db, system_id, "model_health", usage)
        status = "healthy"
        message = "连接成功，模型已正常响应"
    except (httpx.HTTPError, ValueError) as exc:
        log.info("model health check failed for %s: %s", system_id, exc)
        status = "unhealthy"
        message = describe_model_error(exc, timeout=timeout)

    row, previous = await _upsert(
        db,
        system_id,
        status=status,
        model_name=config.model_name,
        latency_ms=int((time.perf_counter() - started) * 1000),
        message=message,
        source=source,
    )
    if commit:
        await db.commit()

    return row


async def _run_once() -> None:
    settings = get_settings()
    if not settings.model_health_enabled:
        return
    async with SessionLocal() as lock_db:
        locked = (
            await lock_db.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK}
            )
        ).scalar()
        if not locked:
            return
        try:
            async with SessionLocal() as db:
                system_ids = list(
                    (await db.execute(configured_system_ids_stmt())).scalars()
                )
            for system_id in system_ids:
                try:
                    async with SessionLocal() as db:
                        await probe_system(db, system_id)
                except Exception:
                    log.exception("model health probe failed for %s", system_id)
        finally:
            await lock_db.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK}
            )


async def model_health_loop() -> None:
    settings = get_settings()
    while True:
        try:
            await _run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("model health loop iteration failed")
        await asyncio.sleep(max(60, settings.model_health_interval_seconds))
