# Vega 部署手册（Docker Compose）

## 组成

| 服务 | 说明 |
|---|---|
| `db` | 可选（`--profile bundled-db`）；复用现成 PG 时不启动 |
| `api` | FastAPI（容器内 8000，不对外暴露） |
| `web` | nginx 托管前端 + 反代 `/api` `/webhooks`，宿主机 `8180` |
| `runner` | 可选（`--profile runner`），执行 claude 的节点；工具链全在镜像里 |

## 前置

- Docker 24+ / Compose v2.20+（`depends_on.required`），宿主机 8180 空闲。
- PostgreSQL 17：内网主机 上已有 `aster-qa-postgres-1`（`qa` 超级用户，监听 5432），建个独立库即可复用；不想共用就用 `--profile bundled-db` 起内置的。
- api/runner 能访问 agent-memory（`VEGA_AM_BASE_URL` 8096、`VEGA_AM_HUB_BASE_URL` 8125），runner 另需访问 GitLab 与 `VEGA_CRG_EMBEDDING_BASE_URL`（18083）。
- GitLab 服务器能回访 `http://<host>:8180`（配 webhook 用）。

## 首次部署

```bash
cp .env.example .env
```

`.env` 里至少改三项：`VEGA_DATABASE_URL`（库名/密码，密码避开 `@ : /`）、`VEGA_SECRET_KEY`（长随机串，**上线后不可更换**：它是 token 签名与落库加密的同一主密钥）、`VEGA_ADMIN_PASSWORD`。

复用现成 PG 时先建库（内网主机 上以 aster-qa 的 `qa` 超级用户执行；`CREATE DATABASE` 不能和别的语句同批）：

```bash
docker exec aster-qa-postgres-1 psql -U qa -d qa_manage -c "CREATE ROLE vega LOGIN PASSWORD '<与 .env 一致>'"
docker exec aster-qa-postgres-1 psql -U qa -d qa_manage -c "CREATE DATABASE vega OWNER vega"
```

然后启动（`db` 在 `bundled-db` profile 里，默认不启）：

```bash
docker compose up -d --build
```

用内置 PG 则把 `.env` 改成 `...@db:5432/vega`、设置 `POSTGRES_PASSWORD`，并加 profile：

```bash
docker compose --profile bundled-db up -d --build
```

打开 `http://<host>:8180`，用 `VEGA_ADMIN_USERNAME` / `VEGA_ADMIN_PASSWORD` 登录（首次启动自动建档，之后改 env 不会重置已有账号）。

之后在「集成」页填 GitLab 地址与 token，在「研发组」里为每个组配 webhook secret。webhook 地址由 `.env` 的 `VEGA_API_URL`（填 GitLab 能访问到的基地址）拼出来，即：

```
<VEGA_API_URL>/webhooks/gitlab
```

## runner 节点

1. 界面「节点」新建节点，复制发放的 token。
2. 写进 `.env`：`VEGA_RUNNER_TOKEN=…`（`VEGA_RUNNER_NAME` 可改）。
3. 启动：

```bash
docker compose --profile runner up -d --build runner
```

用内置 PG 时再加 `--profile bundled-db`。

节点页显示在线即通。runner 镜像已内置：claude CLI、LSP（Python `pyright-langserver` / TypeScript `typescript-language-server` / Java `jdtls` + JRE 25）、code-review-graph、serena、ast-grep、git。code-review-graph 与 serena 的 MCP 条目由 runner 启动时按 `.env` 写入 `CLAUDE_CONFIG_DIR/.claude.json`（即 `/root/.claude-vega/.claude.json`）。

全局提示词（四个能力的使用约定）在 `api/runner-claude.md`，构建时写到 `/root/.claude-vega/CLAUDE.md`。

## 数据与备份

卷：`pgdata`（仅内置 PG）、`claude-config`（claude 会话/插件/全局提示词）、`runner-home`、`runner-workspace`（常驻仓库与 worktree）。

复用现成 PG 时，vega 的数据在对方的卷里（内网主机 上是 `aster-qa_pgdata`），备份也在那边做：

```bash
docker exec aster-qa-postgres-1 pg_dump -U vega vega | gzip > vega-$(date +%F).sql.gz
```

内置 PG 时：`docker compose exec -T db pg_dump -U vega vega | gzip > …`。
共用实例要接受它的生命周期：aster-qa 那边 `docker compose down -v` 会把 vega 库一起删掉。

SQLite 模式（`VEGA_DATABASE_URL=sqlite+aiosqlite:////data/vega.db`）直接备份 `./data/vega.db`。

## 升级

```bash
docker compose up -d --build
```

- 建表用的是 `create_all`：**只建新表，不会给已有表加列**。升级遇到新增字段需先手工 `ALTER TABLE`（旧数据保留），后续接 Alembic 再做版本化迁移。
- `VEGA_SECRET_KEY` 保持不变，否则已存的所有 token 解不开、只能重新录入。
- LSP 插件与全局提示词都是构建时装进镜像、`claude-config` 卷只在首次创建时从镜像播种。升级 runner 镜像后若插件/提示词没跟着更新，删卷重建：`docker compose rm -sf runner && docker volume rm vega_claude-config`（会丢 claude 本地会话记忆）。

## 构建平台

镜像按构建机架构出（Apple Silicon 上是 arm64，内网主机 是 amd64）。要么直接在 内网主机 上构建，要么在别的机器上指定平台：

```bash
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose build
```

runner 镜像构建时还需要访问 `download.eclipse.org`（jdtls）。claude 官方插件市场的三个 LSP 插件已内置在 `api/vendor/claude-plugins`（市场名 `vega-plugins`），不依赖 GitHub；jdtls 也可以按下一节的本地缓存方式喂给构建。

## 构建源与代理

- 默认已配国内镜像：`.env` 的 `PIP_INDEX_URL`（清华）、`NPM_REGISTRY`（npmmirror）与 `APT_MIRROR`（runner 装 git/curl 用，清华），compose 作为 build args 透传。
- `download.eclipse.org` 国内直连只有几十 KB/s，jdtls 包有 48MB。可在快网下载后放到宿主机起临时 HTTP 服务，构建时覆盖地址：`JDTLS_URL=http://<host>:18099/jdtls.tar.gz docker compose --profile runner build runner`。
- 代理坑：宿主 shell 的 `http_proxy=http://127.0.0.1:…`（mihomo 等）和 `~/.docker/config.json` 的 `proxies` 段都会被 docker 注入构建容器，而 `127.0.0.1` 在容器里指向容器自己，pip/npm 会直接连不上。构建前把 `~/.docker/config.json` 的 `proxies` 段去掉（或改成容器可达的地址），并清掉 shell 代理变量：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY docker compose build
```

## 已知限制

- CORS 目前是 `*` + credentials，内网期可用；对外提供服务前必须收紧到实际域名（`api/vega/main.py`）。
- api/web/runner 日志走 json-file，单文件 20MB × 5。
