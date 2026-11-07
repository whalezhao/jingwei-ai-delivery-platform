from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 与 Vega 共库：qa_* 表是本服务的，person/dev_group/dev_system/requirement 只读
    database_url: str = "postgresql+asyncpg://qa:qa@localhost:5432/vega"

    # 校验 Vega 的 HMAC token（必须与 Vega 的 VEGA_SECRET_KEY 完全一致）
    vega_secret_key: str = ""

    # 逗号分隔。Vega web 的开发/生产 origin。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    runner_dir: str = "runner"
    case_timeout_ms: int = 600_000

    # 执行产物清理：后台协程每 24 小时跑一次
    artifact_cleanup_enabled: bool = True
    artifact_retention_days: int = 30
    artifact_keep_per_task: int = 5

    # worker 心跳超时：超过这个时间没心跳就视为失联，API 的 worker 状态页也用它
    worker_stale_seconds: int = 90

    # 模型健康巡检：默认 30 分钟一次，避免频繁消耗 token
    model_health_enabled: bool = True
    model_health_interval_seconds: int = 1800
    model_health_timeout_seconds: float = 20.0

    # 环境变量密钥。生产必须设置；开发默认值只用于本机联调。
    environment_secret_key: str = "dev-environment-key-change-me"
    # 通知里拼报告链接用；留空则只发相对路径
    public_base_url: str = ""

    @property
    def runner_path(self) -> Path:
        p = Path(self.runner_dir)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()