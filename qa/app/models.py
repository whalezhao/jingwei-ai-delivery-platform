"""SQLAlchemy 模型。

两条贯穿全表的约定：

1. 枚举一律用 text + CHECK，不用 PostgreSQL 原生 enum —— 原生 enum 的 ALTER TYPE
   不能在事务里执行，以后加一个状态值会卡住迁移。
2. 执行域（runs / case_results / ai_calls）全部是 midscene 产物的投影副本，权威在
   产物文件本身，所以允许 reparse 覆盖重建。

测试域跑在 Vega 的同一个 PostgreSQL 库里：`qa_*` 表归本服务所有，`person` /
`dev_group` / `dev_system` / `requirement` 是 Vega 的表，这里只映射用到的列，
**只读不写**。不保留 aster 原有的 teams / projects / users 组织表。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _in(column: str, *values: str) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


# --------------------------------------------------------------------- Vega 只读表


class Person(Base):
    """Vega 人员。只读。"""

    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(64))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    roles: Mapped[list | None] = mapped_column(JSON, default=list)


class DevGroup(Base):
    """Vega 研发组。只读。"""

    __tablename__ = "dev_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[int | None] = mapped_column(Integer)


class GroupMember(Base):
    """Vega 研发组成员。只读，用于权限判断。"""

    __tablename__ = "group_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, index=True)
    person_id: Mapped[int] = mapped_column(Integer, index=True)


class System(Base):
    """Vega 系统（dev_system，一个 GitLab 仓库）。只读。"""

    __tablename__ = "dev_system"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(String(256))


class SystemOwner(Base):
    __tablename__ = "system_owner"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    system_id: Mapped[int] = mapped_column(Integer, index=True)
    person_id: Mapped[int] = mapped_column(Integer, index=True)


class GroupTechLead(Base):
    __tablename__ = "group_tech_lead"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, index=True)
    person_id: Mapped[int] = mapped_column(Integer, index=True)


class Requirement(Base):
    """Vega 需求。只读；status=online 表示所有开发任务已合入。"""

    __tablename__ = "requirement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    system_id: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    claimant_id: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------- 系统配置


class SystemModelConfig(Timestamped, Base):
    """系统级的 midscene 模型配置，覆盖 worker 环境里的全局值。

    语义是**逐字段覆盖**：只填了模型名就用全局的 key，全部留空就等于没配。
    一行都不存在表示该系统完全走全局配置。

    关于密钥：这一版明文存库。约束是硬的：**api_key 绝不进任何 API 响应、
    绝不进日志**，对外只暴露 api_key_set 布尔值。
    """

    __tablename__ = "qa_system_model_configs"

    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), primary_key=True
    )
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_family: Mapped[str | None] = mapped_column(String(64))
    base_url: Mapped[str | None] = mapped_column(String(512))
    api_key: Mapped[str | None] = mapped_column(String(512))
    init_config_json: Mapped[str | None] = mapped_column(Text)
    # 推理模型默认会把 token 大量花在思考上，实测一次小请求能把 max_tokens 烧光并
    # 返回 content=null，midscene 直接解析不到结果。NULL = 跟随全局。
    reasoning_enabled: Mapped[bool | None] = mapped_column(Boolean)
    # 每月 token 限额（NULL = 不限）。执行器的 ai_calls + 平台侧 AI 调用都计入。
    token_quota: Mapped[int | None] = mapped_column(BigInteger)
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )


class SystemModelHealth(Base):
    """系统模型接通的最近一次探测结果（每系统一行，只保留最新）。"""

    __tablename__ = "qa_system_model_health"
    __table_args__ = (
        CheckConstraint(
            _in("status", "healthy", "unhealthy", "unknown"),
            name="ck_qa_system_model_health_status",
        ),
        CheckConstraint(
            _in("source", "periodic", "manual"),
            name="ck_qa_system_model_health_source",
        ),
    )

    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    model_name: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="periodic")
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SystemEnvironment(Timestamped, Base):
    """系统的一套测试环境（地址 + 变量）。

    任务只存环境名；执行时按系统解析成对应环境。
    """

    __tablename__ = "qa_system_environments"
    __table_args__ = (
        UniqueConstraint("system_id", "name", name="uq_qa_system_environments_name"),
        Index("ix_qa_system_environments_system", "system_id"),
        Index(
            "uq_qa_system_environments_default",
            "system_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )


class SystemEnvironmentVariable(Timestamped, Base):
    """环境变量。密钥值在库里是 Fernet 密文，接口只回 value_set。"""

    __tablename__ = "qa_system_environment_variables"
    __table_args__ = (
        UniqueConstraint(
            "environment_id", "key", name="uq_qa_system_environment_variables_key"
        ),
        Index(
            "ix_qa_system_environment_variables_env", "environment_id"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    environment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_system_environments.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(String(255))


# ----------------------------------------------------------------------- 功能与用例


class Feature(Timestamped, Base):
    __tablename__ = "qa_features"
    __table_args__ = (
        UniqueConstraint("system_id", "name", name="uq_qa_features_system_name"),
        CheckConstraint(
            _in("stage", "in_iteration", "released"), name="ck_qa_features_stage"
        ),
        Index("ix_qa_features_system", "system_id"),
        Index("ix_qa_features_requirement", "requirement_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # 关联 Vega 需求；回归类功能不挂需求时为 NULL
    requirement_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("requirement.id", ondelete="SET NULL")
    )
    # 保留 aster 的分期字段；Vega 前端暂不暴露
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="in_iteration")
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )

    cases: Mapped[list["TestCase"]] = relationship(
        back_populates="feature", cascade="all, delete-orphan"
    )


class TestCase(Timestamped, Base):
    __tablename__ = "qa_test_cases"
    __table_args__ = (
        UniqueConstraint("feature_id", "name", name="uq_qa_test_cases_feature_name"),
        CheckConstraint(
            "stage_override IS NULL OR " + _in("stage_override", "in_iteration", "released"),
            name="ck_qa_test_cases_stage_override",
        ),
        CheckConstraint(
            "priority IS NULL OR " + _in("priority", "P0", "P1", "P2", "P3"),
            name="ck_qa_test_cases_priority",
        ),
        Index("ix_qa_test_cases_feature", "feature_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    feature_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_features.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # 一个用例 = 一个 midscene YAML 文档（可含多个 cases）
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    # 这条用例是为哪些缺陷补的（禅道/Jira 的 bug 单号，逗号分隔可多个）
    defect_ids: Mapped[str | None] = mapped_column(String(255))
    priority: Mapped[str | None] = mapped_column(String(4))
    # NULL 表示分期跟随所属功能；有值表示单独钉选
    stage_override: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )

    feature: Mapped[Feature] = relationship(back_populates="cases")

    @property
    def effective_stage(self) -> str:
        """用例的生效分期。派生而非落存，所以功能转正后不可能出现不一致。"""
        return self.stage_override or self.feature.stage

    @property
    def system_id(self) -> int | None:
        """用例本身不存系统，靠 feature 上溯。"""
        return self.feature.system_id if self.feature else None


class TestCaseVersion(Base):
    """用例的一次内容快照。创建、保存、AI 修复、回滚都会各留一版。"""

    __tablename__ = "qa_test_case_versions"
    __table_args__ = (
        UniqueConstraint("case_id", "version_no", name="uq_qa_test_case_versions_no"),
        CheckConstraint(
            _in("change_source", "create", "manual", "ai_fix", "rollback", "backfill"),
            name="ck_qa_test_case_versions_source",
        ),
        Index("ix_qa_test_case_versions_case", "case_id", "version_no"),
    )

    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_test_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str | None] = mapped_column(String(4))
    defect_ids: Mapped[str | None] = mapped_column(String(255))
    stage_override: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    change_source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ----------------------------------------------------------------------- 任务与回归


class TestTask(Timestamped, Base):
    __tablename__ = "qa_test_tasks"
    __table_args__ = (
        CheckConstraint(
            _in("type", "case_run", "feature_regression", "requirement_test", "adhoc"),
            name="ck_qa_test_tasks_type",
        ),
        CheckConstraint(
            _in("status", "draft", "queued", "running", "completed", "failed", "cancelled"),
            name="ck_qa_test_tasks_status",
        ),
        CheckConstraint(
            _in("case_selection_mode", "snapshot", "dynamic"),
            name="ck_qa_test_tasks_selection_mode",
        ),
        Index("ix_qa_test_tasks_system", "system_id"),
        Index("ix_qa_test_tasks_requirement", "requirement_id"),
        Index("ix_qa_test_tasks_status", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), nullable=False
    )
    # 需求触发时记录来源；普通功能执行/回归为 NULL
    requirement_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("requirement.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False, default="case_run")
    description: Mapped[str | None] = mapped_column(Text)
    # draft / queued 由人工设置；running / completed / failed 由 runs 聚合派生
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    case_selection_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="snapshot")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 环境名而不是环境 id；NULL = 使用系统的「默认」环境
    environment_name: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )

    features: Mapped[list[Feature]] = relationship(secondary="qa_task_features")


class TaskFeature(Base):
    __tablename__ = "qa_task_features"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_test_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    feature_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_features.id", ondelete="CASCADE"),
        primary_key=True,
    )


class TaskCase(Base):
    """任务的用例集合 —— 权威字段，决定执行时暂存哪些 YAML。"""

    __tablename__ = "qa_task_cases"
    __table_args__ = (UniqueConstraint("task_id", "case_id", name="uq_qa_task_cases"),)

    id: Mapped[uuid.UUID] = _pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_test_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_test_cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 血缘：这个用例是通过哪个功能被选进来的
    source_feature_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_features.id", ondelete="SET NULL")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# --------------------------------------------------------------------- 执行域（投影）


class RunBatch(Timestamped, Base):
    """一次任务执行的批次 —— 用户视角的「一次执行」。

    run 是技术粒度：一次 midscene 进程只能跑一个系统（YAML 里的 base_url 是系统级的），
    批次是这两者之间的一层。执行记录按批次展示，点进去才看到各 run，
    再往下是功能 / 用例 / midscene 报告。不存聚合指标，一律从 runs 现算。
    """

    __tablename__ = "qa_run_batches"
    __table_args__ = (
        CheckConstraint(
            _in("trigger", "manual", "regression", "rerun", "requirement"),
            name="ck_qa_run_batches_trigger",
        ),
        Index("ix_qa_run_batches_system", "system_id"),
        Index("ix_qa_run_batches_task", "task_id"),
        Index("ix_qa_run_batches_parent", "parent_batch_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_test_tasks.id", ondelete="SET NULL")
    )
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), nullable=False
    )
    # 任务名的快照：任务改名或删除后，历史执行记录还要能显示当时叫什么
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    # 重跑失败时指向源批次
    parent_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_run_batches.id", ondelete="SET NULL")
    )
    # 环境名快照：环境改名/删除后历史批次仍能显示当时跑的是什么
    environment_name: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )
    # 批次完成通知只发一次：并行槽位下，最后两个 run 可能同时看到「全部终态」。
    # 用一条带条件的 UPDATE 认领通知权，谁把 NULL 改成时间戳谁负责发。
    completed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list["Run"]] = relationship(
        back_populates="batch", passive_deletes=True
    )


class Run(Timestamped, Base):
    __tablename__ = "qa_runs"
    __table_args__ = (
        CheckConstraint(
            _in(
                "status",
                "queued",
                "preparing",
                "running",
                "parsing",
                "succeeded",
                "failed",
                "cancelled",
                "timed_out",
            ),
            name="ck_qa_runs_status",
        ),
        Index("ix_qa_runs_system", "system_id"),
        Index("ix_qa_runs_task", "task_id"),
        Index("ix_qa_runs_batch", "batch_id"),
        Index("ix_qa_runs_status", "status"),
        Index("ix_qa_runs_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_test_tasks.id", ondelete="SET NULL")
    )
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), nullable=False
    )
    # 用户视角的「一次执行」。跨系统的任务会拆成多个 run，它们共享同一个 batch。
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_run_batches.id", ondelete="SET NULL")
    )
    # 环境快照：id 用于追溯，name/base_url 用于环境被改名/删除后仍可读
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_system_environments.id", ondelete="SET NULL"),
    )
    environment_name: Mapped[str | None] = mapped_column(String(64))
    environment_base_url: Mapped[str | None] = mapped_column(String(512))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    # 认领这个 run 的 worker 进程。多 worker 下用来判断「原 worker 是否还活着」，
    # 只有拥有者失联时才允许恢复逻辑把它标失败。
    owner_worker_id: Mapped[str | None] = mapped_column(String(64))
    # 单系统任务分片：这个 run 只跑其中一部分用例；为空表示老数据/不分片
    case_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    shard_index: Mapped[int | None] = mapped_column(Integer)
    shard_count: Mapped[int | None] = mapped_column(Integer)
    midscene_run_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    midscene_status: Mapped[str | None] = mapped_column(String(32))
    # 有失败用例时 exitCode != 0，但执行是成功的 —— 所以只用它做诊断，不用它判成败
    exit_code: Mapped[int | None] = mapped_column(Integer)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    total: Mapped[int | None] = mapped_column(Integer)
    passed: Mapped[int | None] = mapped_column(Integer)
    failed: Mapped[int | None] = mapped_column(Integer)
    not_run: Mapped[int | None] = mapped_column(Integer)
    passed_after_retry: Mapped[int | None] = mapped_column(Integer)
    final_pass_rate: Mapped[float | None] = mapped_column(Numeric(6, 4))
    first_pass_rate: Mapped[float | None] = mapped_column(Numeric(6, 4))
    filtered: Mapped[int | None] = mapped_column(Integer)
    collection_errors: Mapped[int | None] = mapped_column(Integer)
    project_failures: Mapped[int | None] = mapped_column(Integer)

    work_dir: Mapped[str | None] = mapped_column(Text)
    result_dir: Mapped[str | None] = mapped_column(Text)
    report_html_path: Mapped[str | None] = mapped_column(Text)

    # 模型漂移归因：通过率跳变时用来判断是不是换了模型/配置
    model_name: Mapped[str | None] = mapped_column(String(128))
    env_hash: Mapped[str | None] = mapped_column(String(64))

    error_message: Mapped[str | None] = mapped_column(Text)
    # 非用例本身的原因导致的失败（模型额度用尽、模型服务不可达、执行器崩了）。
    # 这些必须能和「真实的测试失败」区分开 —— 否则上游 429 会让通过率崩掉。
    failure_kind: Mapped[str | None] = mapped_column(String(32))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )

    cases: Mapped[list["CaseResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    batch: Mapped["RunBatch | None"] = relationship(back_populates="runs")


class CaseResult(Base):
    __tablename__ = "qa_case_results"
    __table_args__ = (
        # 幂等 upsert 键。caseId/documentId 是内容哈希，不能当身份，所以只做去重而非识别
        UniqueConstraint(
            "run_id", "midscene_document_id", "case_index", name="uq_qa_case_results_identity"
        ),
        Index("ix_qa_case_results_run", "run_id"),
        Index("ix_qa_case_results_case", "test_case_id"),
        Index("ix_qa_case_results_status", "status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_runs.id", ondelete="CASCADE"), nullable=False
    )
    # 靠 source_path 的 basename（暂存文件名 = 用例 UUID）反查，解不出则为 NULL
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_test_cases.id", ondelete="SET NULL")
    )
    midscene_document_id: Mapped[str] = mapped_column(String(128), nullable=False)
    midscene_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(255))
    case_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    passed_after_retry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    run: Mapped[Run] = relationship(back_populates="cases")


class AiCall(Base):
    """ai-profile-stats.log 的逐行投影。用 bigserial，这是高频追加表。"""

    __tablename__ = "qa_ai_calls"
    __table_args__ = (
        Index("ix_qa_ai_calls_run", "run_id"),
        Index("ix_qa_ai_calls_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_runs.id", ondelete="CASCADE")
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input: Mapped[int | None] = mapped_column(Integer)
    cost_ms: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[str | None] = mapped_column(Text)


class CaseStability(Base):
    """每次 run 进终态后增量刷新的用例稳定性汇总。

    flaky 是这套系统的核心产品指标：AI 执行器同一个用例时过时不过，需要被显式看见
    而不是被平均掉。
    """

    __tablename__ = "qa_case_stability"

    test_case_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("qa_test_cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    runs_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flaky: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ------------------------------------------------------------------------ AI 结论


class AiCheck(Base):
    """AI 在某个人工节点上给出的结论（评估执行结果 / 签名前预检）。

    这张表同时当**任务队列**用：worker 在批次收尾时插一条 ``pending``，
    API 进程里的轮询协程认领它、调模型、写回结论 —— 不占那个串行跑测试的 worker。
    """

    __tablename__ = "qa_ai_checks"
    __table_args__ = (
        Index("ix_qa_ai_checks_entity", "entity_type", "entity_id"),
        Index("ix_qa_ai_checks_due", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    # batch（执行结果评估）/ task（任务预检）
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # healthy / risky / needs_attention；纯文本结论为 None
    verdict: Mapped[str | None] = mapped_column(String(24))
    summary: Mapped[str | None] = mapped_column(Text)
    # 结构化明细：reasons / advice / markdown …
    detail: Mapped[dict | None] = mapped_column(JSONB)
    model_name: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiTokenUsage(Base):
    """平台侧 AI 调用的 token 流水（生成用例 / 执行评估 / 预检）。

    执行器自己的 token 在 ai_calls 里 —— 两边按系统汇总成「当月用量」，
    和系统配置里的 token_quota 比较。只记流水不记余额。
    """

    __tablename__ = "qa_ai_token_usage"
    __table_args__ = (
        Index("ix_qa_ai_token_usage_system_time", "system_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    system_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dev_system.id", ondelete="CASCADE"), nullable=False
    )
    # case_draft / assess / precheck / model_test
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSetting(Base):
    """全局运行时设置（永远只有 id=1 这一行）。

    max_concurrent_runs 是**全局**并发上限：所有 worker 加起来最多同时跑这么多
    run。run_shard_size 是单系统任务的分片粒度；0 表示不分片。
    """

    __tablename__ = "qa_app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    run_shard_size: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    # 全部通过的批次是否还要调模型评估：AI 评估每次都是一笔真实成本。
    ai_assess_failures_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("person.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkerHeartbeat(Base):
    """worker 进程心跳。每个进程一行，启动注册，退出删除，运行中每 10 秒更新。

    恢复逻辑只处理「心跳过期」的 worker 名下的 run —— 否则新 worker 一启动就会
    把其他 worker 正在跑的测试全部标失败（这是多 worker 水平扩容的硬前提）。
    """

    __tablename__ = "qa_worker_heartbeats"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str | None] = mapped_column(String(255))
    pid: Mapped[int | None] = mapped_column(Integer)
    slots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )