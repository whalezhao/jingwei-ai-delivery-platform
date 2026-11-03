# vega-api / vega-runner 同一基础层；compose 用 target 选 api / runner
# （直接 docker build 不带 --target 会落到最后的 runner 阶段）。
# 国内构建可传 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
FROM python:3.12-slim AS base
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=${PIP_INDEX_URL} \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY vega ./vega
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

FROM base AS api
EXPOSE 8000
# SSE 是无限流：不设优雅退出超时，SIGTERM 后会一直等旧连接
CMD ["uvicorn", "vega.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "3"]

FROM base AS runner
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG JDTLS_URL=https://download.eclipse.org/jdtls/snapshots/jdt-language-server-latest.tar.gz
# 基础镜像是 trixie 的 deb822 源，两段 URI 都含 deb.debian.org，一并换掉
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends git jq ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
# python 镜像里没有 node：直接搬 node:22 的二进制与 npm，避免再叠一层发行版差异
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
# jdtls 的 latest 快照要求 Java 25（bookworm 只有 17）：从 temurin 搬一份 JRE 进 /opt/java/openjdk。
# 快照跟着最新 JDK 走，日后快照再抬高版本要求时这里会构建失败而不是静默降级。
COPY --from=eclipse-temurin:25-jre-jammy /opt/java/openjdk /opt/java/openjdk
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm config set registry ${NPM_REGISTRY} \
 && npm install -g @anthropic-ai/claude-code @ast-grep/cli typescript typescript-language-server pyright \
 && uv tool install code-review-graph \
 && uv tool install serena-agent \
 && curl -fsSL "${JDTLS_URL}" -o /tmp/jdtls.tar.gz \
 && mkdir -p /opt/jdtls \
 && tar -xzf /tmp/jdtls.tar.gz -C /opt/jdtls \
 && rm /tmp/jdtls.tar.gz \
 && chmod +x /opt/jdtls/bin/jdtls \
 && ln -sf /opt/jdtls/bin/jdtls /usr/local/bin/jdtls \
 && npm cache clean --force
# IS_SANDBOX：claude 以 root 跑 --dangerously-skip-permissions 时的放行开关（容器内即沙箱）
ENV JAVA_HOME=/opt/java/openjdk \
    PATH="/root/.local/bin:/opt/java/openjdk/bin:${PATH}" \
    CLAUDE_CONFIG_DIR=/root/.claude-vega \
    IS_SANDBOX=1
# claude 的 LSP 能力只从插件加载（settings.json 的 lspServers 运行时并不生效）。
# 官方 marketplace 走 GitHub，内网主机 等环境连不上，所以只把这三个插件的定义内置进镜像；
# anthropics 市场名是官方保留名，本地副本改名 vega-plugins。
COPY vendor/claude-plugins /opt/claude-plugins
RUN mkdir -p "$CLAUDE_CONFIG_DIR" \
 && claude plugin marketplace add /opt/claude-plugins \
 && claude plugin install pyright-lsp@vega-plugins \
 && claude plugin install typescript-lsp@vega-plugins \
 && claude plugin install jdtls-lsp@vega-plugins
# EGM（elephant-goldfish-model，Apache-2.0）原样 vendor，只随 EGM 任务用 --plugin-dir 加载，
# 不装进 marketplace：普通开发/初审会话看不到这七个技能。升级换目录，见 vendor/egm/SOURCE.txt。
COPY vendor/egm /opt/egm
# 全局记忆：告诉 claude 这四个能力分别管什么（MCP 由 runner 启动时按 .env 合并进 .claude.json）。
# 内容在 api/runner-claude.md，改提示词改那个文件（claude-config 卷只在首次创建时从镜像播种）。
COPY runner-claude.md $CLAUDE_CONFIG_DIR/CLAUDE.md
CMD ["python", "-m", "vega.runner"]
