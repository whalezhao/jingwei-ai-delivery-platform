# 部署后自动触发需求测试：方案

> 状态：待评审 · 2026-11-18
> 范围：GitLab CI 部署完成 → 自动按环境发起该系统待测需求的测试用例 → 工作台可见

## 1. 背景与目标

当前经纬的链路止于「MR 合并 → 需求置为已上线」，**没有 CD，也没有部署后的回归测试**。
目标是让"部署完成"成为一个可接收的信号，并据此自动触发对应需求的测试用例。

三条已确认的产品约束：

1. **信号源**：GitLab CI（部署 job）
2. **环境可配置**：由系统配置决定哪些环境触发、映射到 QA 的哪个环境
3. **整系统触发**：一次部署覆盖"该系统所有待测需求"，不按单个需求分支部署

目标链路：

```
GitLab CI 部署成功
  → vega 收到 Deployment Hook
  → 记录 deploy_event（系统 + 环境 + sha 去重）
  → 匹配该系统全部待测需求
  → 逐需求调 QA 发起测试（环境按配置映射）
  → QA worker 执行
  → 工作台泳道更新 + octo 通知
```

## 2. 现状核实（代码事实）

| 项 | 现状 |
|---|---|
| GitLab | 13.12.15；项目 hook 字段含 `deployment_events`（能力具备） |
| 真实项目 | 抽查 `cisdigital-platform-jdk21-dependencies`、`cap-file-ffmpeg`、`portal-icons`、`data-sync`：均无 `.gitlab-ci.yml`，environments=0，deployments=0（**信号目前没有产生者**） |
| vega webhook | `api/vega/routers/webhooks.py` 只处理 Push / Merge Request / Pipeline 三类事件 |
| 合并收尾 | MR merge webhook 与 `jobs.py::merge_poll`（10 分钟轮询）两处把任务置 done、需求置 online |
| 测试绑定 | `Feature.requirement_id`（功能级）；用例 `qa_test_cases.feature_id` |
| 需求发起测试 | `qa/app/api/requirement_tests.py::run_requirement_test`：要求需求 `online`、聚合其功能下启用中的用例、快照成任务、`spawn_runs(trigger=...)` |
| 触发来源枚举 | `qa_run_batches.trigger` 仅允许 `manual / regression / rerun / requirement` |
| QA 鉴权 | 只认 vega 签发的 HMAC token（同库同密钥）；QA 自身无调度器 |
| 工作台泳道 | `web/src/pages/WorkbenchFlow.tsx` 的 `LANE_ORDER`：doc → plan → dev → review → ci → merge → notify |

**结论**：接收能力有、发送端缺；绑定是功能级；工作台没有部署与测试泳道。

## 3. 总体设计

### 3.1 信号：Deployment Hook + 轮询兜底

- 主路径：GitLab CI 部署 job 声明 `environment:`，GitLab 在部署状态变化时发 **Deployment Hook**
- 兜底：vega 新增 `deploy_poll` 调度任务（10 分钟），按系统拉 `GET /projects/:id/deployments` 补录漏掉的事件
- 只认 `status == success`；只认 `ref ∈ deploy_refs`；只认落了 `deploy_env_map` 的环境

### 3.2 服务归属

| 职责 | 归属 |
|---|---|
| 接事件、轮询、去重、需求匹配、通知 | vega api |
| 发起测试、组用例、执行、结果 | QA |

两者共库但**不跨层写表**：vega 不直接插 `qa_*`，QA 不直接读 `deploy_event`，通过一个内部接口衔接。

### 3.3 时序

```
GitLab CI deploy job (environment: test)
  │  success
  ▼
POST /webhooks/gitlab                      X-Gitlab-Event: Deployment Hook
  │  校验组 secret → 系统匹配 → 环境映射 → 插入 deploy_event（唯一键去重）
  ▼
后台任务：match_requirements(system, env, deployed_at)
  │  过滤：online / 有功能与用例 / 末次合并 ≤ 部署时间 / 本次部署后未测 / 无进行中 run
  ▼
POST {qa}/internal/requirements/{id}/run   X-Vega-Service: <shared secret>
  │  {environment_name, trigger: "deployment", deploy_event_id}
  ▼
qa_test_tasks + qa_run_batches(trigger=deployment, deploy_event_id) + qa_runs(queued)
  ▼
qa-worker 认领执行 → 结果写 qa_case_results → 批次结束
  ▼
vega 收到结果（轮询或回调）→ deploy_event.result 更新 → 通知 + 工作台泳道
```

## 4. 数据模型

### 4.1 vega

**新表 `deploy_event`**

| 列 | 类型 | 说明 |
|---|---|---|
| id | serial PK | |
| system_id | int FK dev_system | |
| gitlab_project_id | int | 冗余便于核对 |
| environment | varchar(64) | GitLab 环境名 |
| ref | varchar(256) | 部署的分支 |
| sha | varchar(64) | 被部署的提交 |
| status | varchar(16) | success / failed / … |
| deployed_at | timestamptz | |
| payload | json | 原始事件，排查用 |
| handled_at | timestamptz null | 匹配处理完成时间 |
| result | json null | `{triggered: n, skipped: [{req_id, reason}]}` |

唯一键：`(system_id, environment, sha)` —— 以"哪个系统、哪个环境、哪个提交"为业务唯一，而不是 GitLab 的 deployment_id（重试会生成新 id）。

**`dev_task` 新增**

- `merged_at timestamptz null`：MR 合并 webhook 与 `merge_poll` 两处写入
- 兼容：老数据为空时，触发判定回退用该任务 `updated_at`

**`dev_system` 新增（JSON）**

- `deploy_env_map`：`[{"gitlab":"test","qa":"测试环境"}]`，空 = 关闭自动测
- `deploy_refs`：默认 `["<mr_target_branch>"]`

### 4.2 QA

**`qa_run_batches`**

- `trigger` 枚举新增 `deployment`（CheckConstraint 需要迁移）
- 新增 `deploy_event_id int null`
- 唯一索引 `(deploy_event_id, requirement_id)` —— 同一次部署同一需求只跑一次（重复回调/重试幂等）

**（P2 可选）`qa_case_requirements(case_id, requirement_id)`**

用于"一条回归用例同时属于多个需求"。需求发起测试时用例集合 = 其功能下启用用例 ∪ 显式挂接用例。

## 5. 待测判定规则

对一次 `(system, env=E, deployed_at=T)`：

```
候选 = 系统中 status = 'online' 的需求
排除：
  a. 未绑定任何功能，或功能下无启用用例        → reason: no_cases
  b. M = max(dev_task.merged_at) > T            → reason: merged_after_deploy（下次部署再测）
  c. 已存在 batch(trigger='deployment', env=E, created_at >= M)
                                                → reason: already_tested
  d. 该需求已有进行中的 run                      → reason: running
  e. 环境 E 未在 deploy_env_map 中               → 整轮不触发（配置层拦截）
```

命中者逐个发起，结果写进 `deploy_event.result`。

> 语义即"部署就触发所有待测需求"：测过的不重复，刚合入未部署的必测。
> 规则 c 用"合入时间"而不用 sha 祖先判定，是因为整系统单分支部署场景下时间序已经足够；多分支并行部署是 P2 的话题。

## 6. 配置与界面

系统详情（或集成中心）新增区块「部署触发测试」：

| 控件 | 来源 |
|---|---|
| GitLab 环境多选 + 映射 | `GET /projects/:id/environments` → 拉环境名；每个环境选一个 QA 项目环境（`qa_project_environments`） |
| 部署分支 | 默认 `mr_target_branch`，可多选 |
| 开关 | 有映射即启用；清空即关闭 |

默认不含生产环境；要覆盖生产必须显式添加映射。

## 7. 接口清单

**vega**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/webhooks/gitlab` | 新增 `Deployment Hook` 分支（复用现有组 secret 校验） |
| GET/PUT | `/api/systems/{id}` | 扩展 `deploy_env_map` / `deploy_refs` 字段 |
| GET | `/api/workbench` | 返回每个需求最近一次覆盖它的部署事件（泳道用） |

**QA**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/internal/requirements/{id}/run` | 共享密钥 `QA_SERVICE_TOKEN`；body `{environment_name, trigger, deploy_event_id}`；内部复用 `run_requirement_test` 的组用例与 `spawn_runs` |
| GET | `/api/requirements/{id}/test-summary` | 已存在，泳道直接复用 |

内部接口不冒充个人身份，避免测试记录里出现假发起人；发起人记为需求认领人。

## 8. 工作台泳道

```
doc → plan → dev → review → ci → merge → [deploy] → [test] → notify
```

| 泳道 | 是否绘制 | 状态 | 数据源 |
|---|---|---|---|
| 部署 | 系统配置了 `deploy_env_map` | 待部署 / 已部署（环境 + sha + 时间）/ 失败 | `deploy_event` |
| 测试 | 需求已上线 | 未触发 / 排队 / 执行中 / 通过(xx%) / 失败 / 无用例 | QA test-summary |

细节：

- 整系统部署时，所有在线需求行的部署节点**同时点亮**
- 「失败」与「未触发」分开表达（测试失败 ≠ 链路没跑）
- 未配置部署的系统不画部署列（沿用现有按项目裁剪轨道的 `nodeKeys()` 做法）
- 测试泳道点击进入对应批次页（`/qa/batches/{bid}`）

## 9. CI 侧要求（各业务仓库）

```yaml
deploy:test:
  stage: deploy
  script:
    - ./deploy.sh test          # 现有部署动作
    - ./scripts/healthcheck.sh  # 建议：健康检查放进同一 job
  environment:
    name: test                  # 必须与平台里的 GitLab 环境名一致
    url: https://test.example.com
  only:
    - nightly
```

- 没有 `environment:` 的 job 不会产生部署事件
- 健康检查失败则 job 失败，不发送 success，避免"脚本返回 0 但服务没起来却触发测试"

## 10. 幂等、失败与并发

| 场景 | 处理 |
|---|---|
| GitLab 重发同一事件 | `deploy_event` 唯一键拦截 |
| 同一需求被重复触发 | `(deploy_event_id, requirement_id)` 唯一索引拦截 |
| QA 有进行中的 run | 跳过并记 `reason=running`，不重试 |
| Worker 离线 | 记 `reason=no_worker`，保留待处理；下一轮部署或 `deploy_poll` 重试 |
| 匹配阶段异常 | `deploy_event.handled_at` 不写，后台重试；超过 N 次记失败并通知 |
| 一次部署触发几十个需求 | 全部入队，由 QA 全局并发闸门串行执行；通知汇总一条，避免刷屏 |

## 11. 迁移与兼容性

| 变更 | 兼容性影响 | 迁移方式 |
|---|---|---|
| 新表 `deploy_event` | 无 | create |
| `dev_task.merged_at` 可空 | 旧数据为空 | 回填 `updated_at` |
| `dev_system` 两个 JSON 列 | 默认空 = 功能关闭 | 默认值 `[]` |
| `qa_run_batches.trigger` 加值 | 只放宽约束，旧数据不受影响 | Alembic 迁移（qa 侧已有迁移链） |
| `qa_run_batches.deploy_event_id` + 唯一索引 | 可空，旧批次不参与 | 同上 |
| 内部接口 | 新增，不影响现有调用 | — |

默认关闭：未配置映射的系统，行为与当前完全一致。

## 12. 验证方案

**P0（不依赖真实部署）**

1. GitLab 项目 hook 页面用「Test → Deployment events」推一条样例事件
2. 断言：vega 收到、组 secret 校验通过、`deploy_event` 落库且字段解析正确（environment / sha / ref / status）
3. 用真实 GitLab 的 `GET /projects/:id/deployments` 校准字段名（`deployable.sha` 以实例实际 payload 为准）

**P1（端到端）**

1. 在一个可控仓库（如 data-sync 加一条部署 job）声明 `environment: test`
2. 平台配置该环境映射到 QA 的测试环境
3. 准备两个需求：一个已 online 且有用例、一个已测过（验证不重复）、一个无用例（验证跳过原因）
4. 触发部署 → 断言：只触发应触发的那一个；批次 `trigger=deployment`；重复推同一事件不产生第二批
5. 工作台：部署节点点亮、测试泳道状态流转、完成后通知

**回归**：手动发起需求测试、功能测试、批次页、通知均不受影响。

## 13. 里程碑

| 阶段 | 内容 | 估时 |
|---|---|---|
| P0 | Deployment Hook 分支 + `deploy_event` + 测试事件验收 | 0.5 天 |
| P1 | 判定规则 + QA 内部接口 + 迁移 + 自动触发 + 通知 | 2 天 |
| P1.5 | 系统配置页 + 工作台两条泳道 | 1 天 |
| P2 | `deploy_poll` 兜底、sha 祖先精确匹配、用例级绑定 | 0.5~1 天 |

## 14. 风险与待确认

| 风险 | 说明 | 缓解 |
|---|---|---|
| CI 改造量 | 抽样仓库均无流水线，需要团队把部署搬进 GitLab CI 或至少补 `environment:` | 先在一个项目试点 |
| Worker 容量 | 部署后集中触发，测试排队 | 观察排队时长，必要时加 worker 槽位 |
| 生产误触发 | 配错映射直接打生产 | 默认排除 prod；映射变更留操作记录 |
| 事件字段差异 | 不同 GitLab 小版本 payload 略有差异 | P0 以实例测试事件为准，解析容错 |
| 无用例系统 | 部署了但没用例 → 永远不跑 | 判定里显式记录 `no_cases`，工作台展示原因 |

## 15. 参照实现位置

| 改动 | 文件 |
|---|---|
| webhook 新事件分支 | `api/vega/routers/webhooks.py` |
| 事件表与调度 | `api/vega/models.py`、`api/vega/jobs.py`、`api/vega/scheduler.py` |
| 系统配置字段与接口 | `api/vega/models.py`、`api/vega/routers/systems.py` |
| 内部发起接口 | `qa/app/api/requirement_tests.py`（复用 `run_requirement_test`） |
| 迁移 | `deploy/migrations/`、`qa/migrations/versions/` |
| 泳道 | `web/src/pages/WorkbenchFlow.tsx`、`web/src/pages/Workbench.tsx` |