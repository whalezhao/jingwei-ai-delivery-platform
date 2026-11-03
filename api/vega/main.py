from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import sessionmaker
from .routers import (
    am,
    auth,
    code_stats,
    groups,
    integrations,
    nodes,
    people,
    projects,
    requirements,
    runner_api,
    setup,
    tasks,
    webhooks,
    workbench,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .auth import bootstrap_admin
    from .db import init_db

    await init_db()
    async with sessionmaker()() as db:
        await bootstrap_admin(db)

        # 集成中心录入的全局 octo bot token：解密后装进进程内缓存
        from . import octo
        from .crypto import decrypt
        from .models import AppSetting

        row = await db.get(AppSetting, "octo_bot_token")
        if row and row.value:
            try:
                octo.set_global_token(decrypt(get_settings().secret_key, row.value))
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).warning("octo 全局 token 解密失败（VEGA_SECRET_KEY 换过？）")

    sched = None
    if not get_settings().database_url.startswith("sqlite") or _scheduler_wanted():
        from .scheduler import start_scheduler

        start_scheduler()
        sched = True
    yield
    if sched:
        from .scheduler import stop_scheduler

        await stop_scheduler()


def _scheduler_wanted() -> bool:
    import os

    if os.environ.get("VEGA_DISABLE_SCHEDULER"):
        return False
    return True


def create_app() -> FastAPI:
    app = FastAPI(title="Vega 织女", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 内网工具；web 与 api 同 nginx 部署后收紧
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (am, auth, people, groups, projects, requirements, tasks, nodes, runner_api, webhooks, integrations, setup, workbench, code_stats):
        app.include_router(r.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # timeout_graceful_shutdown：SSE 是无限流，不设超时的话 SIGTERM 后进程会
    # 永远等连接结束，变成还攥着旧连接发心跳的僵尸 —— 客户端看不出连接已死。
    uvicorn.run(app, host="127.0.0.1", port=8000, timeout_graceful_shutdown=3)
