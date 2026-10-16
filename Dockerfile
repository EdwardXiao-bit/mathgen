# syntax=docker/dockerfile:1
# mathgen 容器镜像
#
#   docker build -t mathgen .
#   docker run --rm -p 8000:8000 -v mathgen-data:/data mathgen
#   浏览器打开 http://127.0.0.1:8000/teacher
#
# 两个阶段：node 里把前端产物构建出来，python 里装后端 + Chromium（PDF 导出要用）。
# 镜像里**自带**前端产物和 Chromium，所以容器内不需要联网、不需要再装任何东西。
# 体积换功能：Chromium 及其系统依赖让镜像在 1 GB 量级。

# ------------------------------------------------------------------ 1. 前端
FROM node:22-alpine AS web
WORKDIR /web
# pnpm 版本在这里**显式钉死**，不依赖 corepack + package.json 的 packageManager 字段。
# 原因（实测，见 CI run 38940469884 的 docker job）：
#   `packageManager` 字段会让某些 pnpm 安装（npm 装的 pnpm / Windows 上）
#   往 pnpm-lock.yaml 里补写 `packageManagerDependencies` 段，
#   于是 `pnpm install --frozen-lockfile` 直接报
#   ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE 而失败。
#   去掉字段 + 在这里显式钉版本，两边都稳（本地 frozen-lockfile 已验证通过）。
RUN npm i -g pnpm@12.5.1
# pnpm-workspace.yaml 必须一起进来：它里面有 `allowBuilds: esbuild`，
# 少了它 pnpm 会按默认策略拒绝执行 esbuild 的安装脚本（ERR_PNPM_IGNORED_BUILDS）。
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ------------------------------------------------------------------ 2. 运行时
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MATHGEN_DATA_DIR=/data \
    MATHGEN_STATIC_DIR=/app/static \
    MATHGEN_PW_TMP=/tmp

WORKDIR /app

# 后端的依赖与包本体。`app.export.browser` 会去 PLAYWRIGHT_BROWSERS_PATH 找 Chromium。
COPY backend/ /app/backend/
RUN pip install /app/backend \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# 前端产物：由上一阶段构建，容器里只读挂载点
COPY --from=web /web/dist /app/static

# 非 root 运行；数据目录单独一个卷，重建容器不丢卷子与成绩
RUN useradd --create-home --uid 10001 mathgen \
 && mkdir -p /data \
 && chown -R mathgen:mathgen /data \
 && chmod -R a+rX /ms-playwright /app/static
USER mathgen

VOLUME ["/data"]
EXPOSE 8000

# 用 /openapi.json 做存活探测：项目里既有的探针口径就是它，不新增路由（见 docs/04 §7）
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "app", "--host", "0.0.0.0", "--no-browser"]
