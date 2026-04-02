# ---- 阶段1: 构建前端 ----
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
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
