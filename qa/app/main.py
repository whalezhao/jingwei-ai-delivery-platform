import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .ai_review import assess_loop
from .api import (
    ai,
    batches,
    cases,
    environments,
    features,
    preflight,
    requirement_tests,
    runs,
    settings,
    stats,
    systems,
    task_runs,
    tasks,
    workers,
)
from .config import get_settings
from .db import engine
from .migrate import run_migrations
from .model_health import model_health_loop
from .retention import cleanup_loop

log = logging.getLogger("api")
# uvicorn 只配置自己的 logger，root 的级别/处理器不归我们管；直接给 api 这个
# logger 挂一个 handler，请求日志和后台协程的 info 才稳定可见。
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
if not log.handlers:
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

# 后台协程（retention / ai_review）用的是各自的 logger，走 root；
# root 默认 WARNING，不调级别的话它们的 info 也会被吞掉。
_root = logging.getLogger()
if not _root.handlers:
    _root.addHandler(_handler)
_root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # schema 的唯一来源是 migrations/ —— 启动先升到 head，不再用 create_all
    await asyncio.to_thread(run_migrations)
    s = get_settings()
    if len(s.vega_secret_key) < 32:
        log.warning("VEGA_SECRET_KEY 未设置或过短，Vega token 将无法校验")
    if s.environment_secret_key == "dev-environment-key-change-me":
        log.warning("未设置 ENVIRONMENT_SECRET_KEY，正在使用开发默认值")
    # AI 评估：worker 只入队，这里轮询认领、调模型
    assessor = asyncio.create_task(assess_loop())
    # 执行产物按保留策略定期清理（启动先清一次）
    janitor = asyncio.create_task(cleanup_loop())
    # 模型健康巡检：默认 30 分钟一次，结果供系统配置与预检使用
    health = asyncio.create_task(model_health_loop())
    yield
    assessor.cancel()
    janitor.cancel()
    health.cancel()
    await engine.dispose()


app = FastAPI(title="Vega QA", lifespan=lifespan)

_origins = [item.strip() for item in get_settings().cors_origins.split(",") if item.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(systems.router)
app.include_router(features.router)
app.include_router(cases.router)
app.include_router(tasks.router)
app.include_router(task_runs.router)
app.include_router(requirement_tests.router)
app.include_router(runs.router)
app.include_router(batches.router)
app.include_router(stats.router)
app.include_router(ai.router)
app.include_router(settings.router)
app.include_router(environments.router)
app.include_router(preflight.router)
app.include_router(workers.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """每个请求带一个 id：日志、响应头、报错都能串起来。"""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "请求异常 id=%s %s %s", request_id, request.method, request.url.path
        )
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    # 报告是 iframe 里的大文件，不刷请求日志
    if not request.url.path.startswith("/reports"):
        log.info(
            "%s %s %s %.0fms id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
    return response


@app.get("/health")
async def health():
    """给监控用：数据库不可用时返回 503，而不是假装健康。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        log.exception("健康检查：数据库不可用")
        return JSONResponse({"status": "degraded", "db": "down"}, status_code=503)
    return {"status": "ok", "db": "ok"}