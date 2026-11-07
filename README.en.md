# JINGWEI — R&D Delivery Platform

English | [简体中文](README.md)

[![CI](https://github.com/whalezhao/vega/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/whalezhao/vega/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Docker](https://img.shields.io/badge/Docker%20Compose-2496ED?logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)

> A service-oriented system covering the full flow: **people → teams → projects → requirements → plans → AI dev tasks → MR → CI → merge → notifications**.
> AI claims requirements, aligns on a plan with humans, writes the code, opens the MR, passes AI code review and CI, and auto-merges into nightly — with octo group-chat updates throughout. Humans only gate the key steps.

## What it solves

It consolidates R&D automation that used to live in scattered cron scripts (MR pre-review, merge polling, CI failure analysis, code stats…) into one system with a UI, state machines, and a full audit trail:

- **Requirement pool**: upload docs or draft requirements in chat; once claimed, an AI requirements expert co-writes and finalizes them.
- **Plan discussion**: multi-turn streaming conversation with an AI plan expert, finalized into plan versions.
- **Agent development**: one click turns a plan into dev tasks; runner nodes pick them up and drive `claude` inside an isolated git worktree to code, push a branch, and open an MR.
- **AI code review**: an MR touching files that match the project's audit prefixes is queued for pre-review; the verdict (✅ approve / ⚠️ modify / ❌ fail) is posted back to GitLab and announced via octo.
- **CI & auto-merge**: once CI is green, MRs matching the project's auto-approve prefixes are fast-forward merged into nightly; conflicts and unmergeable states @ the owner instead of retrying blindly.
- **Workbench**: a live flow chart (5s refresh) of requirement → finalize → dev → review → CI → merge, with per-stage details and timestamps on every card.
- **Code statistics**: daily commits / additions / deletions and hot files, stored per person and per project.

## Architecture

```
octo group chat ← announcements ← vega-api ← webhooks ← GitLab 13.12
                        ↑  ↓ long-polling
                  vega-runner (N nodes, each driving claude -p)
                        ↑ SSE streaming
                     vega-web (React 18 + Tailwind v4 + beUI)
```

| Component | Stack | Notes |
|---|---|---|
| `api/vega` | Python 3.12 · FastAPI · SQLAlchemy async · SQLite (dev) / PostgreSQL (prod) · APScheduler | API and runner share this repo as two entrypoints |
| `web/` | Vite · React 18 · antd 5 · @ant-design/pro-components · @ant-design/x | workbench / requirement pool / chat / tasks / stats |
| GitLab integration | In-house thin httpx client (`vega/gitlab.py`) | Works around GitLab 13.12 quirks: single global webhook URL + per-group secret, no merge events → 10-min `merge_poll` fallback |
| Notifications | Direct octo bot API client (`vega/octo.py`) | mentions task creator / project owners / pusher |
| LLM | 3-level config resolution person → group → project (`vega/llm.py`); claude CLI on runners | shared by requirement / plan / review / CI analysis |
| Secrets | Fernet symmetric encryption at rest, `VEGA_SECRET_KEY` as the single master key | GitLab / octo / LLM tokens all stored encrypted |

Scheduled jobs (APScheduler): GitLab project scan & sync (30 min), daily code stats, MR merge polling (10 min), blocked-MR morning report (daily 9:00 + hourly), DB backup rotation (daily 3:17), dead-letter requeue.

## Layout

```
vega/
├── api/               # FastAPI backend + runner node (two entrypoints, one repo)
│   ├── vega/          #   models / routers / jobs / runner / gitlab / octo / autoflow ...
│   ├── tests/         #   pytest (136 tests)
│   └── .env.example   #   config template (the only env file committed)
├── web/               # React frontend
├── deploy/            # nginx.conf + deployment guide
├── docker-compose.yml # api / web / optional built-in PG / optional runner
└── DESIGN.md          # full design doc (v3, aligned with the implementation)
```

## Quick start

Prerequisites: Python 3.12+, Node 20+ (pnpm), and the `claude` CLI (runner only).

```bash
# 1. Backend
cd api
cp ../.env.example .env          # at least set VEGA_SECRET_KEY / VEGA_ADMIN_PASSWORD
.venv/bin/python -m uvicorn vega.main:app --host 127.0.0.1 --port 8000

# 2. Frontend
cd web && pnpm install && pnpm dev   # http://localhost:5173

# 3. Runner node (another terminal)
cd api && .venv/bin/python -m vega.runner

# Tests
cd api && .venv/bin/python -m pytest tests -q
```

First login uses `VEGA_ADMIN_USERNAME` / `VEGA_ADMIN_PASSWORD`; then add your GitLab URL and token on the Integrations page, set each group's webhook secret under Teams, and point GitLab webhooks at `<VEGA_API_URL>/webhooks/gitlab` (Push / Merge Request / Pipeline events).

## Deployment

Production runs on Docker Compose (external PostgreSQL by default, built-in PG available as a profile):

```bash
cp .env.example .env   # verify every entry against deploy/README.md
docker compose up -d --build api web
```

The full operations manual (PG setup, runner registration, webhook wiring, why `VEGA_SECRET_KEY` must never rotate, backup/restore) lives in [deploy/README.md](deploy/README.md).

## Key design decisions

- **Single global webhook URL**: GitLab 13.12 gets one webhook endpoint; the group is identified by matching `X-Gitlab-Token` against each group's secret.
- **Merge events are not trusted**: 13.12 occasionally drops them, so finalization is backed by the `merge_poll` job; webhook and poller are idempotent via the task terminal state.
- **Auto-merge is fail-closed**: without `auto_approve_prefixes` configured, nothing auto-merges — the flow ends with an octo mention to the project owners for a manual merge; conflicts must be escalated to humans and are never auto-resolved.
- **Full audit trail**: every runner-relayed claude event (thinking / tool calls / results) is stored in `dev_task_event` and replayable on the task detail page.

## Status

In internal production use, iterated on continuously. See [DESIGN.md](DESIGN.md) for design details and evolution history.
