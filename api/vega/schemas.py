"""API 出入参模型。加密字段只写不读（响应永不回显 key）。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# claude --effort 全量档位（透传 runner）；Qwen 上游只认 xhigh/medium/low，其余档位走 claude 系/自定义上游
Effort = Literal["low", "medium", "high", "xhigh", "max"]
# 前端「未设置」选项传空串；空串与 null 同义（清除，落到下一级兜底），不能当成非法档位
EffortOpt = Annotated[Effort | None, BeforeValidator(lambda v: v or None)]

# 研发体系角色：product 认领需求池里的需求并定稿需求文档；dev 在定稿后接管做方案与开发。
# project_lead = 项目总负责人（关注本项目各研发组负责人的汇报，不参与认领门禁）
# pm / tech_lead / qa 目前只作标识与展示，不参与认领门禁
Role = Literal["pm", "product", "tech_lead", "dev", "qa", "project_lead"]

# 能接进方案会话的 EGM 技能；review / review-loop / bootstrap / drift 走独立任务类型，
# 因为它们要么必须零上下文、要么是项目级产物
EGM_SKILL = Literal["brainstorm", "draft", "resolve"]


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
    # Vega 登录名；不填时回落到 gitlab_username（兼容旧导入）
    username: str | None = None
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
    username: str | None = None
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
    username: str | None = None
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
    project_id: int | None = None
    gitlab_base_url: str | None = None
    gitlab_token: str | None = None
    gitlab_bot_username: str | None = None
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
    project_id: int | None = None
    project_name: str | None = None
    gitlab_base_url: str | None
    gitlab_bot_username: str | None = None
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


class MemberAccountIn(BaseModel):
    gitlab_account_id: int | None = None


class GitlabAccountIn(BaseModel):
    base_url: str
    token: str


class GitlabAccountOut(BaseModel):
    id: int
    base_url: str
    gitlab_user_id: int
    username: str
    name: str = ""
    email: str = ""
    has_token: bool = True


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    person_name: str = ""
    gitlab_username: str | None = None
    gitlab_email: str | None = None
    # 本人在本研发组使用的 GitLab 账号（空 = 未绑定，派发开发任务会被拦）
    gitlab_account_id: int | None = None
    gitlab_account_username: str | None = None
    gitlab_accounts: list[GitlabAccountOut] = []  # 该成员名下可选的本组账号
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
    name: str
    code: str | None = None
    octo_group_no: str | None = None
    leader_person_ids: list[int] = []


class ProjectUpd(BaseModel):
    name: str | None = None
    code: str | None = None
    octo_group_no: str | None = None
    leader_person_ids: list[int] | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    code: str | None = None
    octo_group_no: str | None = None
    leader_person_ids: list[int] = []
    group_ids: list[int] = []


class SystemIn(BaseModel):
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
    # 建项目时一次性动作：立刻投 EGM bootstrap（不落库，所以不需要迁移）
    run_egm_bootstrap: bool = False


class SystemUpd(BaseModel):
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


class SystemOut(BaseModel):
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
    system_id: int
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
    system_id: int
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
    # EGM 设计模式：本轮由哪个技能接管（只在方案模式可用）。空 = 普通方案对话
    egm: EGM_SKILL | None = None
    # egm=resolve 时引用哪一份评审报告；空 = 取该需求最新一份已完成的评审
    review_id: int | None = None


class PlanReviewIn(BaseModel):
    """发起 Goldfish 评审：指定被评审的方案版本；缺省用最新定稿方案。"""

    plan_version_id: int | None = None


class PlanReviewOut(BaseModel):
    id: int
    requirement_id: int
    plan_version_id: int | None = None
    status: str
    report_md: str | None = None
    blockers: int = 0
    # 结构化条目：title / severity / scope / description / recommendation
    findings: list[dict] = Field(default_factory=list)
    created_at: str | None = None


class EgmJobIn(BaseModel):
    """项目级 EGM 任务：bootstrap 生成 README 树，drift 出设计-代码漂移报告。"""

    kind: Literal["bootstrap", "drift"]


class EgmReportOut(BaseModel):
    id: int
    system_id: int
    kind: str
    status: str
    report_md: str | None = None
    branch: str | None = None
    mr_url: str | None = None
    created_at: str | None = None


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
    # 任务本身在池子里人人可见，但详情里的需求文档/方案只有认领人能看
    can_open: bool = True


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
