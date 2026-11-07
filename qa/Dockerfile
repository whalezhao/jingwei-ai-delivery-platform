#
# API 服务：FastAPI + Alembic（启动时自动 upgrade head）。
# 不含 Node/Chromium —— 执行器在 worker 镜像里。

FROM python:3.13-slim-bookworm

# ghcr.io 在部分内网/国内直连不通，装 PyPI 上的 uv（走构建时镜像源）
ARG UV_INDEX_URL=
RUN if [ -n "$UV_INDEX_URL" ]; then pip install --no-cache-dir uv -i "$UV_INDEX_URL"; else pip install --no-cache-dir uv; fi

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 依赖层单独缓存：只改代码时不用重装依赖
COPY pyproject.toml uv.lock ./
RUN if [ -n "$UV_INDEX_URL" ]; then export UV_INDEX_URL; fi; uv sync --frozen --no-dev --no-install-project

COPY . .
RUN if [ -n "$UV_INDEX_URL" ]; then export UV_INDEX_URL; fi; uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
