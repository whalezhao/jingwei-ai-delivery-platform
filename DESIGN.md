# 经纬 · 研发交付系统 · 技术选型与设计方案（v3）

> 系统定名：**经纬 / JINGWEI**（原名 Vega / 织女星）。「经」是流程与标准，「纬」是每一条需求；仓库/目录/服务名仍统一用 `vega`（vega-api / vega-web / vega-runner）。
>
> 目标：一套承接「人员 → 研发组 → 项目 → 需求 → 计划 → AI 开发任务 → MR → CI → 通知」全流程的服务化系统，替换目前散落在 Hermes cron 里的脚本化自动化。
>
> v2 变更：① 前端定为 Ant Design Pro V6 + Ant Design X（对话流式）；② runner 改为**可多节点部署**的独立服务；③ 明确 LLM 解析链（人员 → 研发组兜底，CICD/开发按研发组归属）；④ octo↔GitLab 成员匹配定为 **AI 自动对配 + 人工随时调整**。
>
> v3 变更（2026-10-25，对齐实际实现）：① 技术栈落地修正——前端 Vite + React 18 + antd 5（不用 Umi）、DB 开发 SQLite / 部署 PG、GitLab 用自研 httpx 薄客户端；② webhook 收敛为**全局单址** + 组 secret 识别；③ 身份同步升级为**三方对配 v2**（GitLab 权威建档 + octo 兜底建档 + 同人合并）；④ 新增 §4.8 **MR 初审与自动放行**；⑤ 数据模型补全已实现字段与 mr_review / dev_task_event 表；⑥ runner 改常驻项目 clone + bootstrap 装机命令。

---

## 0. 已验证的现有资产（设计依据，非猜测）

以下均在 2026-10-24 实际核实：

| 资产 | 现状 | 对本系统的意义 |
|---|---|---|
| **GitLab** | `git.example.com`，**v13.12.15**，API v4，PRIVATE-TOKEN 认证 | webhook 可用，但 **13.12 不发 merge 事件**（Hermes 已实测，mr-merge-watch 就是为此改为轮询）；pipeline 事件可用。管理员 token 可扫到 65+ 项目（MIN_ACCESS=40） |
| **agent-memory proxy** | `127.0.0.1:8096`，URL 形如 `http://…:8096/claude-code/<spaceId>/v1/messages` | ① 身份绑定头：`x-team-id` + `x-agent-id`，**源码已确认 task 可选**（team+agent 齐即可直接注册 session，无表单）② 大模型直连头：`p-url` + `p-api-key`（hdr2 镜像已上线，p-* 头不透传上游）③ 客户端鉴权：`ANTHROPIC_AUTH_TOKEN` = 业务用户 user_key（sk-mem-…） |
| **Claude Code 接线方式** | cc-switch 的 Agent-Memory provider 已在用 | `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` + `ANTHROPIC_CUSTOM_HEADERS`（换行分隔的 `name: value`）+ `ANTHROPIC_MODEL`，即插即用，`claude -p` 同样吃这套 env |
| **octo** | bot HTTP API（`OCTO_API_URL` + `OCTO_BOT_TOKEN`），`GET /v1/bot/groups/{group_no}/members` 拿群成员 uid→昵称，发消息带 mention entities（UTF-16 offset）实现 @ | 成员同步、@通知都有现成协议，`im_send.py` 是可移植参考实现 |
| **aster-qa 前端** | React 18.3.1 + Vite + antd 5 + pro-layout（从镜像 bundle 指纹核实） | 新系统用 antd5/ProLayout 同一设计语言，风格天然一致 |
| **Hermes 脚本库** | `gitlab_daily_commit_stats.py`（aiohttp 并发 32，实测 68 项目 6s）、`mr_blockers.py`、`mr_autoflow.py`（AI 初审+自动放行）、`ci_fix*.py`（CI 失败修复闭环）、`mr-merge-watch`（merge 轮询兜底） | 业务逻辑直接移植，不是从零写 |
| **Git 门禁** | `~/.ai/git-flow.sh`：feature 分支规范、Angular 提交规范、载体分支 FF 合入 nightly、AI 不得自行 merge | runner 内置同样的门禁逻辑，AI 只能 commit + 建 MR，合并留给人 |
| **内网主机 服务器** | Docker 就绪，已跑 memory 栈/deepwiki/new-api/aster-qa；**宿主机无 node**（node 相关构建要走容器） | api/web/db 部署落点；首个 runner 节点也放这，后续可加任意节点 |

---

## 1. 技术选型

| 层 | 选择 | 理由（一句话） |
|---|---|---|
| 后端 | **Python 3.12 + FastAPI + Uvicorn** | 团队生态全是 Python（Hermes 全部脚本），aiohttp/httpx 模式现成；async 天然适合「任务队列 + webhook + 定时任务 + SSE」并存 |
| ORM/迁移 | SQLAlchemy 2 async（开发期 `create_all` 建表） | 标配；部署 PG 后再引 Alembic 做版本化迁移 |
| 定时任务 | APScheduler（进程内 AsyncIOScheduler） | 扫描类任务都是分钟~天级 |
| 任务队列 | **DB 表队列**（PG `FOR UPDATE SKIP LOCKED` / SQLite 回退写认领） | runner 拉取式派发只需要 DB，不引入 MQ |
| DB | 开发 **SQLite**（aiosqlite，零运维）/ 部署 **PostgreSQL 17**（独立容器） | 同一套 async ORM 双方言兼容，换库只改 `DATABASE_URL` |
| 前端框架 | **Vite 5 + React 18 + antd 5 + pro-components（ProLayout）** | 管理端 CRUD/列表/表单全家桶；构建快、不绑 Umi 脚手架 |
| AI 对话组件 | **Ant Design X**（`@ant-design/x` **2.5.0**：Bubble / Sender / Prompts / ThoughtChain + XRequest·XStream） | 官方 AI 对话组件，XRequest/XStream 原生吃 SSE 流；ThoughtChain 承载 AI 过程思考链（原生状态图标/折叠/闪烁） |
| 实时通道 | **SSE**（plan 会话 + runner 日志流式回传） | 单向流足够，比 WebSocket 简单一个量级 |
| GitLab 客户端 | 自研 httpx 薄客户端（`vega/gitlab.py`，PRIVATE-TOKEN） | 13.12 老版本字段自己控；统计类并发沿用既有 gather 模式 |
| IM | octo bot API 直连（移植 im_send.py 核心逻辑为模块） | 已验证协议；不依赖 Hermes 网关进程存活 |
| AI 执行 | **Claude Code CLI**（`claude -p`，装在每个 runner 节点内） | 需求原文指定；plan 会话与开发任务同一套 CLI |
| 部署 | api/web/db 跑 内网主机（compose）；**runner 独立镜像，可部署任意多节点** | 容量 = 加 runner 节点，api 不动 |

**明确不引入的**：消息队列（Redis/Kafka）、微服务拆分、k8s。规模用不上，出问题全是运维成本。

---

## 2. 总体架构

```
 浏览器 ──HTTP/SSE──▶  web (nginx, :8180)
                       ├─ 静态 SPA (Vite + React 18 + antd 5 + Ant Design X)
                       └─ /api 反代 → api

 GitLab webhook ─────▶ api (FastAPI, 内网主机)
 (push/MR/pipeline)    ├─ REST: 人员/组/项目/需求/任务 CRUD
                       ├─ /webhooks/gitlab（全局单址，X-Gitlab-Token
                       │   = 组 webhook_secret 识别归属组）
                       ├─ /api/requirements/{id}/plan/stream (SSE)
                       ├─ /runner/*  ← 注册/心跳/长轮询领任务/回传事件
                       ├─ APScheduler: GitLab 扫描 / octo 成员同步
                       │              / merge 轮询兜底 / 日报 / MR 自动放行
                       │              / SQLite 备份
                       └─ 通知器: octo bot API（组专属 bot token，全局兜底）
                              │
                       DB：SQLite(开发) / PostgreSQL(部署)
                       （业务数据 + 任务队列）
                              ▲
        ┌─────────────────────┼──────────────────────┐
        │ 长轮询拉任务 + 回传日志（只需出网访问 api）     │
   runner 节点 #1          runner 节点 #2         runner 节点 #N
   (内网主机, 首个)             (任意服务器/机器)        (团队扩容=加机器)
   node:22 + claude CLI + git
   常驻项目 clone + worktree + CRG daemon
        │
        ▼
   agent-memory proxy (内网主机:8096, 复用现有)
   x-team-id / x-agent-id / p-url / p-api-key
```

数据流三条主线：
1. **事件线**：GitLab webhook → api → 按项目归属定位研发组/人员 → octo @通知（成功静默、失败触发 LLM 分析）。
2. **计划线**：人员认领需求 → UI 多轮对话 → 任务队列派到**粘性 runner** 执行 `claude -p --permission-mode plan`（经 agent-memory 绑定 team+agent）→ 沉淀执行计划。
3. **执行线**：确认需求 → 生成任务编号 → runner 领任务，在本地独立 worktree 开发 → push 分支 → api 建 MR → octo @下发人审核 → CI webhook 闭环。

---

## 3. 数据模型（核心表）

```
person            人员：name, password_hash(PBKDF2 登录), gitlab_username, gitlab_email,
                  octo_uid(可空,对配回填), am_agent_id, am_user_key(加密,agent-memory
                  user_key), llm_base_url, llm_api_key(加密), llm_model,
                  llm_effort(独立解析链), is_admin
dev_group         研发组：name, gitlab_base_url, gitlab_token(加密),
                  llm_base_url, llm_api_key(加密), llm_model, llm_effort,
                  octo_group_no, octo_bot_token(加密,可空=回退全局 bot),
                  am_team_id, mr_target_branch(放行目标分支,默认 nightly),
                  webhook_secret, scan_enabled, auto_webhook
group_member      组-人关联 + octo_nickname + match_status(auto_exact/auto_ai/
                  pending/manual/unmatched) + match_confidence + match_reason
                  + manual_lock(人工设定永久锁) + am_agent_id
project           项目：group_id, gitlab_project_id, path, name,
                  code_prefix(编号前缀), default_branch,
                  audit_prefixes(JSON, MR 初审触发), auto_approve_prefixes
                  (JSON, 自动放行白名单), am_agent_prefix
project_pm        项目-项目经理关联（多人，日报收件 + MR 阻塞兜底 @）
requirement       需求：project_id, code(REQ-<项目码>-<年月>-<序号>), title,
                  status(pool/claimed/planned/confirmed/archived),
                  claimant_id, plan_session_id, plan_runner_id(粘性路由), plan_md(定稿)
requirement_doc   需求文档（多版本）：requirement_id, filename,
                  content(text/md 提取后), version(需求内自增), is_final(定稿,需求内唯一)
dev_task          任务：requirement_id, code(TASK-<需求码>-<序号>),
                  status(pending/queued/running/pushed/mr_created/reviewing/
                         cicd/done/failed), creator_id(=认领人),
                  title(任务标题，finalize 单任务时=需求标题), scope(实现范围,手工补建任务用),
                  branch, mr_iid, mr_url, error, escalated_level(MR 无人处理升级 0~3)
dev_task_event    开发任务执行事件留痕（runner 回传）
plan_message      计划会话消息：requirement_id, role(user/assistant), content
plan_version      方案版本：requirement_id, version, content(md 快照),
                  is_final(finalize 时定标；每轮 AI 修改自动成版)

runner_node       runner 节点：name, token_hash(sha256, 明文只在发放时
                  返回一次), slots(遗留列，已不参与调度), last_heartbeat, status
runner_task       任务队列：type(dev_task/plan_turn/mr_review), payload(jsonb),
                  status(queued/claimed/running/done/failed),
                  runner_id(认领者, plan 会话粘性回写),
                  secrets(jsonb, 组 GitLab 凭据随任务内存态下发), enqueued_at

mr_review         MR 初审：UniqueConstraint(project_id, mr_iid, head_sha) 幂等，
                  verdict(approve/modify/failed), summary, issues(jsonb),
                  changed_files(jsonb), gate(CI 门禁, ""或"ci:状态"),
                  commented, flow_state(''/merged/conflict_notified/
                  hold_not_mergeable), error
gitlab_event      原始事件留痕：group_id, event_type, payload(jsonb), handled_at
notify_log        通知记录：group_id, octo_chat, mentioned_uids, content,
                  reason, ok
daily_stat        每日提交统计（项目×人）：date, project_id, person_id,
                  commits, additions, deletions
```

**编号规则**：需求 `REQ-<项目码>-YYMM-NNN`，任务 `TASK-<需求号>-N`（如 REQ-PMS-2610-007 → TASK-…-007-2），外键关联，MR 描述自动带任务号。

---

## 4. 核心流程设计

### 4.0 LLM 解析链（全局规则）

所有 claude -p / 一次性 LLM 调用，先按场景定链条，再按「项目 → 研发组」归属取组配置：

| 场景 | 解析链 |
|---|---|
| plan 会话（需求认领对话） | **认领人 LLM → 研发组 LLM → proxy 默认（Qwen3.8）** |
| 开发任务（AI 写代码） | **认领人 LLM → 研发组 LLM → proxy 默认** |
| CI 失败分析 | **研发组 LLM → proxy 默认**（按项目归属的研发组） |
| 组级分析（MR 初审、日报摘要等） | **研发组 LLM → proxy 默认** |

实现为一个纯函数 `resolve_llm(scope) → (base_url, api_key, model)`：命中人员/组配置时拼 `p-url`/`p-api-key` 头 + `ANTHROPIC_MODEL`；未命中则不发 p-* 头走 proxy 默认上游。LLM 配置优先级：**人员 > 研发组 > proxy 默认**，三层都是「配了就用、没配就往下落」。

### 4.1 研发组 GitLab 扫描（定时，每 30 分钟）

1. 用组 token `GET /projects?min_access_level=40` 拉有管理权的项目，新项目入库并**自动配置 webhook**（push / merge_request / pipeline 事件，URL=`http://内网主机:8180/webhooks/gitlab` **全局单址**，secret_token=组的 webhook_secret，靠 secret 识别归属组；幂等：已存在同 URL 的 hook 跳过；扫到历史单组地址（`/webhooks/gitlab/{id}`）的旧 hook 顺手清掉）。
2. **每日提交排行**（每日 09:05）：移植 `gitlab_daily_commit_stats.py` 的 aiohttp 并发统计，写 daily_stat，推 octo 群 + 私发项目经理。
3. **阻塞 MR 扫描**（每日 09:00 + 每小时增量）：open 超 N 天无 approve/无动态 → @assignee；超 M 天 → 兜底 @项目经理（移植 `mr_blockers.py` 逻辑）。
4. 扩展分析（二期）：代码活动简报、死代码扫描——Hermes 都有现成脚本。

### 4.2 身份同步与三方对配（v2）

身份有三种来源，职责分明：

- **GitLab 是研发身份的权威来源**：每小时扫本组全部项目成员并集，新 username 自动建 Person + 挂组；已有 username 不重建。
- **octo 群成员不直接建档**——群里有 bot/外人，直接建会污染人员表；octo 身份靠对配挂到 Person 上（bot 在 octo 解析层即被滤掉）。
- **octo 兜底建档**：对配跑完后群里仍无人认领的人类成员才建 Person 并 `manual_lock` 锁定，不和 GitLab 建档/自动对配抢人。

**同人合并**：先被 octo 兜底建档的人（无 gitlab_username）后来被 GitLab 扫到时，仅当「唯一精确同名且无 gitlab_username」才**复用回填**（gitlab_username/email 写进原记录，octo 绑定不动）；同名歧义或昵称改名不自动猜，留给成员抽屉的人工绑定（gitlab-match：username 唯一、被占 400、绑定时回填邮箱、可解绑）。

- 每小时 `GET /v1/bot/groups/{group_no}/members` → uid/nickname 入 group_member（组专属 bot token 优先，未配回退全局）。
- **三段式匹配流水**（触发时机：每小时成员同步后 + 后台「重新对配」按钮 + 组保存 gitlab/octo 配置变更自动触发）：

```
① 规则精确匹配（零成本，先跑）
   gitlab_username == octo nickname（忽略大小写/下划线/横线）
   gitlab_email 前缀 == octo nickname 等
   → match_status=auto_exact

② AI 对配（①没配上的残料，交给研发组 LLM，按 resolve_llm 解析）
   输入：两边未匹配名单
     - 组内人员：gitlab_username / gitlab_email / 姓名
     - 群内成员：octo uid / nickname
   输出（JSON）：配对 + 置信度(0~1) + 一句话理由
   置信度 ≥ 0.8  → 直接生效，match_status=auto_ai
   置信度 < 0.8  → 进待确认列表，match_status=pending
   两边都点名但模型说配不上 → unmatched

③ 人工调整（随时，最终裁决权）
   后台「成员对配」页：每人一行，octo 身份下拉可改可清空；
   人工设定 → match_status=manual，**永久锁定，后续任何自动对配
   （规则或 AI）都不许覆盖**；只有人工主动改才变化。
```

- AI 对配结果连同理由展示在调整页（「模型认为张三=群里的 zhangsan.five，因为…」），人工一眼核对该不该放行。
- 匹配成功回填 `person.octo_uid`，@通知就通了；webhook 事件里的作者（pusher email / MR author）→ person → octo_uid → @。

### 4.3 需求 → 计划（claude -p plan 模式，经 runner 执行）

```
浏览器输入 ──POST /api/requirements/{id}/plan/messages──▶ api
  api: 无 session_id → 生成 UUID；无 plan_runner_id → 入队 plan_turn(不带粘性约束)
       有 plan_runner_id → 入队 plan_turn(sticky=该 runner)
       该节点已不存在 → 解绑并作废会话，按「无 plan_runner_id」入队，首轮带该模式全量历史（自愈）
  runner 领到任务:
    spawn: claude -p --session-id <uuid> --resume <uuid> \
             --permission-mode plan \
             --output-format stream-json --verbose \
             "<用户消息>"
    env: ANTHROPIC_BASE_URL=http://127.0.0.1:8096/claude-code/default
         ANTHROPIC_API_KEY=<认领人的 sk-mem user_key>   # 或平台公共业务用户
         ANTHROPIC_CUSTOM_HEADERS="x-team-id: <组的 am_team_id>\n
                                   x-agent-id: <认领人的 am_agent_id>\n
                                   p-url: <resolve_llm 命中值>\n
                                   p-api-key: <resolve_llm 命中值>"   # 未命中省略 p-*
         ANTHROPIC_MODEL=<resolve_llm 命中值>
    stdout(stream-json) 逐事件 POST 回 api ──▶ api SSE ──▶ 浏览器(Ant Design X 渲染)
  首次认领后 requirement.plan_runner_id = 该 runner（Claude 会话文件在 runner 本地，
  后续轮次必须回到同一节点）；runner 掉线 → 用已落库的 plan_message 历史重开会话
  会话消息同时落库 plan_message，多轮即多次派发 --resume 同一 session-id
```

要点：
- **不带 `x-task-id`**（已核实：proxy 的 preset 逻辑 team+agent 齐即可直接注册，task 可选）。
- 首轮系统提示自动拼：需求标题 + 已上传 doc/md 全文（取 **is_final 定稿版**，未定稿时取最新版）+ 项目上下文（分支规范、代码门禁摘要）。
- **需求文档多版本**：同一需求可多次上传（version 需求内自增），首个版本自动定稿；
  之后人工点选切换 is_final（需求内唯一）；前端 md 直接渲染、txt 原文、docx 走
  /docs/{id}/download + mammoth 浏览器端转 HTML 预览。
- **方案多版本**：runner 每完成一轮 plan 回复，api 同时落 plan_message（会话）与
  plan_version（md 快照，version 自增）——AI 输出即自动成版，对话里**没有确认按钮**。
  人在预览区挑一版 POST /plan-versions/{id}/finalize 标记 is_final（需求内唯一）。
- finalize → plan_md 定稿、requirement.status=confirmed；**一个需求 = 一个任务**：
  finalize 建单个 dev_task（title=需求标题）入池。
  更大粒度的并行拆解不在这里做——主 agent 执行期用 Task 工具自开子 agent，
  子 agent 进展经事件 parent 字段外露（见 4.4），平台不替 AI 拆需求。
- **页面布局以预览为主 + 右侧常驻 AI 栏**：需求详情页主区 = 方案/文档预览
  （顶部定稿按钮，与文档定稿同款交互），AI 对话是页面最右侧的**常驻停靠栏**
  （占位不遮挡、默认展开、随页滚动吸顶，栏头「»」可收起，收起后卡片头部
  出现 💬 AI 对话按钮）；任务详情页同款——主区 = 定稿方案 + 定稿需求 +
  同需求任务，AI 工作进展（会话树 + 主/子泳道思考链）在右侧常驻栏（默认展开）。
  思考链 = Ant Design X ThoughtChain（**v2.5.0**）全量用法：tool_use/tool_result
  就地配对成节点，原生 status（loading 转圈 / success ✓ / error ✗ / abort ⊖，
  任务失败时遗留 loading 标 abort）、per-item collapsible（输入+返回正文可折叠）、
  description（入参首行）、footer（返回摘要/子代理 token 用量）、blink（活跃
  节点闪烁）、子代理（task_progress）也作为链节点展示。

### 4.4 任务 → AI 开发（任务池抢单 + runner 本地 worktree 隔离）

```
api 侧（确定性动作，不交给 AI）：
  1. finalize 建单个 dev_task（title=需求标题，无 scope=无并行边界约束），入队
     runner_task(type=dev_task)，状态 pending→queued；组 GitLab 未配置则留 pending
  2. 任务池抢单：任意在线 runner 长轮询 /api/runner/next 认领
     （PG FOR UPDATE SKIP LOCKED / SQLite 单写者互斥，天然不重复派），
     认领即 dev_task.status=running、执行节点名对 UI 可见
  3. 等结果；runner 回报 pushed 后 → 用组 token 走 git-flow 门禁建 MR
     （target=nightly，assignee=认领人，描述带任务号/需求号），mr_url 落库
  4. octo @认领人：「任务 TASK-…-1 完成，MR 待审核：<url>」
  5. MR 终态（自动放行合并 / 人工合并 / 被关闭）→ 任务 done，octo @认领人毕业通知
     （mr_merged / mr_closed，notify 留痕即毕业凭证）

runner 侧（领到 dev_task 后）：
  1. 常驻项目 clone：repos/<url sha1[:16]>（非 bare；首次 clone / 后续 fetch，
     token 不写 remote、按显式 URL 拉取），checkout --detach 触发 CRG daemon
     增量建图，首次 `crg-daemon add` 注册
  2. git worktree add ~/vega/worktrees/<task_code> \
       -b feature/<版本>/<认领人>/<需求ID>/t<N> origin/master
     （N=任务序号：同需求多任务并行 push 分支互不覆盖）
  3. claude -p --dangerously-skip-permissions \
       --output-format stream-json --max-turns 200 \
       "<任务提示词：任务标题 + 任务范围 + 并行边界约束（只做本任务范围，
        其他任务由别的 AI 并行实现）+ 定稿计划 + git 门禁规范 + 完成后只 commit 不 push>"
     （env 同 4.3，LLM 按 resolve_llm 解析；am_agent_id=认领人 agent）
  4. 容器退出 → 校验分支上有新 commit → git push（组 token 作 HTTP 凭据）→ 回报 pushed
```

要点：
- **worktree 每任务独立目录**：AI 进程只看得见自己的 worktree，物理上碰不到别的分支和代码，满足「不影响别的分支」。
- **push 由 runner 确定性执行、建 MR 由 api 确定性执行**（GitLab API 调用集中在 api，token 不散落）；分支规范、Angular 提交规范、FF 语义照搬 `git-flow.sh`。AI 只负责写代码 + commit。
- 合并永远留给人（Git 门禁第 41 条精神）。
- **开发任务提示词只注入定稿方案**（plan_md + Git 门禁 + 任务编号 + 分支信息），不再带需求描述原文，避免与定稿计划打架；需求文档同样只附**定稿版**。
- **多 agent 协作可见（主/子 agent 泳道）**：claude -p 以 stream-json 运行时，
  子 agent（Task 工具）的消息天然出现在流里并携带 `parent_tool_use_id` 指回派生
  它的 tool_use 块（主会话为 null；官方 headless 文档「Follow subagent messages」），
  嵌套子 agent 逐级带 ID 可重建协作树。runner 侧设
  `CLAUDE_CODE_FORWARD_SUBAGENT_TEXT=1` 让子 agent 文本/思考也进流（默认只有
  工具事件）；claude.py 归一化事件统一附 `parent` 字段（tool_use 另带 `id`），
  全链路透传：dev_task_event.payload 落库、GET /events 展开、SSE dev_raw 直播。
  plan 会话的文本累积与 plan_text 直播只吃主会话事件（parent 为空），子 agent
  输出不污染方案定稿内容。工作台对活跃任务聚合 agents={sub,events}（按 parent
  去重得子 agent 个数）；任务详情页按泳道渲染。
- **plan 会话过程直播（plan_raw 频道）**：runner 回传的事件在 post_events 分流——
  开发任务走 dev_raw；plan 任务（无 dev_task_id）的归一化块（thinking/tool_use/
  tool_result，parent 为空）与 claude 原始 system 事件都走 plan_raw 直播。其中
  `thinking_tokens`（estimated_tokens 计量）驱动前端思考块的 token 计数，
  `task_progress`（携带 tool_use_id/description/subagent_type/usage）即**子代理
  实时进展**，按 tool_use_id 分组就是会话树的子节点（主 agent 开的每个子会话一行）。
- **需求对话页 = Ant Design X 全家桶**（/reqs/{id}）：Bubble.List 每项用足
  avatar（用户/Vega AI 头像）、header（署名）、messageRender（Markdown 渲染）、
  typing（流式回复打字机）、loadingRender（等待首字节的骨架加载泡）；过程区 =
  可折叠思考块（live token 计量 / 已思考）+ 子代理进展行（Explore · 描述 ·
  N 工具 · token 数，活跃行呼吸点）+ ThoughtChain 工具链；顶部「已引入」文件卡片
  （定稿需求文档 + 最新方案快照，点击右侧预览）。SSE 重连回放时最后一条是 user
  则恢复 busy 等待态——刷新页面思考块不丢。
- **任务详情页**（/tasks/{id}）：聚合端点 GET /api/dev-tasks/{id}/detail 一次给全
  任务头（状态/节点/MR/分支/范围/错误）+ 定稿需求（is_final 文档，前端按类型预览）+
  定稿方案（plan_version is_final 快照，旧数据回退 plan_md）+ runner_task_ids
  （SSE 过滤键）。AI 工作进展 = **Claude 式会话树 + Ant Design X ThoughtChain**：
  主会话行（任务标题，可折叠）+ L 形连线挂子会话行（Task 描述命名 + 事件数 +
  活跃呼吸点），点击切换下方该会话的 ThoughtChain（工具调用/思考/输出/结果全
  状态链，可折叠）；数据 = SSE 实时推 + 3s 轮询兜底，终态断流。
- 节点装机：`python -m vega.runner bootstrap` 幂等装全链工具（brew/node/jq/ast-grep/uv/code-review-graph/LSP/claude CLI + CRG daemon 拉起）；当前探测链依赖 brew，**Linux 节点支持待补**。

### 4.5 runner 多节点协议

```
POST /runner/register    {name} → {runner_token}             # 管理员审批制发放
POST /runner/heartbeat   → {server_time}                     # 30s 一次，超 90s 判 offline
GET  /runner/next        (长轮询 25s, Bearer runner_token)
                          → {task_id, type, payload}         # SKIP LOCKED 认领
POST /runner/tasks/{id}/events  (stream-json 事件批量回传)
POST /runner/tasks/{id}/result  {status, branch?, mr_ready?}
```

- **拉取式**：runner 只需能出网访问 api（NAT 后的机器也能当节点），无需暴露端口。
- **容量模型**：runner 不设并发上限——领取即执行，一个任务一个 claude 子进程，能同时跑几个由上游模型（agent-memory proxy）的并发决定。api 按优先级 + FIFO 派发，**不做全局并发节流**。每个任务（含 MR 评审）各自一个 worktree，共享仓库只做 fetch；**只读问答例外**：直接跑在常驻基线目录（CRG 图按绝对路径索引，worktree 借不到图），基线 HEAD 恒钉默认分支，工具白名单只给 Read/Grep/Glob/LS + memory-bridge 检索 curl，仓库拉不下来就退到需求文档目录；同仓库的 git 操作、同一需求的会话轮次在节点内串行（共享资源，不是限额）。
- 任务超时（runner 心跳在、任务卡死）→ `claimed/running` 超过阈值回收重派或标 failed。
- **安全**：runner 注册凭证 + api↔runner 走内网/VPN；组 GitLab token 只随任务 payload 下发给正在执行该任务的 runner（内存态，不落盘）。

### 4.6 CI 闭环

- pipeline webhook：`status=success` → 静默（记库）；`failed` → 拉失败 job 的 trace → 按项目归属的研发组 LLM 一次性 httpx 调用分析根因（不占 claude 会话）→ octo @提交人。
- MR 合并检测：13.12 无 merge 事件 → merge_poll 每 10 分钟轮询 reviewing 状态任务的 MR（mr-merge-watch 模式）→ 合并/关闭 → 任务毕业通知。
- **MR 无人处理升级**（merge_poll 顺带）：MR opened 且按 GitLab `updated_at` 已闲置 ≥12h → `dev_task.escalated_level` 升一级（1~3 级封顶，单调递增不重置、跳级只发当前级），每级 octo @审核人；第 3 级连带 @项目经理（project_pm）。毕业（合并/关闭）即止。

### 4.7 通知矩阵

| 事件 | @谁 | 兜底 |
|---|---|---|
| webhook 实时事件（push/MR/CI 失败分析） | 当事人 | 匹配不到 octo_uid 则发群不 @ |
| 每日提交排行 | 发群 | 私发项目经理 |
| MR 阻塞 >N 天 | assignee | >M 天 @项目经理 |
| 任务完成/MR 待审 | 下发任务的人 | — |
| 代码评审开始（mr_review 入队） | 任务审核人（无任务的外部 MR 按作者匹配） | 留痕 reason=mr_review_started |
| 代码评审有结论（GitLab 评论 + octo 双通道） | 任务审核人 | 结论+CI 门禁一起带出，reason=mr_reviewed |
| CI 失败 | 提交人 | 连续失败 @项目经理 |
| MR 无人处理升级（每 12h 一级、3 级封顶，单调递增） | 审核人 | 第 3 级连带 @项目经理，reason=mr_escalate_l{1..3} |
| MR 初审不通过 | 作者 | — |
| 自动放行冲突 | 作者（评论 + 群 @） | 等人工，不重试不强推 |
| MR 合并/关闭（任务毕业） | 认领人（=任务创建人） | 通知留痕即毕业凭证 |

### 4.8 MR 初审与自动放行（移植 hermes mr_autoflow 模式）

- **触发**（fail-closed）：MR create/update webhook → 项目 `audit_prefixes` 非空且改动文件命中前缀才入队 `mr_review`；`(project, iid, sha)` 未审过才入（幂等）；24h 内同项目 5 次熔断防轰炸。
- **执行**（runner）：常驻 clone detach 到 head_sha（sha 不存在=被强推 → 直接跳过）→ code-review-graph CLI（`detect-changes` / `impact`，`--base origin/<target>`，各截 12KB；失败或空 fallback `git diff`）→ claude 单轮禁工具初审 → verdict JSON（**approve 为默认、modify 必须给出硬伤**；解析失败重跑一次再 failed）。
- **回传落库**：幂等建 mr_review 行（failed 也建行留痕）→ `mr_pipelines` 按 sha 前缀判 CI gate（非 success 即未绿，同时留 `pipeline_url` 供前端跳转）→ GitLab 审计评论（approve/modify 都发）+ octo 评审结论 @审核人 → approve 直通 `try_autoflow`。
- **自动放行 try_autoflow**（直通 + 每 5 分钟 sweep 兜底，全链硬门禁）：
  verdict=approve 且已评论；`auto_approve_prefixes` 非空且 changed_files **全部命中**（部分命中不放行）；实时复查 mr_detail：opened、非 draft、target==组 `mr_target_branch`、head startswith(review.head_sha[:12])；gate 未绿 → 等 sweep 复查转绿再过。
  → `approve_mr`（self-approve 409 仅 log）→ `merge_mr(sha=head)` 防竞态；冲突类拒绝 → `conflict_notified` + 评论 + octo @作者（**终态，等人工**）；其他拒绝 → `hold_not_mergeable`（下轮 sweep 重试）。

---

## 5. 部署方案

```
内网主机（控制面 + 首个 runner）            任意其他节点（按需扩容）
┌──────────────────────────┐          ┌──────────────────────┐
│ db:        postgres:17   │          │ vega-runner:latest  │
│ api:       vega-api     │ ◀──────  │  node:22-slim        │
│ web:       nginx :8180   │  长轮询   │  + claude CLI(npmmirror)│
│ runner #1: vega-runner  │          │  + git               │
└──────────────────────────┘          │  本地 repos/ worktrees│
                                      └──────────────────────┘
```

- 端口：web `:8180`（内网主机 上空闲）；GitLab 服务器需能回访该地址配 webhook（同为内网，可达）。
- 密钥：gitlab_token / llm_api_key / octo_bot_token / am_user_key 用 Fernet 加密落库，主密钥在 api 容器 env；webhook_secret 每组独立（单址识别的唯一凭据）。
- **备份**：SQLite 每日 03:17 自动备份（sqlite3 backup API，热备 + 轮换保留）；切 PG 后备份方案另定（pg_dump 定时）。
- 前端构建：宿主机无 node → `docker run node:22-slim` 挂源码 build（内网主机 已有此模式先例）。
- runner 镜像一份，`docker run -e VEGA_API_URL=… -e VEGA_TOKEN=… vega-runner` 即扩容一个节点。

---

## 6. 风险与已知坑（按影响排序）

1. **GitLab 13.12 老版本**：无 merge 事件（轮询兜底已定）；部分新 API 字段缺失——所有 API 调用先在 13.12 实测再写死。
2. **runner 节点前置条件**：能出网访问 `api:8180` 与 `127.0.0.1:8096`（agent-memory proxy）、能访问 `git.example.com`（git 协议）；部署前逐节点验证 `git ls-remote`。
3. **plan 会话粘性**：Claude 会话文件在 runner 本地 `~/.claude`，节点故障会丢会话——自愈：节点删了（或绑定已悬空）就解绑，下一条消息新开会话并把该模式历史整体带给模型，随后在该节点重新固化粘性。UI 尚未标注「会话已重置」。
4. **p-url 上游必须 anthropic 或 openai 兼容**（p-api-key 自动转 x-api-key / Bearer）；不兼容的上游 proxy 覆盖不了——配置页写清支持范围并加连通性测试按钮。
5. **proxy forwardTimeoutMs=600s**：单轮 10 分钟上限，开发任务用 `--max-turns` 控制总时长；超时任务标记 failed 并 @下发人。
6. **任务队列单点在 DB**：Postgres 挂了全停——接受（compose restart: always + volume），不上 MQ。
7. **am_team_id / am_agent_id 的创建**：研发组创建时通过 core 的 /v3 API（或面板手工）在 agent-memory 里建 team/agent 并存映射；两边删改不同步属预期，以后台映射表为准。
8. **octo @匹配**：昵称≠gitlab 用户名是常态。AI 对配负责把人工工作量压到「扫一眼确认」，但不追求全对——置信度不足就进待确认，人工永远可改且 manual 结果锁定不被覆盖。
9. **13.12 merge 偶发 406**（hermes 已实测）：merge 失败分类处理——冲突类进 `conflict_notified` 终态 @人，其他进 `hold_not_mergeable` 由 sweep 重试，不盲重放。
10. **CORS 当前 `*` + credentials**：内网开发期可接受，公网/生产部署前必须收紧到实际域名。
11. **留存清理未做**：gitlab_event / notify_log / runner_task / daily_stat 只增不删，量大后需要清理任务（当前靠 SQLite 备份轮换兜底）。
12. **datetime 约定**：全链 aware UTC；SQLite 读回 naive 的历史坑已在展示层防御（心跳已修），新代码不得混用 naive/aware。
13. **通知密度**：单个 AI 任务全流程 3~4 条群消息，接入多组后需做降噪（合并播报或汇总日报）。

---

## 6.5 工作台（实时流程图 + 毕业口径）

- `/api/workbench`（组/项目过滤）：需求 → 任务 → MR 初审（verdict/gate）→ 合入（flow_state/目标分支）一次聚合。
- **定稿节点轨道**：每个需求卡内联「📄 需求定稿 → 📋 方案定稿 → ⚙️ 开发」三节点，
  状态取自 doc_final（存在 is_final 文档）/ plan_final（status=confirmed）/ 任务流水线。
- **多 agent 协作状态**：活跃任务的卡上显示「🤖 主 + N 子（协作中）」chip（agents 聚合，见 4.4），点击直达任务详情看泳道。
- **毕业**：需求的所有任务都到终态 = 从流程图退场。终态三口：`merged`（MR 合并，合并路径 await octo @认领人 后才置 done，状态即通知凭证）、`failed`（任务失败）、`closed`（MR 被关闭，merge_poll 哨兵文案 `MR 被关闭` 标记）。
- 需求毕业类型：全 merged=done / 有 failed=failed / 其余 closed；默认不返回毕业行，`include_graduated=true` 返回 + `graduated_summary` 计数。
- 前端：在读需求画动态流程图（连线随状态动），毕业需求收进「🎓 已毕业」折叠名册。

---

## 7. 实施阶段（每阶段独立可用）

> 2026-10-25 状态：P1–P5 已全部实现并通过 80 项自动化测试（含单址 webhook、按组 bot、三方对配 v2 + 同人合并、MR 初审、自动放行、runner bootstrap）。

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| **P1 基座** | 人员/研发组/项目 CRUD、GitLab 扫描+单址 webhook 自动配置、octo 绑定+三方对配 v2、实时事件通知、每日提交排行、MR 阻塞扫描、PM 绑定、LLM 配置链、集成中心（凭证/事件/通知/MR 评审四页签） | 群里收到 push/MR/CI 事件 @通知与日报 | ✅ 已交付 |
| **P2 需求线** | 需求池+文档上传、认领、**runner 协议（注册/长轮询/事件回传）+ plan_turn 粘性派发**、Ant Design X 对话页（SSE）、agent-memory team/agent 绑定 | 界面里聊出一份可执行计划并定稿 | ✅ 已交付 |
| **P3 执行线** | 任务编号与状态机、dev_task 派发、runner 常驻 clone + worktree、bootstrap 装机、AI 开发、push+建 MR、octo @审核通知、多 runner 扩容验证 | 一个需求从确认到 MR 全程无人值守；加一台 runner 即扩容 | ✅ 已交付 |
| **P4 CI 闭环** | pipeline 事件、失败 LLM 分析（按组 LLM）、merge 轮询兜底、worktree 回收、SQLite 备份 | CI 失败群里出现根因分析 | ✅ 已交付 |
| **P5 审计与放行** | audit_prefixes 触发 MR 初审（CRG 事实 + LLM verdict）、CI 硬门禁、auto_approve_prefixes 自动放行、冲突终态 @人 | 命中前缀的 MR 自动收到初审评论；全命中 + CI 绿的 MR 自动 Approve+Merge | ✅ 已交付 |
