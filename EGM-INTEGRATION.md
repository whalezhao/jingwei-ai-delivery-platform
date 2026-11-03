# Vega × EGM 集成方案

> 目标：把 [google/elephant-goldfish-model](https://github.com/google/elephant-goldfish-model)（Apache-2.0）的**全部 7 个技能**接进 Vega 的「需求 → 方案 → 开发 → 评审 → 合入」链路，而不是只在聊天框里多一个 `/egm-draft`。
>
> 本文基于 EGM `main` 分支的实际 SKILL.md / 插件清单，以及 Vega 当前实现（`api/vega/runner.py`、`claude.py`、`routers/requirements.py`）。文中标 **[待验证]** 的条目是没有在 246 runner 上实测过的假设，落地前必须先用第 7 节的 spike 确认。

---

## 0. 结论

EGM 不是一个「更会写文档的提示词」，而是一套**用零上下文子代理做对抗性评审**的流程。Vega 里唯一缺的正是这一环：现在的方案由人和同一个 AI 对话定稿，评审只有 MR 阶段的一次**代码 diff 初审**，没有任何人对**设计本身**做独立审查。

因此集成的主线是：

1. **方案讨论**从「自由聊天」换成 EGM 的 brainstorm → draft（分章节确认）；
2. **方案定稿前**插入 Goldfish 评审（fresh subagent，5 阶段）+ 结论修复轮；
3. **代码库上下文**（bootstrap）与**设计-代码漂移报告**（drift）作为项目级周期任务；
4. EGM 插件随代理镜像分发，仅 EGM 任务加载，不污染普通开发/初审会话。

EGM 是 Apache-2.0，Vega 是 MIT：插件原样 vendor 到独立目录、保留 LICENSE 与版权头即可，两边许可不冲突。

---

## 1. EGM 的真实契约（读源码后）

七个技能的共同前提：**Goldfish 必须是新上下文的子代理**。EGM 明说「如果 harness 不能创建子代理，评审就停下」——同一个上下文里自己复查不算数。

| 技能 | 输入 | 产物 | 需要子代理 | 可非交互 |
|---|---|---|---|---|
| `egm-brainstorm` | 概念描述（多轮问答） | `concepts_brief.md` | 是（6 个视角并行） | 否（必须先问清再开评审） |
| `egm-draft` | 前序文档 / 笔记 | `design_draft.md`，按章节停 | 否（主编排留在主上下文） | 半（每章一个确认门） |
| `egm-review` | `TargetFile`、可选上下文清单、`Step` | 各阶段评审报告 / `consolidated_review_report.md` | 是（5 阶段，每阶段多名评委） | 是（preset 1 = uninterrupted） |
| `egm-review-loop` | `TargetFile`、`SemanticTarget`、`Reviewers`、`Standards`、`GoldfishContext` | 评审报告 + 自动修复 | 是 | 是 |
| `egm-resolve` | `TargetFile`、`LineRange`、`FindingsList` | 改后的目标文档 | 否 | 是（参数齐全即静默执行） |
| `egm-bootstrap` | 目标目录 | 自底向上的 `README.md` 树 | 是（并行 worker） | 是 |
| `egm-drift-report` | 设计文档目录 | `{YYYY-MM-DD}_DRIFT_REPORT.md` | 否 | 是 |

另外三条会直接影响接入方式的硬约束：

1. **产物必须落文件**，禁止把大段正文直接吐在聊天里（Artifact-First）。EGM 在 Claude Code 下还支持 `CLAUDE.md` 里写 `EGM artifact directory: <path>` 来指定产物目录。
2. **`disable-model-invocation: true`**：这些技能**不能**被模型用 Skill 工具自动调用，只能由用户在消息里以 `/egm:<skill>` 触发；后续技能（resolve / review-loop）要求「读 SKILL.md 并在同一线程里照做」。
3. **无头运行要显式授权读取插件目录**，否则评委会因为读不到 persona 而停止，并报告「未完成评审」。EGM 明确反对用 `--dangerously-skip-permissions` 来绕过——评委应当只读。

五个评审阶段（`egm-review`）：Zoom Out → Comprehension → Design Critique → 中间 Zoom Out → Implementation Readiness。

---

## 2. Vega 现状与缺口

现有链路（`api/vega/routers/requirements.py`、`runner.py`）：

```
需求池 → 需求对话(mode=requirement) → 需求文档定稿
       → 方案对话(mode=plan) → 方案定稿(PlanVersion) → 派发 DevTask
       → claude 在 worktree 写代码 → push → MR
       → MR 初审(review_args, 工具全禁) → CI → 自动放行
```

三个缺口：

| 缺口 | 现状 | EGM 补什么 |
|---|---|---|
| 方案只经「同一个 AI」推敲 | 方案对话是自由聊天，AI 自己写、自己改 | brainstorm 的 6 视角压力测试 + draft 的分章节确认门 |
| 方案没有独立评审 | 定稿即派发；只有 MR 阶段看 diff | review 的零上下文 5 阶段评审 + resolve 修复轮 |
| 设计与代码会脱节 | 无任何机制 | drift-report 周期比对 |

**EGM 不替代 Vega 的 MR 初审**：MR 初审看的是代码 diff 与事实，EGM 没有代码评审技能，别硬套。

---

## 3. 总体设计

四层，逐层复用 Vega 已有机制：

```
┌─ 供给层 ────────────────────────────────────────────────┐
│ EGM 插件随 runner 镜像分发（/opt/egm），仅 EGM 任务加载   │
└─────────────────────────────────────────────────────────┘
┌─ 设计层（复用 plan 会话）──────────────────────────────┐
│ 方案讨论开「EGM 模式」：brainstorm → draft（逐章确认）    │
│ 产物落 runner 侧 artifact 目录 → 回传成 PlanVersion      │
└─────────────────────────────────────────────────────────┘
┌─ 评审层（新任务类型 egm_review）───────────────────────┐
│ egm-review(uninterrupted) → PlanReview(report_md)        │
│ → 用户逐条决定 → egm-resolve 回写设计文档（新版本）      │
└─────────────────────────────────────────────────────────┘
┌─ 维护层（项目级任务 egm_job）──────────────────────────┐
│ bootstrap：生成 README 树 → 分支 + MR                     │
│ drift：设计文档 vs 代码 → 报告 + octo 通知                │
└─────────────────────────────────────────────────────────┘
```

设计原则：**方案仍然是「人拍板才成版」**（Vega 现有规则），EGM 只负责把材料做厚、把问题挑出来。

---

## 4. 七个技能各自落在哪

### 4.1 `egm-brainstorm` — 方案讨论的「概念」阶段

- 触发：方案讨论页的「先头脑风暴」按钮，或用户第一句就带 `/egm:egm-brainstorm`。
- 运行：沿用 `plan_turn`，`mode=plan`，payload 加 `egm_skill="brainstorm"`；首轮 prompt 前置 `/egm:egm-brainstorm <需求标题+描述>`。
- 交互：EGM 要求多轮开放式提问（禁止选择题），正好对上 Vega「一条用户消息 = 一轮 claude -p」的节奏；用户直接在聊天框回。
- 产物：`concepts_brief.md` 写到 artifact 目录，回合结束后 runner 读回内容，前端展示为「概念稿」卡片，可一键「据此起草设计」。

### 4.2 `egm-draft` — 方案讨论的「设计」阶段

- 触发：概念稿卡片的「起草设计」，或方案页的「EGM 设计模式」开关。
- 运行：同 `plan_turn`；首轮 prompt 前置 `/egm:egm-draft <concepts_brief 路径>`。
- 分章节确认：EGM 每写完一节就停下等确认 —— Vega 天然支持（每轮一个 user message）。前端把「批准本节 / 要求修改」做成两个快捷回复，省得手打。
- 产物：`design_draft.md` 每轮被整体改写；runner 每轮结束读回全文，生成一个 **候选** PlanVersion（不自动定稿），沿用现有「采用此方案」按钮。
- 兼容：非 EGM 模式完全不变，仍走 `<!-- vega:plan:begin -->` 标记。

### 4.3 `egm-review` — 方案定稿前的评审

- 触发：方案卡片上的「Goldfish 评审」。
- 运行：**新 runner 任务类型 `egm_review`**，不进 plan 会话（评审必须零上下文，进已有会话就毁了前提）。
- prompt：`/egm:egm-review <design 绝对路径> --step uninterrupted` + Vega 适配段（产物目录、只读要求、上下文清单）。`uninterrupted` = preset 1，一口气跑完 5 阶段，不做人工检查点——检查点交给 Vega 的 UI。
- 权限：`Read,Grep,Glob,LS,Agent`，**不加写权限、不用 `--dangerously-skip-permissions`**（EGM 明确要求评委只读；写权限只给 artifact 目录）。
- 产物：`consolidated_review_report.md` → 新表 `PlanReview.report_md`，前端按 finding 列表展示（若报告里带 JSON 块则结构化，否则原样 Markdown）。
- 失败语义：评委会在读不到 persona/standard 时自报「未完成评审」——Vega 把这种结果记 `status=incomplete`，不当作「通过」。

### 4.4 `egm-review-loop` — 评审的底层驱动 / 高级入口

`egm-review` 内部会读它并照做，**不需要单独接**。额外给一个高级入口：在评审弹窗里选「只跑指定评委 × 标准」（如 `implementation_readiness` × `implementation-readiness-standard`），prompt 直接指向 `egm-review-loop`，用于方案改动后只复查某一维度，省 token。

### 4.5 `egm-resolve` — 评审结论的修复轮

- 触发：评审报告页的「按结论修订」。
- 运行：回到**方案会话**（Elephant 上下文，能记得当初为什么这么设计），prompt = 「读 `/opt/egm/skills/egm-resolve/SKILL.md` 并照做；TargetFile=<design>；FindingsList=<报告路径>」。
- 产物：`design_draft.md` 被定点修改 → runner 读回 → 新 PlanVersion。
- 适配：EGM 的 CONTEST / ESCALATE / PROPOSE FIX / DECLINE 四分类原样保留；ESCALATE（需要人拍板）在前端渲染成待办，不让 AI 自己拍。

### 4.6 `egm-bootstrap` — 项目级「生成代码库上下文」

- 触发：项目页按钮（管理员/项目负责人）。
- 运行：新任务类型 `egm_job`，`kind=bootstrap`；在项目常驻仓库的**独立 worktree** 里跑（它要写大量 README，绝不能碰共享基线）。
- 安全门：EGM 规定子目录 >15 必须先问人。runner 侧改成**预扫描**：先数目录，超阈值直接失败并在 UI 提示「请缩小范围」，不静默开跑。
- 产物：分支 `egm/bootstrap-<date>` + push + 建 MR（复用 `gitlab.py`），人来 review 后合入。**不直接写默认分支。**

### 4.7 `egm-drift-report` — 设计 vs 代码漂移

- 触发：项目页「漂移报告」按钮 + 每周定时任务（沿用 APScheduler）。
- 运行：新任务类型 `egm_job`，`kind=drift`；在 worktree 里把该项目所有**已定稿方案的正文**落成 `docs/designs/<req-code>.md`（仅本 worktree 内，不提交），然后跑 `/egm:egm-drift-report docs/designs`。
- 产物：`EgmReport(report_md)`；有差异时 octo 通知项目负责人 + 技术负责人（复用 `notify.py`）。

---

## 5. 关键改造点

### 5.1 插件供给：vendor + `--plugin-dir`（首选）

runner 镜像里已有「本地 marketplace」先例（`api/Dockerfile` 的 `/opt/claude-plugins`）。但 EGM 更适合用更轻的 **`--plugin-dir`**：

- `api/vendor/egm/` 存放 EGM 完整包（`plugin.json`、`.claude-plugin/`、`skills/`、`agents/`、`resources/`、`docs/`、`LICENSE`）；
- Dockerfile 加一行 `COPY vendor/egm /opt/egm`；
- 只有 EGM 任务的 `argv` 追加 `--plugin-dir /opt/egm`。

好处：普通开发/初审会话完全看不到这些技能（不占上下文、不可能误触发）；升级只换目录。
**回退方案**：若 `--plugin-dir` 在无头模式下不暴露 `/egm:*`，改用已验证的本地 marketplace 安装（与三个 LSP 插件同法）。

### 5.2 runner 新增两种任务与一套权限档

`api/vega/claude.py` 增三个 argv 构造器（现有 `plan_args` / `dev_args` / `review_args` 保持不动）：

| 场景 | 权限 | 说明 |
|---|---|---|
| `egm_plan_args` | `--allowedTools Read,Grep,Glob,LS,Agent,Write(<artifact>/**),Edit(<artifact>/**),Bash(curl *am/memory-bridge/*)` | 比 plan 模式多出**子代理**和**仅产物目录的写权限**；不加 `--permission-mode plan`（它禁写，draft 就废了） |
| `egm_review_args` | `--allowedTools Read,Grep,Glob,LS,Agent --max-turns 120` | 只读 + 子代理 |
| `dev_args` | 不变 | 开发任务本来就有全权限与子代理 |

`routers/queue.py` 的优先级改为 `mr_review 0 / plan_turn 1 / egm_review 2 / 其他 3`，保证 MR 初审不被评审饿死。

### 5.3 产物目录与基线保护（最容易出错的地方）

方案会话的 `cwd` 是**多任务共享的项目基线仓库**（`_query_dir`），写进去会弄脏基线，后续 fetch/checkout 全被挡。因此：

- artifact 目录固定在 `~/vega-runner/egm/req-<id>/`，通过 `--append-system-prompt "EGM artifact directory: <abs>"` + prompt 双保险告知；
- 写权限只授这个目录；
- 每轮结束 runner 执行 `git status --porcelain` 断言基线干净，脏了直接判任务失败并告警（这是唯一能兜住模型越界写的地方）。

### 5.4 数据模型（新增两张表）

```python
class PlanReview(Base):
    __tablename__ = "plan_review"
    id, requirement_id, plan_version_id, runner_task_id
    status            # running / done / incomplete / failed
    report_md: Text
    findings: JSON    # 可选，能解析出结构化条目时填
    created_at

class EgmReport(Base):
    __tablename__ = "egm_report"
    id, project_id, kind   # bootstrap / drift
    status, report_md: Text, branch: str | None
    created_at
```

`RunnerTask.type` 是 `String(16)`，`egm_review`(10) / `egm_job`(7) 都放得下，**不需要迁移列**。新增表由现有建表逻辑创建（`db.py` 的 `create_all`），老库无破坏。

### 5.5 接口与前端

| 接口 | 作用 |
|---|---|
| `POST /requirements/{id}/plan/messages`（改） | body 加 `egm: "brainstorm"\|"draft"\|"resolve"`，决定首轮注入哪个 `/egm:` 命令 |
| `POST /requirements/{id}/plan/review` | 入队 `egm_review` |
| `GET /requirements/{id}/plan/reviews` | 报告列表（含过程状态） |
| `POST /projects/{id}/egm-jobs` | `kind=bootstrap\|drift` 入队 |

前端只动两处：
- `RequirementChat.tsx`：方案模式加「EGM 设计模式」开关 + 评审按钮 + 报告抽屉 + 「按结论修订」；
- `Projects.tsx`：两个按钮 + 报告列表。

`TaskDetail.tsx` 的事件回放可原样复用（runner 仍然逐事件 `_emit`）。

---

## 6. 兼容性、风险与验证方式

| 风险 | 影响 | 处置 |
|---|---|---|
| **模型能力** | 默认走 agent-memory proxy 的 27B 级模型（`am_default_model`）。Goldfish 的价值来自「新上下文」，不是「更强模型」；弱模型给出的批评会很水 | 给研发组单独配 EGM 专用 `llm_model`（现有三级解析链已支持），并在方案里标注「评审质量取决于该模型」 |
| **上下文窗口** | 128144 tokens 上限（`claude_max_context_tokens`）。5 阶段 × 多名评委 × 读仓库文件，极易撑爆 | 上下文清单限定**具体文件**而非目录；设计文档超过阈值时先拆；单评委失败按 `incomplete` 记，不算通过 |
| **权限与基线** | draft 要写文件，而 plan 模式禁写、基线仓库禁写 | 5.3 的 artifact 目录 + 写权限白名单 + `git status` 断言 |
| **子代理可用性** | EGM 的全部评审价值依赖子代理。`plan` 权限档当前**不允许** Agent | 新增专门的 `egm_plan_args`；开发任务已证明子代理链可用（`CLAUDE_CODE_FORWARD_SUBAGENT_TEXT`、`parent_tool_use_id` 都在用） |
| **无头读插件** | 评委读不到 persona 会停止 | 把插件目录加入 Read 允许范围；把「未完成评审」当失败而不是通过 |
| **CLI 版本** | `omitClaudeMd` 需要 Claude Code ≥ 2.1.271 | Dockerfile 固定 `@anthropic-ai/claude-code@<版本>`，不做 latest 漂移 |
| **成本与时长** | 一次完整评审 ≈ 30 次子代理调用，27B 本地推理可能 10–40 分钟 | 队列低优先级、可停止（复用现有 stop 机制）、UI 显示阶段进度；提供「只跑单维度」的省 token 入口 |
| **上游漂移** | vendor 的副本会过期 | 记录上游 commit sha + 版本号；升级走一次 PR，不自动跟随 |
| **许可** | EGM 是 Apache-2.0，Vega 是 MIT | 独立目录保留 `LICENSE`、版权头与 NOTICE；不把 EGM 代码混进 Vega 源文件 |

**验证方式（不是「跑通测试」就算完）**：
1. 单元层：`api/tests` 加 `egm_plan_args` / `egm_review_args` 的 argv 断言、artifact 目录越界写的失败用例；
2. 集成层：mock claude 产出一份伪 `design_draft.md`，验证「读回 → 候选版本 → 人确认才定稿」；
3. 真机：在 246 上跑一次完整 draft + review，人工核对报告里是否真的引用了设计文档的行号与仓库文件——这是唯一能判断「评委有没有真读」的证据；
4. 基线保护：故意让模型往仓库里写，确认任务失败且基线 `git status` 干净。

---

## 7. 落地前必须先做的三个 spike

1. **`--plugin-dir` 在 `-p` 下是否暴露 `/egm:*`**：造一个最小插件，`claude -p --plugin-dir <dir> "/demo:skill"` 看是否展开。失败则退回本地 marketplace 安装（Dockerfile 里已有先例）。
2. **slash 参数能否传 `Step=uninterrupted`**：不行就在 prompt 里用自然语言显式写「执行 preset 1：uninterrupted 全流程」，不要赌参数解析。
3. **子代理在受限 `--allowedTools` 下能否继承只读工具**：用 `egm-review` 跑一份两页的假设计文档，确认 5 个评审员都产出了报告而不是「读不到文件」。

> 本机当前无法执行 spike 1/3：本机 `claude` CLI 未登录（`Not logged in`），且 GitHub 直连 TLS 被断，EGM 源码只能经 `gh api` 读取。spike 必须在 246 runner 上做。

---

## 8. 分期与验收

| 期 | 内容 | 验收 |
|---|---|---|
| P0（0.5 天） | vendor + 镜像 + 三个 spike | `/egm:*` 在 runner 上可见；最小 draft 能写出文件 |
| P1（2 天） | brainstorm + draft 接入方案会话 | 一条需求从概念稿走到候选方案，全程不脏基线 |
| P2（2–3 天） | `egm_review` + `PlanReview` + resolve | 评审报告能指出设计里的具体缺口；修订产出新版本 |
| P3（2 天） | `egm_job`：drift + bootstrap | 漂移报告能定位到具体文件；bootstrap 产出可合入的 README MR |

每期都可独立上线，P2 之前系统行为与现在完全一致（EGM 开关默认关）。

---

## 9. 明确不做

- **不替换 MR 代码初审**：EGM 没有代码评审技能，硬套只会得到一份没有 diff 依据的空泛意见。
- **不让 AI 自动定稿方案**：EGM 的分章节确认门、resolve 的 ESCALATE，最终都由人拍板——这与 Vega 现有「人点采用才成版」一致。
- **不自动合并 bootstrap 产物**：README 树会改动大量文件，必须走 MR。
- **不把 EGM 技能装进全局会话**：避免每个开发任务都背着七个技能的上下文。
- **不跟进上游自动升级**：固定 sha，人工评审后升级。