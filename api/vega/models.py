"""全部数据表。加密字段后缀 _enc 存 Fernet 密文。"""

from __future__ import annotations

import random
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# utcnow() 产出带时区的时间，PG 侧列必须是 timestamptz——asyncpg 拒绝把 aware 值写进
# timestamp without time zone（SQLite 忽略 tz，读回仍 naive，比较处已各自补时区）。
TZDateTime = DateTime(timezone=True)


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # 预设头像 key（web/public/avatars/aNN.svg），只允许内置头像、不上传；
    # 新用户默认随机分配一张，之后仍可清空
    avatar: Mapped[str | None] = mapped_column(String(32), default=lambda: f"a{random.randint(1, 12)}")
    gitlab_username: Mapped[str | None] = mapped_column(String(128), unique=True)
    gitlab_email: Mapped[str | None] = mapped_column(String(190))
    octo_uid: Mapped[str | None] = mapped_column(String(64), index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256))  # 空 = 不能登录
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # 研发体系角色（JSON 数组，可多选）：决定谁能认领需求——未定稿只认 product，
    # 定稿后只认 dev。空数组 = 无角色，两段都认领不了
    roles: Mapped[list | None] = mapped_column(JSON, default=list)

    # 人员级 LLM（resolve_llm 第一优先级）
    llm_base_url: Mapped[str | None] = mapped_column(String(512))
    llm_api_key_enc: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(String(128))
    llm_effort: Mapped[str | None] = mapped_column(String(16))  # 思考等级 low/medium/xhigh

    # agent-memory 业务用户 user_key（sk-mem-…），未绑走平台公共 key
    am_user_key_enc: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class DevGroup(Base):
    __tablename__ = "dev_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    gitlab_base_url: Mapped[str | None] = mapped_column(String(256))  # 如 https://git.example.com
    gitlab_token_enc: Mapped[str | None] = mapped_column(Text)
    llm_base_url: Mapped[str | None] = mapped_column(String(512))
    llm_api_key_enc: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(String(128))
    llm_effort: Mapped[str | None] = mapped_column(String(16))  # 思考等级 low/medium/xhigh
    octo_group_no: Mapped[str | None] = mapped_column(String(64))
    octo_bot_token_enc: Mapped[str | None] = mapped_column(Text)  # 组独立 bot token，空=用全局
    am_team_id: Mapped[str | None] = mapped_column(String(64))
    webhook_secret: Mapped[str | None] = mapped_column(String(128), index=True)  # 单址 webhook 靠它识别组
    scan_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_webhook: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    members: Mapped[list[GroupMember]] = relationship(back_populates="group", cascade="all,delete-orphan")


class GroupMember(Base):
    """组内人员的 octo 侧身份 + 三段式对配状态。"""

    __tablename__ = "group_member"
    __table_args__ = (UniqueConstraint("group_id", "person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"), index=True)
    person: Mapped[Person] = relationship()
    group: Mapped[DevGroup] = relationship(back_populates="members")

    octo_uid: Mapped[str | None] = mapped_column(String(64))
    octo_nickname: Mapped[str | None] = mapped_column(String(128))
    # agent-memory 的 agent id（agent 挂在 team 下，所以存组-人对上；默认取 gitlab_username）
    am_agent_id: Mapped[str | None] = mapped_column(String(64))
    # auto_exact / auto_ai / pending / manual / unmatched
    match_status: Mapped[str] = mapped_column(String(16), default="unmatched")
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_reason: Mapped[str | None] = mapped_column(String(512))
    manual_lock: Mapped[bool] = mapped_column(Boolean, default=False)


class Project(Base):
    __tablename__ = "dev_project"
    __table_args__ = (UniqueConstraint("group_id", "gitlab_project_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    gitlab_project_id: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(256))  # group/repo
    name: Mapped[str] = mapped_column(String(128))
    code_prefix: Mapped[str] = mapped_column(String(12))  # 需求编号前缀，如 PMS
    default_branch: Mapped[str] = mapped_column(String(64), default="master")
    # MR 落在项目上：feature 分支合到哪个分支由项目自己定（原研发组配置，组级无意义）
    mr_target_branch: Mapped[str] = mapped_column(String(64), default="nightly")
    # 路径前缀（JSON 数组，None=未配置）：命中 audit_prefixes 的 MR 触发 CRG 审计；
    # auto_approve_prefixes 全命中才允许自动放行（fail-closed）
    audit_prefixes: Mapped[list | None] = mapped_column(JSON)
    auto_approve_prefixes: Mapped[list | None] = mapped_column(JSON)
    # agent-memory 绑定（项目级优先，组 am_team_id 兜底）：随 claude 调用以 x-team-id /
    # x-agent-id 头下发，proxy 据此直选注入团队资产，跳过会话初始化提问
    am_team_id: Mapped[str | None] = mapped_column(String(64))
    am_agent_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    pms: Mapped[list[ProjectPM]] = relationship(back_populates="project", cascade="all,delete-orphan")
    owners: Mapped[list[ProjectOwner]] = relationship(back_populates="project", cascade="all,delete-orphan")


class ProjectPM(Base):
    """技术负责人：MR 卡住升级的备份收件人（项目没配则落到组技术负责人）；不收日报。"""

    __tablename__ = "project_pm"
    __table_args__ = (UniqueConstraint("project_id", "person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"), index=True)
    project: Mapped[Project] = relationship(back_populates="pms")


class ProjectOwner(Base):
    """项目负责人：MR 卡住时的一级升级对象；无响应再升到技术负责人。"""

    __tablename__ = "project_owner"
    __table_args__ = (UniqueConstraint("project_id", "person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"), index=True)
    project: Mapped[Project] = relationship(back_populates="owners")


class GroupTechLead(Base):
    """研发组技术负责人：代码日报/周报/月报的 @ 对象，也是项目没配技术负责人时的兜底。"""

    __tablename__ = "group_tech_lead"
    __table_args__ = (UniqueConstraint("group_id", "person_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"), index=True)


class Requirement(Base):
    __tablename__ = "requirement"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # REQ-PMS-2610-007
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    # pool / claimed / planned / confirmed / archived / online（已上线）
    status: Mapped[str] = mapped_column(String(16), default="pool", index=True)
    # 迭代版本标签，逗号分隔（如 V2.3,V2.4）；迭代状态不落库，由 status==online 推导成「正式」
    tags: Mapped[str | None] = mapped_column(String(256))
    claimant_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    # 需求提出人：产品认领并定稿文档后，认领权移交开发，产品留在这里供展示
    proposer_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    # 需求讨论与方案讨论各用一条 session（agent / 资产注入不同，不能串）
    req_session_id: Mapped[str | None] = mapped_column(String(64))
    plan_session_id: Mapped[str | None] = mapped_column(String(64))
    plan_runner_id: Mapped[int | None] = mapped_column(ForeignKey("runner_node.id"))
    plan_md: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    docs: Mapped[list[RequirementDoc]] = relationship(back_populates="requirement", cascade="all,delete-orphan")
    tasks: Mapped[list[DevTask]] = relationship(back_populates="requirement")


class RequirementDoc(Base):
    """需求文档（多版本，其一为定稿版本）。"""

    __tablename__ = "requirement_doc"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirement.id"), index=True)
    filename: Mapped[str] = mapped_column(String(256))
    version: Mapped[int] = mapped_column(Integer, default=1)  # 同需求内自增
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)  # 定稿版本（同需求唯一）
    # 原始文件字节：平台只做传递与下载，解析交给 runner 上的 claude 自行完成
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    requirement: Mapped[Requirement] = relationship(back_populates="docs")


class PlanVersion(Base):
    """plan 模式方案版本：每次 AI 修改出一份 md 快照，confirm 时标记定稿。"""

    __tablename__ = "plan_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirement.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)  # 定稿方案（同需求唯一）
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class PlanReview(Base):
    """EGM Goldfish 方案评审：一轮评审一行，报告留库供前端抽屉回看。

    status=incomplete 是 EGM 的「评委读不到 persona/标准，自报未完成」——
    必须与 done 分开记，否则会把一次没跑成的评审当成「通过」。
    """

    __tablename__ = "plan_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirement.id"), index=True)
    plan_version_id: Mapped[int | None] = mapped_column(ForeignKey("plan_version.id"))
    runner_task_id: Mapped[int | None] = mapped_column(ForeignKey("runner_task.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/done/incomplete/failed
    report_md: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[list | None] = mapped_column(JSON)  # 报告里能解析出 JSON 条目时填，否则空
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    @property
    def blocker_count(self) -> int:
        return sum(1 for f in (self.findings or []) if (f or {}).get("severity") == "Blocker")


class DevTask(Base):
    __tablename__ = "dev_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirement.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)  # TASK-REQ-PMS-2610-007-2
    # pending / queued / running / pushed / mr_created / reviewing / cicd / done / failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("person.id"))  # 下发人（=认领人）
    am_agent_id: Mapped[str | None] = mapped_column(String(64))  # agent-memory agent
    # 定稿拆解出的任务切面：标题 + 只属于自己的实现范围（多任务并行、互不越界）
    title: Mapped[str | None] = mapped_column(String(256))
    scope: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(String(256))
    mr_iid: Mapped[int | None] = mapped_column(Integer)
    mr_url: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    # MR 无人处理升级等级（每 12h 一级、3 级封顶；单调递增，毕业即止）
    escalated_level: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    requirement: Mapped[Requirement] = relationship(back_populates="tasks")


class PlanMessage(Base):
    __tablename__ = "plan_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirement.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    # 两种讨论各一条会话历史：requirement / plan。历史行默认 plan（双模式上线前只有方案讨论）
    mode: Mapped[str] = mapped_column(String(16), default="plan")
    # 该轮用户在输入框选的团队/专家（空 = 项目/研发组默认）；每轮随请求头生效
    team_id: Mapped[str | None] = mapped_column(String(64))
    agent_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class PlanEvent(Base):
    """plan 会话过程留痕（思考/工具链/子代理进度）：SSE 只广播，刷新后要能回放思考链。

    message_id 在回合结束时回填到该轮 assistant 消息上，前端按消息重放过程。
    """

    __tablename__ = "plan_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("requirement.id"), index=True)
    runner_task_id: Mapped[int] = mapped_column(ForeignKey("runner_task.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("plan_message.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 同任务内序号，跨批次续增
    kind: Mapped[str] = mapped_column(String(16))  # thinking/tool_use/tool_result/raw
    payload: Mapped[dict] = mapped_column(JSON)  # 前端 plan_raw 同款形状
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class RunnerNode(Base):
    __tablename__ = "runner_node"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64))  # sha256hex
    # 遗留列：旧库 NOT NULL 无默认值，删掉映射会令旧库插入失败；已不参与任何逻辑
    slots: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(16), default="offline")  # online/offline
    last_heartbeat: Mapped[datetime | None] = mapped_column(TZDateTime)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class RunnerTask(Base):
    """任务队列。plan_turn 粘性：payload.plan_runner_id 决定只能被哪个 runner 领。"""

    __tablename__ = "runner_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # plan_turn / dev_task
    payload: Mapped[dict] = mapped_column(JSON)
    # git_token / claude_env 等秘密：加密落库，认领时解密并入返回的 payload（内存态）
    secrets: Mapped[str | None] = mapped_column(Text)
    # queued / claimed / running / done / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    runner_id: Mapped[int | None] = mapped_column(ForeignKey("runner_node.id"))
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirement.id"), index=True)
    dev_task_id: Mapped[int | None] = mapped_column(ForeignKey("dev_task.id"), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    enqueued_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class DevTaskEvent(Base):
    """开发任务过程留痕：runner 回传的 claude 事件（思考/文本/工具调用/结果），前端回放用。

    SSE 只广播不落库，不看直播就丢了 —— 这里把 dev_task 的全过程存下来。
    """

    __tablename__ = "dev_task_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    dev_task_id: Mapped[int] = mapped_column(ForeignKey("dev_task.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 同任务内序号，跨批次续增
    kind: Mapped[str] = mapped_column(String(16))  # text/thinking/tool_use/tool_result/result/raw
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class MrReview(Base):
    """MR 初审结论 + 自动放行状态机。一行 = 一个 (project, iid, sha) 的完整生命周期。

    verdict/gate 是初审侧（gate 非空表示初审时 CI 未绿，放行前须复查转绿）；
    flow_state 是放行侧（空=待处理，hold_not_mergeable 非终态下轮重试）。
    """

    __tablename__ = "mr_review"
    __table_args__ = (UniqueConstraint("project_id", "mr_iid", "head_sha"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    mr_iid: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(256), default="")
    source_branch: Mapped[str | None] = mapped_column(String(256))
    target_branch: Mapped[str | None] = mapped_column(String(256))
    changed_files: Mapped[list] = mapped_column(JSON, default=list)  # 命中 audit_prefixes 的文件
    verdict: Mapped[str] = mapped_column(String(16))  # approve / modify / failed
    summary: Mapped[str | None] = mapped_column(Text)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    gate: Mapped[str] = mapped_column(String(32), default="")  # "" 或 ci:<status>
    pipeline_url: Mapped[str | None] = mapped_column(String(512))  # gate 对应 pipeline 页面地址（CI 失败跳转）
    commented: Mapped[bool] = mapped_column(Boolean, default=False)
    flow_state: Mapped[str] = mapped_column(String(24), default="")  # "" / merged / conflict_notified / hold_not_mergeable
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class EgmReport(Base):
    """项目级 EGM 产物：bootstrap（README 树）与 drift（设计-代码漂移）。

    bootstrap 会写大量文件，只落在独立 worktree 并推 branch 开 MR，branch 记在这里
    让人知道去哪看；drift 只读，branch 为空。
    """

    __tablename__ = "egm_report"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # bootstrap / drift
    runner_task_id: Mapped[int | None] = mapped_column(ForeignKey("runner_task.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/done/failed
    report_md: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(String(256))
    mr_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class GitlabEvent(Base):
    __tablename__ = "gitlab_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    handled_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class NotifyLog(Base):
    __tablename__ = "notify_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    octo_chat: Mapped[str | None] = mapped_column(String(64))
    mentioned_uids: Mapped[list] = mapped_column(JSON, default=list)
    content: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class DailyStat(Base):
    __tablename__ = "daily_stat"
    __table_args__ = (UniqueConstraint("group_id", "stat_date", "project_id", "gitlab_author"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    stat_date: Mapped[date] = mapped_column(Date)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))  # 匹配不到人为空
    gitlab_author: Mapped[str] = mapped_column(String(190))  # email 或 username
    commits: Mapped[int] = mapped_column(Integer, default=0)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)


class FileStat(Base):
    """每日热点文件：同一提交按文件路径计一次，供日报热点榜与代码统计页用。"""

    __tablename__ = "file_stat"
    __table_args__ = (UniqueConstraint("group_id", "stat_date", "project_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    stat_date: Mapped[date] = mapped_column(Date)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"))
    path: Mapped[str] = mapped_column(String(512))
    commits: Mapped[int] = mapped_column(Integer, default=0)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)


class CommitFile(Base):
    """提交×文件：留下提交信息与作者，风险里用来回答「这个文件反复改，是在改什么」。"""

    __tablename__ = "commit_file"
    __table_args__ = (
        UniqueConstraint("project_id", "sha", "path"),
        Index("ix_commit_file_path", "project_id", "path", "stat_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    stat_date: Mapped[date] = mapped_column(Date)
    project_id: Mapped[int] = mapped_column(ForeignKey("dev_project.id"))
    path: Mapped[str] = mapped_column(String(512))
    sha: Mapped[str] = mapped_column(String(64))
    author: Mapped[str] = mapped_column(String(190), default="")
    message: Mapped[str] = mapped_column(String(500), default="")  # 只存首行
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    """全局键值配置。目前只有 octo 全局 bot token（集成中心录入，值加密）。"""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class GitlabMember(Base):
    """本组 GitLab 项目成员快照：每次拉取整组覆盖，成员抽屉直接读它，不再实时逐项目拉。"""

    __tablename__ = "gitlab_member"
    __table_args__ = (UniqueConstraint("group_id", "username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("dev_group.id"), index=True)
    username: Mapped[str] = mapped_column(String(190))
    name: Mapped[str] = mapped_column(String(190), default="")
    email: Mapped[str] = mapped_column(String(190), default="")
    synced_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
