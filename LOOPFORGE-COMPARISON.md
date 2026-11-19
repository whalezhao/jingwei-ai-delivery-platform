# LoopForge 对比分析：值得借鉴什么、我们哪里更强

> 对象：腾讯开源 [LoopForge](https://github.com/Tencent/LoopForge)（MIT，Go 1.26 + Gin/GORM/MySQL） 与 本仓库「经纬 AI 研发交付平台」（FastAPI + 独立 QA 服务 + React） 结论基于源码阅读（LoopForge 浅克隆）与本仓库实际代码，事实来源见文末。

## 0 · 一句话结论

**LoopForge 把「研发流程」做成了数据（可编排的 DAG），我们把「研发流程」做成了代码（固化的阶段机）。** 这一条差异派生了其余所有差异：它通用、可配置、可插拔，但需要团队自己画流程； 我们专用、开箱即用，但想改流程就得改代码。两者不是替代关系。

我们用一句话概括彼此的位置：

- **LoopForge**：一个自托管的 coding-agent 工作流引擎，卖点是「任意流程 + 硬门禁 + 沙箱」。
- **经纬**：一条已经跑通的交付流水线，卖点是「需求到上线到回归的闭环 + GitLab 深度 + 代码智能与记忆 + 组织角色」。

---

## 1 · 逐维度对照

| 维度 | LoopForge | 经纬 | 判断 |
| --- | --- | --- | --- |
| 流程定义 | 图：`nodes(agent/script/approval/sop)` + `edges`，画布或 JSON，带 revision/publish | 代码里写死的阶段机（需求/方案/任务三段） | 它更灵活，我们更省心 |
| 执行模型 | 进程内 DAG 调度，依赖就绪即起 goroutine，**分支真并行** | DB 队列 + runner 长轮询，阶段基本串行 | 它更强，我们够用 |
| 运行快照 | `Run` 冻结 `WorkflowSnapshot`，`NodeRun` 记 attempt/reworkCount/sessionId | 有 `plan_version`、`dev_task_event`，但无「本次用的 prompt/模型/规则」快照 | **值得借鉴** |
| Agent 接入 | 10 个 CLI 硬编码（CodeBuddy/Codex/Claude Code/Cursor/…），子进程 + 独立 HOME | 只有 `claude -p`，按任务类型切参数（plan 只读、dev 跳过权限、评审无工具） | 它更广，我们更聚焦 |
| 人工门禁 | 一等公民：`approval` 节点、`gateResult`、`submit_gate_result(pass/rework/fail/human)`、`rejectTo` + `maxReworks` | 固定动作：认领、采用方案、GitLab 合并；**没有可配置闸门，没有自动打回上游** | **值得借鉴** |
| 失败处理 | 自动重试注入反馈、打回上游、verification（command/manual/mr） | 失败重试；冲突只评论 @ 人；无回到方案 | **值得借鉴** |
| Git 托管 | GitHub（OAuth/PAT/Issue/PR）；GitLab/Gitea **只能 SSH clone/push**（issue #1039/#1038） | GitLab 全事件（push/MR/pipeline/deployment）+ 自动建 MR + target branch + merge 兜底轮询 | **我们明显更强** |
| CI/CD | **未接 CI 状态** | pipeline webhook（事件级）+ Deployment Hook → 自动触发需求测试 | **我们明显更强** |
| 测试域 | 无（到 code review / PR 为止） | midscene 用例：生成、AI 修改、失败修复、稳定性、调试试跑、部署触发 | **我们明显更强** |
| 代码智能 | 无内置；只提供 MCP/skills 扩展点，代码理解靠 agent 自己 | CRG：结构图 + 向量索引（qwen3-embedding），runner 按 commit 增量同步，MR 按 `audit_prefixes` 做影响面；serena + LSP 做符号级定位与编辑 | **我们明显更强** |
| 跨会话记忆 | 仓库中未见内置实现，`release-readiness` 明确知识库尚未联调 | agent-memory proxy：person/group/system 三级绑定，`x-conversation-id` 选择记忆资产，plan 会话另有只读 memory-bridge 检索通道 | **我们明显更强** |
| 方案评审 | 评审就是一个 agent 节点 + MR 门禁 | EGM 插件：六视角 brainstorm/draft/resolve + Goldfish 零上下文多评委（5 阶段、blocker 计数、`incomplete` 不算通过）+ 每周设计-代码漂移 | **我们明显更强** |
| 隔离 | 4 种 sandbox（docker/bwrap/seatbelt/local），每 agent 独立 HOME/MCP | 同机子进程，dev 任务 `--dangerously-skip-permissions` | **值得借鉴（安全）** |
| 并发控制 | DB lease（owner/leaseUntil/executionGeneration）+ 准入上限 | PG `SKIP LOCKED` + sticky runner + 心跳 + 死节点重排；QA 侧 advisory lock 限流 | 各有取舍，我们够用 |
| 持久化 | MySQL 8.4，44 个 migration | PostgreSQL，alembic | 无优劣 |
| 组织模型 | 明确「可信团队」：登录用户可读全实例，**非多租户** | 研发组 / 项目 / 角色 / 认领 / 三方通知矩阵 | **我们明显更强** |
| 可观测 | SSE、日志/telemetry 分页、artifact 预览、token 成本 | SSE、思考链/工具调用全程可回放、状态栏、统计页、octo 通知 | 各有侧重，我们偏业务 |
| 工程质量 | 881 个 `_test.go`，测试 15.3 万行 vs 产品 14 万行；CI 85% 语句覆盖 + 85% 路由命中 + 24 个 e2e | API 侧有测试；QA 侧 40 个测试、核心两个文件近千行 | **值得借鉴** |
| 部署形态 | Compose（MySQL + platform）或原生 Go；Compose 挂 docker.sock（等同宿主 root，按可信假设） | 单机 Compose，无外部依赖（PG 可与其它系统共用） | 我们更轻 |

---

## 2 · 值得我们借鉴的（按性价比排序）

### ① MR head SHA 校验 —— 最便宜、收益最直接

**它怎么做**：verification gate 有 `mode=mr`，校验被批准/评审的那次 commit 与即将合并的 MR head SHA 一致（`engine/gate_mr.go`）。

**我们现在**：评审看的是 MR，合并也按 MR，但**没有校验「评审过的 sha == 合并的 sha」**。评审后追加一个 commit 再合并，等于评审被绕过。

**建议**：webhook 的 merge 事件里比对 `MrReview.sha` 与 merge 的实际 sha，不一致就告警/阻断。改动局限在 `api/vega/routers/webhooks.py` + `autoflow.py`。

**代价**：小（半天内）。

### ② 自动打回上游 + 返工上限

**它怎么做**：`rejectTo` 指定打回哪个上游节点，`maxReworks` 限制返工次数；agent 通过 MCP 提交 `pass/rework/fail/human`，`human` 会停在人工门（`engine/gate_submit.go`、`node_outcome.go`）。

**我们现在**：任务失败只会重试同一个任务；「方案本身是错的」这条路径不存在，人只能手动重来。

**建议**：给 `dev_task` 增加「打回方案」动作（`status=failed` → 生成新 `plan_version` 并重新派发），带次数上限。**不要**引入完整 gate 协议，只补这一条回路。

**代价**：中（1\~2 天，含状态机与 UI）。

### ③ 运行快照

**它怎么做**：`Run` 冻结整个 `WorkflowSnapshot`；`NodeRun` 记 attempt / reworkCount / sessionId。

**我们现在**：`plan_version` 有内容快照，但\*\*「这次用的模型、prompt 版本、规则/技能」没有一起冻结\*\*。模型或 prompt 改了之后，历史结果无法解释。

**建议**：在 `dev_task_event` 的首次事件里记录 `model_name + prompt_hash + claude 参数 + 插件/技能列表`。已有 `env_hash` 是同类思路，扩展即可。

**代价**：小（半天）。

### ④ 每任务独立 HOME / MCP 配置

**它怎么做**：每个 agent 子进程独立私有 HOME 与 MCP 配置，sandbox profile 可选 docker/bwrap/seatbelt/local。

**我们现在**：dev 任务直接 `--dangerously-skip-permissions` 跑在 runner 宿主上，共享用户目录。

**建议**：至少做到「一任务一临时 HOME + 只注入该任务需要的 MCP/插件目录」。完整沙箱（容器）可以推迟，但 HOME 隔离便宜且能挡住大部分误伤。

**代价**：小（半天）。

### ⑤ 工程质量门禁

**它怎么做**：CI 强制 85% 语句覆盖 + 85% 路由命中率，24 个 e2e API 文件。

**我们现在**：`api/` 有测试；`qa/` 只有 40 个用例，`worker.py`（978 行）和 `ai_case.py`（956 行）几乎没有覆盖。

**建议**：先给 QA 的 parse/persist/worker 关键路径补测试，再谈覆盖率数字。**不要**照抄 85% 门禁——那会让每次改动都被迫补测试，节奏不匹配。

**代价**：中，持续投入。

### ⑥ 租约 + 执行代数（可选）

**它怎么做**：`ownerInstanceId / leaseUntil / executionGeneration`，多实例下防止僵尸进程写入。

**我们现在**：心跳 + sticky runner + 1 分钟重排，单实例场景没问题。若将来跑多 runner 实例，`executionGeneration` 这个思路能挡住「旧进程醒来后覆盖新结果」。

**代价**：小，但现在不需要。

---

## 3 · 我们做得更好的

### ① GitLab 深度（最硬的优势）

它是 GitHub-first，**GitLab 只能 SSH clone/push，没有托管 API**（issue #1039/#1038），也没有 CI 状态集成。 我们是 GitLab 13.12 的实打实适配：push/MR/pipeline/deployment 全事件、自动建 MR、target branch 配置、以及 **13.12 不发 merge 事件时的** **`merge_poll`** **兜底**。这套东西是踩过坑才有的，换任何通用引擎都买不到。

> 注：这条优势绑定我们的客户环境。若哪天要支持 GitHub，可以反过来借鉴它的 OAuth/PR 抽象。

### ② 交付闭环延伸到「部署之后」

LoopForge 到 code review / PR 为止。我们的链路是 **需求 → 方案 → 开发 → MR → 合并 → 部署事件 → 自动选需求跑回归 → 三方通知**。 其中「部署事件 → 环境映射 → 幂等触发 → 跳过原因 → 汇总通知」是它完全没有的领域能力。

### ③ 组织与角色模型

研发组、项目负责人、测试负责人、产品/开发认领分段、三方通知矩阵——对应的是真实企业流程。 它的设计前提是「可信团队、非多租户、登录可读全实例」，放到有权限边界的环境里不能直接用。

### ④ 测试用例域

用例生成 / AI 修改 / 失败修复 / 稳定性 flaky / 首次与最终通过率 / 步骤级证据 / 调试试跑——它没有这一层。

### ⑤ 轻量与可运维

单机 Compose，PG 可与其它系统共用；它需要 MySQL + api/worker 双角色 + sandbox 运行时，最小部署面明显更大。

---

## 3.5 · 代码智能与记忆层（上一版遗漏，补充）

上一版只对比了「流程」，漏了我们真正的技术差异：**经纬在 runner 里带了一套代码理解与记忆设施，LoopForge 只提供 MCP 扩展点**。这不是功能清单上的一行，而是「模型能不能看到事实」的分水岭。

### ① CRG（code-review-graph）：结构图 + 向量

- runner 每次接到仓库就同步：无图则 build，有图按 commit 漂移 update，向量按节点增量补（`api/vega/runner.py::_crg_sync`）
- 嵌入走内网 OpenAI 兼容服务（`qwen3-embedding-0.6b`，`api/vega/config.py:60`），不依赖公网
- 以 MCP server 形式合进 claude 的 `.claude.json`（`runner.py:1018`），模型可查调用链、影响面、社区、桥梁节点
- MR 审计按 `audit_prefixes` 触发（`models.py:184`），把「这次改动炸到哪」变成可查询事实

为什么重要：有图谱时，影响面是**查出来的**；没有时只能靠读文件猜——这正是 AI 评审最容易漏掉跨文件破坏的地方。

### ② serena + LSP：符号级操作

- 镜像内置 pyright / typescript-language-server / jdtls（`api/Dockerfile`、`deploy/README.md`）
- MCP 暴露符号定位、`replace_symbol_body`、`rename_symbol`；跨文件重命名不靠正则猜
- plan/EGM 任务走白名单，评审侧禁用符号编辑；dev 任务才有写权限

### ③ agent-memory：跨会话记忆

- 所有 LLM 流量统一走 AM proxy（anthropic Messages 协议），模型/密钥按 人员 → 研发组 → 默认 解析（`api/vega/llm.py`）
- 绑定粒度为 person / group / system，另有 `x-conversation-id` 让 proxy 按会话选记忆资产
- plan 会话的工具白名单里专门留了 `Bash(curl *{am}/memory-bridge/*)`（`claude.py:224`）——记忆检索是**受控通道**，不是把整库塞进 prompt

### ④ EGM：方案质量门

- 方案轮：六视角 brainstorm / draft / resolve，需要子代理，所以有独立的一套参数（`claude.py::egm_plan_args`）
- Goldfish 评审：**零上下文**、独立 worktree、5 阶段多名评委，报告落 `consolidated_review_report.md`；`incomplete`（评委读不到 persona/标准）绝不当作通过（`runner_api.py:450`）
- 项目级任务：bootstrap 生成 README 树、drift 每周比对设计与代码漂移（`scheduler.py:117`）
- 权限上明确拒绝 `--dangerously-skip-permissions`——评委的约束不能靠自觉

### 对照

| | LoopForge | 经纬 |
| --- | --- | --- |
| MCP | 一等扩展点，每 agent 独立配置 | 用到，但更依赖内置能力 |
| 代码图谱 | 无内置 | CRG：图 + 向量 + 按 commit 增量同步 |
| 符号编辑 | 无内置 | serena + 三种 LSP |
| 跨会话记忆 | 无内置 | AM：三级绑定 + 受控检索 |
| 设计评审 | 一个 agent 节点 | EGM：多评委 + 零上下文 + 漂移检测 |

**结论**：LoopForge 的门禁管「流程有没有走完」，EGM 管「方案本身好不好」，CRG 管「改动炸到哪」——三者不在同一层。我们的组合更接近**能自己判断对错的系统**，它更接近**能可靠执行任意流程的引擎**。

---

## 4 · 两边都没做好（不建议现在投入）

| 方向 | 现状 |
| --- | --- |
| 多 agent 编排 | 它的「角色群协作」已退役；我们只有 Claude 子代理。双方都没有真正的多 agent 协议 |
| 成本核算 | QA 侧有 token 流水与月配额；Vega 侧只展示上下文用量，无按任务成本 |
| 需求/文档级 RAG | LoopForge 的知识库未联调；我们有 AM 记忆与 code graph，但需求文档仍是整段注入，没有做检索 |
| 完整 DAG DSL | 我们**不建议**引入：流程稳定、参与方固定，改成可编排只会把复杂度转嫁给使用者 |

---

## 5 · 建议的三个近期动作

| 优先级 | 动作 | 为什么现在做 | 估时 |
| --- | --- | --- | --- |
| P0 | 安全：每任务独立 HOME + `git` 报错抹 token（已修）+ QA 侧 `api_key` 加密对齐 Vega | 这是真实风险，不是架构问题 | 1 天 |
| P1 | MR head SHA 校验 | 堵住「评审被追加提交绕过」，改动最小 | 0.5 天 |
| P2 | 「打回方案」+ 返工上限 | 让错误能在正确的层被修正，而不是重跑同一个任务 | 1\~2 天 |
| P3 | 运行快照（模型/prompt/参数） | 让历史结果可解释，为后续 A/B 打底 | 0.5 天 |

---

## 6 · 事实来源与不确定项

**LoopForge**（源码级）： `docs/architecture.md`、`internal/domain/{workflow,run,agent}.go`、`engine/{node_execute,gate_mr,gate_submit,run_ops}.go`、`repo/{catalog,run_admission}.go`、`runtime/sandbox_manager.go`、`docs/deployment.md`、`docs/release-readiness.md`、issue #1039/#1038/#1074/#1079；CI 配置 `platform.yml`。

**经纬**（本仓库）： `api/vega/routers/{requirements,webhooks}.py`、`api/vega/{deploy,queue,runner,claude,llm,notify}.py`、`api/vega/models.py`、`api/vega/config.py`（CRG 嵌入配置）、`api/runner-claude.md`（runner 工具约定）、`deploy/README.md`（镜像内置 LSP/CRG/serena）、`qa/app/{ai_case,worker}.py`、`qa/app/api/{cases,runs,stats}.py`、`qa/tests/`。

**不确定项**：

- LoopForge 的真实模型交付效果、K8s/HA 方案未在其仓库中找到明确文档；
- 「无内置代码图谱/记忆」基于其 `internal/`、`engine/`、`runtime/` 与 `release-readiness.md` 的扫描，未逐包通读，若它有隐藏实现应以源码为准；
- 它是「内部上游 + 公开适配」导出，部分企业 adapter 属兼容代码，能力上限需以「可信团队自托管」为前提；
- 我们的生产运行状态未纳入本报告（只对比代码与设计）。

---

## 7 · 顺带修复

调研过程中在两个仓库里发现的真实问题：

1. **（已修）** `api/vega/runner.py::_git` 失败时把带 token 的远端 URL 写进异常串，会进日志、任务结果与群通知。 已加 `_scrub`（`oauth2:***@`），并补了 `api/tests/test_runner_scrub.py`。
2. **（待修）** `qa/app/models.py` 的 `SystemModelConfig.api_key` 明文落库，与 Vega 侧 Fernet 加密不一致。 建议与 P0 一起做，注意历史数据迁移。
3. **（待清理）** 死状态：`planned/archived`、`pushed/cicd` 从不写入却仍在模型注释与前端渲染，容易误判流程。
