# 构建上下文是仓库根目录（要带上 deploy/nginx.conf）：
#   docker build -f web/Dockerfile .
# 国内构建可传 --build-arg NPM_REGISTRY=https://registry.npmmirror.com
FROM node:22-slim AS build
ARG NPM_REGISTRY=https://registry.npmjs.org
# 测试域（aster API）的对外 origin；不设则页面里的 QA 请求走同源（会 404）
ARG VITE_QA_API_BASE=""
ENV VITE_QA_API_BASE=${VITE_QA_API_BASE}
WORKDIR /app
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN npm config set registry ${NPM_REGISTRY} \
 && npm install -g pnpm@12.15.0 \
 && pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
