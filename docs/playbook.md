# 运行手册

## 部署方式一览

| 方式 | 适用场景 | 依赖 |
|---|---|---|
| [Docker 部署](#docker-部署) | 服务器 / 生产环境，手动 | Docker |
| [发布 Release](#发布-release自动部署) | 自动构建 + 部署到 self-hosted runner | gh CLI（本机） |
| [打包部署](#打包部署) | 服务器无外网 / 内网部署 | Docker（仅本机构建时需要） |
| [开发模式](#开发模式) | 本地开发调试 | Python 3.10+, Node.js 18+ |

---

## Docker 部署

最简单的方式。服务器上需要安装 Docker。

### 1. 获取代码并启动

```bash
git clone <你的仓库地址> infill
cd infill

# 准备产品目录
cp data/catalog.yaml.example data/catalog.yaml
# 按需编辑 data/catalog.yaml

# 一键启动
docker compose up -d --build
```

访问 **http://服务器IP:8000** 即可使用。

### 2. 日常操作

```bash
# 查看日志
docker compose logs -f

# 停止
docker compose down

# 重启
docker compose restart

# 更新代码后重新部署
git pull
docker compose up -d --build
```

### 3. 数据说明

- 数据库和产品目录都在 `data/` 目录下，通过 volume 挂载，容器重建不丢数据
- 修改 `data/catalog.yaml` 后在网页上点"重新加载目录"即可生效，不用重启
- 备份：直接备份 `data/` 目录

---

## 打包部署

适用于服务器没有外网、不能 git clone 的场景。在本机打包成一个文件，拷到服务器一键部署。

### 1. 本机打包

本机需要安装 Docker。

```bash
cd infill
./scripts/bundle.sh
```

打包完成后在项目根目录生成 `infill-deploy.tar.gz`（约几百MB，包含完整的 Docker 镜像）。

### 2. 拷贝到服务器

```bash
scp infill-deploy.tar.gz user@server:~/
```

### 3. 服务器上部署

服务器只需要安装 Docker，不需要 Python、Node.js、git。

```bash
mkdir infill && cd infill
tar xzf ~/infill-deploy.tar.gz
./deploy.sh
```

`deploy.sh` 会自动加载镜像、启动服务，并提示访问地址。

### 4. 更新

本机重新 `./scripts/bundle.sh`，拷贝到服务器同一目录再次运行 `./deploy.sh` 即可。

---

## 发布 Release（自动部署）

GH Actions（`.github/workflows/deploy.yml`）监听 `release: types: [published]`：你只要 `gh release create vX.Y.Z`，就会：

1. cloud runner 用 buildx + QEMU 构建 `linux/arm64` 镜像
2. 推到 `ghcr.io/<repo>:vX.Y.Z` + `:latest`
3. self-hosted runner（tag `[self-hosted, macOS, deploy]`）`docker compose pull` + `up -d`
4. 健康检查 `curl http://localhost:8000/api/products`（60 秒内必须 200）

> 具体 runner 主机 / 用户 / 部署目录细节在 self-hosted runner 配置和 `docker-compose.yml` 里，不在本文件——避免泄露到 public repo。

### 版本号约定

锁定在当前 pinned minor，patch 自由 +1；升 minor / major 必须有明确理由（重构、新增大功能模块等），不要自作主张：

```bash
# 上一个 tag
git tag --sort=-v:refname | head -1
# → v0.4.0

# 默认下一版（patch）
git tag --sort=-v:refname | head -1 | awk -F. '{ printf "%s.%s.%d\n", $1, $2, $3+1 }'
# → v0.4.1
```

### 一次完整发布

```bash
# 1. 确认 main 干净 + 已 push
git status                       # 应该 nothing to commit
git push origin main             # 确保远端是最新的

# 2. 看一眼自上次 tag 起的提交摘要（给 release notes 准备素材）
LAST_TAG=$(git tag --sort=-v:refname | head -1)
git log "${LAST_TAG}..HEAD" --oneline

# 3. 创建 release（同时打 tag 推到远端）
gh release create vX.Y.Z \
  --title "vX.Y.Z — 一句话主题" \
  --notes "$(cat <<'EOF'
## 新功能
- ...

## 修复
- ...

## 内部
- ...
EOF
)"

# 4. 关注 Actions
gh run watch --exit-status            # 跟到 success
# 或
gh run list --workflow=deploy.yml --limit 3
```

### 验证生产环境实际跑的版本

`$HOST` 是部署机的内网 IP（不要把它写进文档/提交里）：

```bash
# 健康检查
curl -sS http://$HOST:8000/api/health
# → {"status":"ok"}

# 前端 bundle 里搜 APP_VERSION（构建时由 Dockerfile ARG 注入）
JS=$(curl -sS http://$HOST:8000/ | grep -oE '/assets/index-[A-Za-z0-9_]+\.js' | head -1)
curl -sS "http://$HOST:8000${JS}" | grep -oE 'v0\.[0-9]+\.[0-9]+' | head -1
```

### 常见问题

**`Build & push (linux/arm64)` 卡 1h+**
ubuntu-latest 是 x86，arm64 build 走 QEMU 模拟，慢 5-10 倍。前端 bundle 体积增长会让 GHA 缓存 (`type=gha`) 失效，首次全量构建大概 30-60 min；之后增量 5-20 min。卡超 1h 基本是网络/缓存挂了，直接取消重跑：

```bash
RUN_ID=$(gh run list --workflow=deploy.yml --limit 1 --json databaseId -q '.[0].databaseId')
gh run cancel $RUN_ID
sleep 5
gh run rerun $RUN_ID
```

**Release 创建了但没触发 Actions**
检查是不是建成 draft 了——`draft: false` 才会触发 `release: published` 事件：

```bash
gh release view vX.Y.Z | grep draft
# → draft: false
```

**self-hosted runner 上 docker compose 拉不到镜像**
ssh 到部署机重新 login GHCR + 重启：

```bash
docker logout ghcr.io
echo $GHCR_TOKEN | docker login ghcr.io -u <github-username> --password-stdin
docker compose pull && docker compose up -d
```

**手动重新部署 / 不打新 tag**
对已存在的 release 直接 rerun，或者 workflow_dispatch：

```bash
# 重跑历史 run
gh run rerun <RUN_ID>

# 手动触发（不走 release event）
gh workflow run deploy.yml
```

### 回滚

`ghcr.io` 上历史 tag 都在。在部署机上：

```bash
docker pull ghcr.io/<repo>:vX.Y.Z      # 拉旧版本
# 改 docker-compose.yml 里 image: 行的 tag，或临时：
docker tag ghcr.io/<repo>:vX.Y.Z ghcr.io/<repo>:latest
docker compose up -d
```

DB schema 是单向迁移（`ensure_*_exists` 加列不会撤销），所以即使回滚镜像，新加的列仍在表里（兼容，旧版本忽略即可）。

---

## 开发模式

需要**同时运行后端和前端**两个进程。

### 环境要求

| 依赖 | 最低版本 |
|---|---|
| Python | 3.10+ |
| Node.js | 18+ |

### 1. 安装依赖

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm install
```

### 2. 准备产品目录

```bash
cp data/catalog.yaml.example data/catalog.yaml
# 按需编辑
```

### 3. 启动服务

**终端 1 — 后端：**

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**终端 2 — 前端：**

```bash
cd frontend
npm run dev
```

访问 **http://localhost:5173**。前端代理已配置，`/api` 请求自动转发到后端。

### 4. 停止

各终端按 `Ctrl + C`。

---

## 产品目录

系统的产品、组件、打印盘配置全部由 `data/catalog.yaml` 定义（唯一数据源）。

- 格式参考 `data/catalog.yaml.example`
- 修改后在网页"产品目录"页面点"重新加载目录"即可生效
- 不需要重启服务

---

## 数据库

- Docker 部署：数据库位于 `data/data.db`
- 开发模式：数据库位于 `backend/data.db`
- 首次启动后自动创建
- 备份：直接复制 `.db` 文件
- 重置：在网页"系统设置"页面点"重置数据库"（保留库存和订单），或删除 `.db` 文件重启（全部清空）

---

## 后端 API 文档

后端启动后，访问自动生成的文档：

- Swagger UI：**http://localhost:8000/docs**
- ReDoc：**http://localhost:8000/redoc**

---

## 目录结构

```
infill/
├── data/
│   ├── catalog.yaml            # 产品目录（用户数据）
│   ├── catalog.yaml.example    # 产品目录示例
│   └── data.db                 # 数据库（Docker 模式，运行后生成）
├── backend/
│   ├── requirements.txt
│   ├── data.db                 # 数据库（开发模式，运行后生成）
│   └── app/
│       ├── main.py             # FastAPI 入口 + 静态文件托管
│       ├── database.py         # 数据库配置
│       ├── models.py           # 数据模型
│       ├── schemas.py          # API 模型
│       ├── routers/            # API 路由
│       │   ├── catalog.py
│       │   ├── orders.py
│       │   ├── inventory.py
│       │   ├── printers.py
│       │   ├── schedule.py
│       │   └── config.py
│       └── services/
│           ├── scheduler.py    # 排班算法
│           ├── catalog.py      # 目录加载
│           └── migrate.py      # 数据库迁移
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── api/client.ts
│       ├── components/Layout.tsx
│       └── pages/
│           ├── Dashboard.tsx
│           ├── Products.tsx
│           ├── Orders.tsx
│           ├── Inventory.tsx
│           ├── Schedule.tsx
│           └── Settings.tsx
├── scripts/
│   └── bundle.sh               # 打包部署脚本
├── Dockerfile
├── docker-compose.yml
└── docs/
    └── playbook.md             # 本文件
```

---

## 常见问题

### 端口被占用

```bash
# Docker 模式：修改 docker-compose.yml 中的端口映射
ports:
  - "9000:8000"    # 改成 9000

# 开发模式
uvicorn app.main:app --reload --port 8001        # 后端换端口
npm run dev -- --port 3000                        # 前端换端口
# 后端换端口后需同步修改 frontend/vite.config.ts 中的代理地址
```

### 局域网访问

Docker 模式下默认监听所有网卡，局域网设备直接用 IP 访问即可（如 `http://192.168.1.100:8000`）。

### 数据库损坏

```bash
# Docker 模式
rm data/data.db
docker compose restart

# 开发模式
rm backend/data.db
# 重启后端
```
