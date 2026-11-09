"""接口的输入/输出形状。

系统、人员、需求都是 Vega 的表：这里只出现它们的整数 id，不复制人员资料。
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ORM = ConfigDict(from_attributes=True)


class In(BaseModel):
    """所有写入模型的基类：拒绝未声明的字段，避免前端塞进意外字段。"""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------- 系统配置


class SystemHealthOut(BaseModel):
    model_config = ORM
    status: str
    message: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    checked_at: datetime | None = None
    source: str | None = None


class SystemOut(BaseModel):
    model_config = ORM
    id: int
    group_id: int
    name: str
    path: str
    # 系统是否已配置自己的大模型。没配就不允许执行该系统的用例。
    has_model_config: bool = False
    model_health: SystemHealthOut | None = None


class ModelConfigOut(BaseModel):
    """系统级模型配置。

    **绝不包含 api_key 本身**，只给 api_key_set 表示是否已配。
    """

    configured: bool = False
    # 还缺哪些必填项的中文名，用于界面直接说清楚「差什么」
    missing: list[str] = []
    model_name: str | None = None
    model_family: str | None = None
    base_url: str | None = None
    init_config_json: str | None = None
    api_key_set: bool = False
    # None = 跟随全局
    reasoning_enabled: bool | None = None
    # 每月 token 限额；None/0 = 不限
    token_quota: int | None = None
    # 本自然月已用（执行器 + 平台侧）
    month_tokens_used: int = 0
    model_health: SystemHealthOut | None = None


class ModelConfigIn(In):
    """逐字段覆盖：留空的字段继续用全局配置。

    api_key 用 clear_api_key 显式清除；reasoning_enabled 三态：不传=不改，
    clear_reasoning=恢复跟随全局，true/false=显式设置。
    """

    model_name: str | None = None
    model_family: str | None = None
    base_url: str | None = None
    init_config_json: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    reasoning_enabled: bool | None = None
    clear_reasoning: bool = False
    token_quota: int | None = Field(default=None, ge=0)
    clear_token_quota: bool = False


class ModelConfigTestIn(In):
    """测试连接：留空的字段用已保存的配置，方便保存前先试。"""

    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ModelConfigTestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: int = 0


# --------------------------------------------------------------------- 功能 / 用例


class FeatureOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    system_id: int
    name: str
    description: str | None
    requirement_id: int | None
    stage: str
    released_at: datetime | None


class FeatureCreate(In):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    requirement_id: int | None = None


class FeatureUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    requirement_id: int | None = None


class TestCaseOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    feature_id: uuid.UUID
    name: str
    description: str | None
    yaml_content: str
    priority: str | None
    defect_ids: str | None
    stage_override: str | None
    is_active: bool
    effective_stage: str
    # 用例本身只知道 feature，带上 system_id 省一次来回
    system_id: int | None = None
    # 最新版本号；前端版本历史入口用
    version_no: int | None = None


class TestCaseCreate(In):
    name: str = Field(min_length=1, max_length=128)
    yaml_content: str = Field(min_length=1)
    description: str | None = None
    priority: str | None = None
    defect_ids: str | None = Field(default=None, max_length=255)
    stage_override: str | None = None
    is_active: bool = True
    change_source: str | None = None
    change_note: str | None = None


class TestCaseUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    yaml_content: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority: str | None = None
    defect_ids: str | None = Field(default=None, max_length=255)
    stage_override: str | None = None
    is_active: bool | None = None
    change_source: str | None = None
    change_note: str | None = None


class YamlValidateIn(In):
    yaml_content: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CaseDraftIn(In):
    """对话历史由前端保存并整段回传，后端不存会话状态。"""

    messages: list[ChatMessage] = Field(min_length=1)
    current_yaml: str | None = None


class CaseDraftOut(BaseModel):
    reply: str
    yaml: str | None = None
    yaml_ok: bool = False
    yaml_errors: list[str] = []
    usage: dict = {}


class YamlValidateOut(BaseModel):
    ok: bool
    errors: list[str] = []
    case_count: int = 0


class CaseVersionOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    case_id: uuid.UUID
    version_no: int
    name: str
    description: str | None = None
    yaml_content: str
    priority: str | None = None
    defect_ids: str | None = None
    stage_override: str | None = None
    is_active: bool
    change_source: str
    change_note: str | None = None
    created_by: int | None = None
    created_at: datetime


class CaseDiffOut(BaseModel):
    from_version: int
    to_version: int
    diff: str


class AiFixIn(In):
    run_id: uuid.UUID | None = None


class AiFixOut(BaseModel):
    run_id: uuid.UUID
    proposed_yaml: str
    explanation: str
    diff: str
    yaml_ok: bool
    yaml_errors: list[str] = []
    model_name: str | None = None


# --------------------------------------------------------------------- 任务 / 执行


class TaskOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    system_id: int
    requirement_id: int | None
    name: str
    type: str
    description: str | None
    status: str
    retry_count: int
    environment_name: str | None = None
    case_selection_mode: str
    created_by: int | None = None
    created_at: datetime | None = None
    # 由列表接口聚合填充
    feature_count: int = 0
    case_count: int = 0
    last_batch_id: uuid.UUID | None = None
    last_batch_status: str | None = None
    last_run_at: datetime | None = None
    # 跨系统列表（GET /api/tasks）批量填充；系统内列表不填
    system_name: str | None = None
    group_id: int | None = None
    requirement_code: str | None = None
    requirement_title: str | None = None
    created_by_name: str | None = None


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int


class TaskCreate(In):
    """一个任务就是一堆功能的组合；feature_ids 连同其下启用中的用例一起快照。"""

    name: str = Field(min_length=1, max_length=128)
    type: str = "case_run"
    description: str | None = None
    requirement_id: int | None = None
    retry_count: int = Field(default=0, ge=0, le=5)
    environment_name: str | None = Field(default=None, max_length=64)
    feature_ids: list[uuid.UUID] = []
    run_now: bool = False


class TaskUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    retry_count: int | None = Field(default=None, ge=0, le=5)
    environment_name: str | None = Field(default=None, max_length=64)


class TaskRunIn(In):
    """勾选多个环境时，每个环境各建一条执行记录。"""

    environment_names: list[str] = []


class TaskPreflightIn(In):
    environment_name: str | None = Field(default=None, max_length=64)


class CaseIdList(In):
    case_ids: list[uuid.UUID] = []


class TaskIdList(In):
    task_ids: list[uuid.UUID] = []


class FeatureIdList(In):
    feature_ids: list[uuid.UUID] = []


class CaseResultOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    test_case_id: uuid.UUID | None
    source_path: str | None
    name: str | None
    case_index: int
    status: str
    attempt_count: int
    passed_after_retry: bool


class RunOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    task_id: uuid.UUID | None
    batch_id: uuid.UUID | None = None
    system_id: int
    status: str
    midscene_status: str | None
    exit_code: int | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_ms: int | None
    total: int | None
    passed: int | None
    failed: int | None
    not_run: int | None
    passed_after_retry: int | None
    final_pass_rate: float | None
    first_pass_rate: float | None
    model_name: str | None
    error_message: str | None
    failure_kind: str | None = None
    environment_id: uuid.UUID | None = None
    environment_name: str | None = None
    environment_base_url: str | None = None


class RunProgressOut(BaseModel):
    done: int = 0
    total: int = 0
    percent: int = 0


class RunDetailOut(RunOut):
    work_dir: str | None = None
    report_available: bool = False
    ai_calls: int = 0
    ai_total_tokens: int = 0
    ai_cost_ms: int = 0
    progress: RunProgressOut = Field(default_factory=RunProgressOut)


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class BatchFeatureOut(BaseModel):
    feature_id: str
    name: str
    stage: str
    total: int
    passed: int
    failed: int


class BatchOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID | None = None
    task_name: str | None = None
    task_type: str | None = None
    task_status: str | None = None
    task_feature_count: int = 0
    task_case_count: int = 0
    ai_status: str | None = None
    ai_verdict: str | None = None
    system_id: int
    name: str
    trigger: str
    environment_name: str | None = None
    parent_batch_id: uuid.UUID | None = None
    created_at: datetime
    created_by_name: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = 0
    runs: int = 0
    total: int = 0
    passed: int = 0
    failed: int = 0
    excluded_runs: int = 0
    status: str
    features: list[BatchFeatureOut] = []


class BatchRunOut(BaseModel):
    id: uuid.UUID
    system_id: int | None = None
    system_name: str | None = None
    status: str
    total: int | None = None
    passed: int | None = None
    failed: int | None = None
    duration_ms: int | None = None
    model_name: str | None = None
    failure_kind: str | None = None
    environment_name: str | None = None
    report_available: bool = False
    started_at: datetime | None = None
    shard_index: int | None = None
    shard_count: int | None = None


class BatchDetailOut(BatchOut):
    run_items: list[BatchRunOut] = []


class BatchListOut(BaseModel):
    items: list[BatchOut]
    total: int


# --------------------------------------------------------------------- 需求测试


class RequirementRunIn(In):
    environment_name: str | None = Field(default=None, max_length=64)


class RequirementTestSummaryOut(BaseModel):
    requirement_id: int
    # none / queued / running / completed / completed_with_failures / failed / cancelled
    status: str = "none"
    task_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    not_run: int = 0
    running: int = 0
    pass_rate: float | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime | None = None


# --------------------------------------------------------------------- 环境 / 设置


class EnvironmentVariableOut(BaseModel):
    id: uuid.UUID
    key: str
    value: str | None = None
    is_secret: bool
    value_set: bool
    description: str | None = None


class EnvironmentOut(BaseModel):
    model_config = ORM
    id: uuid.UUID
    system_id: int
    name: str
    base_url: str | None
    is_default: bool
    variables: list[EnvironmentVariableOut] = []


class EnvironmentIn(In):
    name: str = Field(min_length=1, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)


class EnvironmentUpdate(In):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)


class EnvironmentVariableIn(In):
    key: str = Field(min_length=1, max_length=64)
    value: str | None = None
    is_secret: bool = False
    description: str | None = Field(default=None, max_length=255)
    clear: bool = False


class EnvironmentVariablesIn(In):
    variables: list[EnvironmentVariableIn] = []


class SettingsOut(BaseModel):
    max_concurrent_runs: int
    run_shard_size: int
    ai_assess_failures_only: bool = False
    updated_at: datetime | None = None


class SettingsIn(In):
    """并发是全局上限；分片大小 0 表示一个系统一个 run，不分片。"""

    max_concurrent_runs: int | None = Field(default=None, ge=1, le=32)
    run_shard_size: int | None = Field(default=None, ge=0, le=50)
    ai_assess_failures_only: bool | None = None


class AiCheckOut(BaseModel):
    """AI 结论的统一形状：执行评估 / 任务预检。"""

    status: str
    verdict: str | None = None
    summary: str | None = None
    reasons: list[str] = []
    advice: list[str] = []
    diagnoses: list[dict] = []
    model_name: str | None = None
    error: str | None = None
    created_at: datetime | None = None


class PreflightCheckOut(BaseModel):
    key: str
    ok: bool
    severity: str
    message: str
    overridable: bool = False
    system_id: int | None = None
    system_name: str | None = None


class PreflightOut(BaseModel):
    checks: list[PreflightCheckOut]
    can_run: bool
    environment_name: str


class WorkerOut(BaseModel):
    id: str
    hostname: str | None = None
    pid: int | None = None
    slots: int
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    age_seconds: int = 0
    active_runs: int = 0


class WorkerStatusOut(BaseModel):
    workers: list[WorkerOut]
    stale_workers: int = 0
    queued_runs: int = 0
    active_runs: int = 0
    max_concurrent_runs: int = 4
    worker_stale_seconds: int = 90
    avg_duration_ms: int | None = None
    oldest_queue_seconds: int | None = None


RunDetailOut.model_rebuild()