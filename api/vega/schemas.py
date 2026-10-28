"""API 出入参模型。加密字段只写不读（响应永不回显 key）。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# claude --effort 全量档位（透传 runner）；Qwen 上游只认 xhigh/medium/low，其余档位走 claude 系/自定义上游
Effort = Literal["low", "medium", "high", "xhigh", "max"]
# 前端「未设置」选项传空串；空串与 null 同义（清除，落到下一级兜底），不能当成非法档位
EffortOpt = Annotated[Effort | None, BeforeValidator(lambda v: v or None)]

# 研发体系角色：product 认领需求池里的需求并定稿需求文档；dev 在定稿后接管做方案与开发。
# pm / tech_lead / qa 目前只作标识与展示，不参与认领门禁
Role = Literal["pm", "product", "tech_lead", "dev", "qa"]


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    person_id: int
    is_admin: bool
    roles: list[str] = Field(default_factory=list)


class PasswordChangeIn(BaseModel):
    old: str
    new: str = Field(min_length=6, max_length=64)


class PersonIn(BaseModel):
    name: str
    # 内置头像 key；只允许小写字母/数字，空串 = 不设置（杜绝任意 URL / 路径穿越）
    avatar: str | None = Field(default=None, pattern=r"^([a-z0-9]{1,32})?$")
    gitlab_username: str | None = None
    gitlab_email: str | None = None
    password: str | None = None
    is_admin: bool = False
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_effort: EffortOpt = None
    am_user_key: str | None = None
    roles: list[Role] = Field(default_factory=list)


class PersonUpd(BaseModel):
    name: str | None = None
    avatar: str | None = Field(default=None, pattern=r"^([a-z0-9]{1,32})?$")
    gitlab_username: str | None = None
    gitlab_email: str | None = None
    password: str | None = None
    is_admin: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_effort: EffortOpt = None
    am_user_key: str | None = None
    octo_uid: str | None = None
    roles: list[Role] | None = None


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    avatar: str | None = None
    gitlab_username: str | None
    gitlab_email: str | None
    octo_uid: str | None
    is_admin: bool
    llm_base_url: str | None
    llm_model: str | None
    llm_effort: str | None
    has_llm_key: bool = False
    has_am_key: bool = False
    roles: list[str] = Field(default_factory=list)


class GroupIn(BaseModel):
    name: str
    gitlab_base_url: str | None = None
    gitlab_token: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_effort: EffortOpt = None
    octo_group_no: str | None = None
    octo_bot_token: str | None = None
    am_team_id: str | None = None
    scan_enabled: bool = True
    auto_webhook: bool = True
    tech_lead_person_ids: list[int] = []


class GroupUpd(GroupIn):
    name: str | None = None  # type: ignore[assignment]
    regenerate_webhook_secret: bool = False


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    gitlab_base_url: str | None
    octo_group_no: str | None
    am_team_id: str | None
    scan_enabled: bool
    auto_webhook: bool
    tech_lead_person_ids: list[int] = []
    llm_base_url: str | None
    llm_model: str | None
    llm_effort: str | None
    has_gitlab_token: bool = False
    has_llm_key: bool = False
    has_octo_bot_token: bool = False


class MemberAdd(BaseModel):
    person_id: int


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    person_name: str = ""
    gitlab_username: str | None = None
    gitlab_email: str | None = None
    octo_uid: str | None
    octo_nickname: str | None
    match_status: str
    match_confidence: float | None
    match_reason: str | None
    manual_lock: bool


class ManualMatchIn(BaseModel):
    octo_uid: str | None = None  # None = 清空解绑


class GitlabMatchIn(BaseModel):
    gitlab_username: str | None = None  # None = 清空解绑


class ProjectIn(BaseModel):
    group_id: int
    gitlab_project_id: int
    path: str
    name: str
    code_prefix: str = Field(min_length=1, max_length=12)
    default_branch: str = "master"
    mr_target_branch: str = "nightly"
    tech_lead_person_ids: list[int] = []
    owner_person_ids: list[int] = []
    audit_prefixes: list[str] | None = None
    auto_approve_prefixes: list[str] | None = None


class ProjectUpd(BaseModel):
    path: str | None = None
    name: str | None = None
    code_prefix: str | None = None
    default_branch: str | None = None
    mr_target_branch: str | None = None
    tech_lead_person_ids: list[int] | None = None
    owner_person_ids: list[int] | None = None
    audit_prefixes: list[str] | None = None
    auto_approve_prefixes: list[str] | None = None
    am_team_id: str | None = None
    am_agent_id: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    gitlab_project_id: int
    path: str
    name: str
    code_prefix: str
    default_branch: str
    mr_target_branch: str
    tech_lead_person_ids: list[int] = []
    owner_person_ids: list[int] = []
    audit_prefixes: list[str] | None = None
    auto_approve_prefixes: list[str] | None = None
    am_team_id: str | None = None
    am_agent_id: str | None = None


class RequirementIn(BaseModel):
    project_id: int
    title: str
    description: str | None = None


class RequirementUpdate(BaseModel):
    """部分字段更新；未传的字段保持原值，传 null 表示清空。"""

    title: str | None = None
    description: str | None = None


class RequirementTagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20)


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    project_name: str | None = None
    code: str
    title: str
    description: str | None
    status: str
    # formal（正式）/ iterating（迭代中）：已上线自动算正式
    iteration_status: str = "iterating"
    tags: list[str] = Field(default_factory=list)
    claimant_id: int | None
    claimant_name: str | None = None
    # 需求提出人：产品认领并定稿后留痕在此，认领权移交开发
    proposer_id: int | None = None
    proposer_name: str | None = None
    plan_md: str | None
    created_at: str | None = None
    # 当前登录人能否打开详情（被他人认领后为 false，列表里只给只读摘要）
    can_open: bool = True
    # 当前登录人此刻能否认领（池中 + 该阶段要求的角色）
    can_claim: bool = False


class PlanMessageIn(BaseModel):
    content: str = Field(min_length=1)
    # 缺省按需求文档是否定稿推导；显式传值必须与当前阶段一致
    mode: Literal["requirement", "plan"] | None = None
    # 输入框里选的团队/专家；空 = 回退项目/研发组配置。每轮随请求头切换
    team_id: str | None = None
    agent_id: str | None = None


class PlanClearIn(BaseModel):
    mode: Literal["requirement", "plan"]


class RequirementDocIn(BaseModel):
    """AI 给出的需求文档正文（已去掉标记，人点「采用为文档」时提交）。"""

    content: str = Field(min_length=1)


class PlanVersionIn(BaseModel):
    """人点「采用此方案」时提交的方案正文（已是标记内的纯 md）。"""

    content: str = Field(min_length=1)


class PlanMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


class DevTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    code: str
    title: str | None = None
    scope: str | None = None
    status: str
    creator_id: int
    branch: str | None
    mr_iid: int | None
    mr_url: str | None = None
    runner_name: str | None = None
    error: str | None
    created_at: str | None = None


class NodeCreateIn(BaseModel):
    name: str


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: str
    last_heartbeat: str | None = None
    running_tasks: int = 0  # 该节点执行中的任务数（= claude 进程数）


class TaskEventIn(BaseModel):
    events: list[dict]


class TaskResultIn(BaseModel):
    status: str  # done / failed
    error: str | None = None
    data: dict = {}


class MatchRunOut(BaseModel):
    stats: dict


class ScanOut(BaseModel):
    projects_added: int = 0
    webhooks_added: int = 0
    error: str | None = None
