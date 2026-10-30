# Vega · 织女星 — 研发流程自动化平台

[English](README.en.md) | 简体中文

[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Docker](https://img.shields.io/badge/Docker%20Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)

> 「人员 → 研发组 → 项目 → 需求 → 计划 → AI 开发任务 → MR → CI → 合入 → 通知」全流程服务化系统。
> AI 认领需求、和人对齐方案、写出代码、提交 MR、通过 AI 代码评审与 CI、自动合入 nightly，全程 octo 群播报、人只把关关键节点。

## 它解决什么问题

把散落在定时脚本里的研发自动化（MR 初审、merge 轮询、CI 失败分析、代码统计……）收敛成一个有界面、有状态机、有审计留痕的系统：

- **需求池**：上传文档 / 对话生成需求，认领后进入「AI 需求专家协写 → 定稿」；
- **方案讨论**：与 AI 方案专家多轮对话（流式），定稿成方案版本；
- **Agent 开发**：方案一键派发为开发任务，runner 节点领任务，驱动 claude 在独立 worktree 里写代码、推送分支、建 MR；
- **AI 代码评审**：MR 命中项目审计前缀即入队初审，结论（✅ 可批准 / ⚠️ 需修改 / ❌ 未通过）回帖到 GitLab 并 octo 播报；
- **CI 与自动放行**：CI 绿了按项目白名单前缀自动 FF 合入 nightly；冲突/不可合则 @负责人，绝不盲目重试；
- **工作台**：需求 → 定稿 → 开发 → 评审 → CI → 合入的实时流程图（5s 刷新），任务卡带阶段信息与时间线；
- **代码统计**：按日提交 / 新增删除行 / 热点文件，人员与项目双维度落库。

## 架构

```
octo 群 ← 播报/派单 ← vega-api ← webhook ← GitLab 13.12
                        ↑  ↓ 长轮询
                    vega-runner（可多节点，每节点驱动 claude -p）
                        ↑ SSE 流式
                     vega-web（React 18 + antd 5 + @ant-design/x）
```

| 组件 | 技术 | 说明 |
|---|---|---|
| `api/vega` | Python 3.12 · FastAPI · SQLAlchemy async · SQLite（开发）/ PostgreSQL（部署）· APScheduler | api 与 runner 同仓两入口 |
| `web/` | Vite · React 18 · antd 5 · @ant-design/pro-components · @ant-design/x | 工作台 / 需求池 / 对话 / 任务 / 统计 |
| GitLab 集成 | 自研 httpx 薄客户端（`vega/gitlab.py`） | 适配 13.12 的坑：webhook 全局单址 + 组 secret 识别、不发 merge 事件 → `merge_poll` 10 分钟轮询兜底 |
| 通知 | octo bot API 直连（`vega/octo.py`） | @任务创建人 / 项目负责人 / 推送人 |
| LLM | 人员 → 研发组 → 项目三级配置解析（`vega/llm.py`），runner 侧 claude CLI | 需求/方案/评审/CI 分析共用一套解析链 |
| 密钥 | Fernet 对称加密落库（`VEGA_SECRET_KEY` 为唯一主密钥） | GitLab token / octo token / LLM key 均密文存储 |

定时任务（APScheduler）：GitLab 项目扫描对配（30min）、代码统计落库（每日）、MR 轮询兜底（10min）、阻塞 MR 早报（每日 9:00 + 每小时）、DB 备份轮转（每日 3:17）、死信重投。

## 目录结构

```
vega/
├── api/               # FastAPI 后端 + runner 节点（同仓两入口）
│   ├── vega/          #   models / routers / jobs / runner / gitlab / octo / autoflow ...
│   ├── tests/         #   pytest（136 用例）
│   └── .env.example   #   配置模板（唯一可入库的 env）
├── web/               # React 前端
├── deploy/            # nginx.conf + 部署手册
├── docker-compose.yml # api / web / 可选内置 PG / 可选 runner
└── DESIGN.md          # 完整技术选型与设计文档（v3，对齐实际实现）
```

## 快速开始

依赖：Python 3.12+、Node 20+（pnpm）、以及 `claude` CLI（仅 runner 需要）。

```bash
# 1. 后端
cd api
cp ../.env.example .env          # 至少改 VEGA_SECRET_KEY / VEGA_ADMIN_PASSWORD
.venv/bin/python -m uvicorn vega.main:app --host 127.0.0.1 --port 8000

# 2. 前端
cd web && pnpm install && pnpm dev   # http://localhost:5173

# 3. runner 节点（另开终端）
cd api && .venv/bin/python -m vega.runner

# 测试
cd api && .venv/bin/python -m pytest tests -q
```

界面首次登录用 `VEGA_ADMIN_USERNAME` / `VEGA_ADMIN_PASSWORD`；之后在「集成」页填 GitLab 地址与 token，「研发组」里配 webhook secret，把 GitLab 的 webhook 指到 `<VEGA_API_URL>/webhooks/gitlab`（Push / Merge Request / Pipeline 三类事件）。

## 部署

生产用 Docker Compose（默认外接 PostgreSQL，可选内置 PG profile）：

```bash
cp .env.example .env   # 按 deploy/README.md 逐项核对
docker compose up -d --build api web
```

完整部署手册（PG 建库、runner 注册、webhook 接线、`VEGA_SECRET_KEY` 不可更换的原因、备份恢复）见 [deploy/README.md](deploy/README.md)。

## 关键设计取舍

- **webhook 全局单址**：GitLab 13.12 只配一个地址，靠 `X-Gitlab-Token` 与各组 secret 比对识别研发组。
- **merge 事件不可信**：13.12 偶发不发 merge，收尾由 `merge_poll` 轮询兜底，webhook 与轮询靠任务终态幂等去重。
- **自动放行 fail-closed**：项目没配 `auto_approve_prefixes` 就绝不自动合入，收尾改为 octo 通知项目负责人人工合入；冲突必须通知人，禁止自动解决。
- **过程留痕**：runner 回传的 claude 事件（思考/工具/结果）全量落库 `dev_task_event`，任务详情页可回放。

## 状态

内部生产使用中，随用随改。设计细节与演进记录见 [DESIGN.md](DESIGN.md)。
