# ---- 阶段1: 构建前端 ----
FROM node:20-alpine AS frontend-build
ARG APP_VERSION=dev
ENV VITE_APP_VERSION=$APP_VERSION
WORKDIR /app/frontend
# patches/ 必须在 npm install 前就位，否则 patch-package 的 postinstall hook
# 找不到 patch 文件，悄无声息跳过，build 出来的 bundle 还含未修补的库 bug
# （例如 @yume-chan/stream-extra 的 super-constructor 崩溃）。
COPY frontend/package*.json ./
COPY frontend/patches ./patches
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- 阶段2: 运行后端 ----
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
# 将前端构建产物复制到后端的 static 目录
COPY --from=frontend-build /app/frontend/dist ./static/

# 数据目录（数据库 + catalog.yaml）
RUN mkdir -p /app/data
ENV DATABASE_URL=sqlite:////app/data/data.db
ENV CATALOG_PATH=/app/data/catalog.yaml

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
