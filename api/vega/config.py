"""织女全局配置（环境变量前缀 VEGA_，见 pydantic-settings）。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VEGA_", env_file=".env", extra="ignore")

    # dev 默认 SQLite；部署 内网主机 时切 postgresql+asyncpg://…
    database_url: str = "sqlite+aiosqlite:///./vega.db"

    # Fernet 密钥与登录 token 签名都从它派生，部署时必须换
    secret_key: str = "dev-secret-change-me"

    # 平台管理员（首次启动自动建 person.is_admin=True）
    admin_username: str = "admin"
    admin_password: str = "vega"

    # agent-memory proxy（所有 LLM 流量的统一出口）
    am_base_url: str = "http://127.0.0.1:8096"
    # agent-memory hub（面板/管理 API：团队与专家列表）
    am_hub_base_url: str = "http://127.0.0.1:8125"
    am_space_id: str = "default"
    am_user_key: str = ""  # 平台公共业务用户 user_key（sk-mem-…），人员未绑定 am_user_key 时兜底
    # 兜底层模型（person/group 均未配置时）：am proxy 不映射 claude 原生模型名，
    # 必须显式给它能认的名字，否则 404 model not found
    am_default_model: str = "Qwen3.8-27B"
    # 兜底思考等级：人员/组都没选时。claude 默认 high，但 Qwen 只认 xhigh/medium/low
    am_default_effort: str = "xhigh"
    # claude 默认 max_tokens 128000 会超 Qwen 128144 总上下文
    claude_max_output_tokens: int = 32000
    claude_max_context_tokens: int = 128144  # Qwen3.8-27B 总上下文；不声明时 CLI 拒识自定义模型名

    # octo bot API。地址给默认值：用户只需要在集成中心贴一个 bot token 就能拉群列表。
    # token 留空则通知全部记 notify_log 不外发。
    octo_api_url: str = "https://octo.example.com"
    octo_bot_token: str = ""  # 兜底：集成中心录入的全局 token 优先

    # claude CLI（测试时指向 stub）
    claude_bin: str = "claude"

    # runner 进程自身
    runner_name: str = ""
    runner_home: str = ""  # 空 = ~/vega-runner；测试/部署可指到别的目录
    # 项目常驻仓库统一根目录（空 = ~/vega-workspace）；worktree/docs 仍在 runner_home
    workspace_home: str = ""
    # CRG 向量：内网 OpenAI 兼容嵌入服务（llama-server，qwen3-embedding 原生 1024 维，不校验模型名）
    crg_embedding_base_url: str = "http://127.0.0.1:18083/v1"
    crg_embedding_model: str = "qwen3-embedding-0.6b-q8_0.gguf"
    crg_embedding_api_key: str = "local"  # 服务不鉴权，CRG 只要求非空
    api_url: str = "http://127.0.0.1:8181"
    runner_token: str = ""  # 由管理员在界面创建节点后发放


@lru_cache
def get_settings() -> Settings:
    return Settings()
