"""启动时把数据库升级到最新 revision。

之前用 `Base.metadata.create_all` 起步，改表靠手工 ALTER —— 模型和线上库迟早漂移。
现在 schema 的唯一来源是 `migrations/versions/`，启动时先 upgrade head 再提供服务。

注意：Alembic 的 async 模板内部会 `asyncio.run()`，不能在已有事件循环里直接调，
所以调用方要用 `asyncio.to_thread(run_migrations)`。
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def run_migrations() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
