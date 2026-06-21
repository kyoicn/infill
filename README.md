# Infill

面向个人 3D 打印小作坊的生产管理系统：产品目录 / 订单 / 库存 / 多打印机自动排班。
后端 FastAPI + 前端 React，单容器部署（Docker），默认端口 `8000`。

---

## 部署速查（给自动化 agent）

复制粘贴执行即可。要求服务器为 Linux + 已安装 Docker（含 `docker compose` 子命令）。

```bash
# 1. 选择目录
mkdir -p ~/infill && cd ~/infill

# 2. 下载最新 release（tag 名当前就是 git short SHA，可在 Releases 页查最新）
LATEST_TAG=$(curl -fsSL https://api.github.com/repos/kyoicn/infill/releases/latest \
  | grep -m1 '"tag_name"' | cut -d'"' -f4)
curl -fL -o bundle.tar.gz \
  "https://github.com/kyoicn/infill/releases/download/${LATEST_TAG}/infill-deploy-${LATEST_TAG}.tar.gz"

# 3. 解压 + 部署
tar xzf bundle.tar.gz
./deploy.sh

# 4. 健康检查
curl -fsS http://localhost:8000/api/products >/dev/null && echo OK
```

部署成功后访问 `http://<服务器 IP>:8000`。

---

## 系统要求

| 项 | 要求 |
|---|---|
| 操作系统 | Linux（amd64）。macOS 也能跑，仅作开发用 |
| Docker | 20.10+ |
| Docker Compose | v2（命令是 `docker compose`，非旧版 `docker-compose`） |
| 磁盘 | ≥ 500 MB（镜像 ~300 MB + 数据） |
| 端口 | `8000`（可在 `docker-compose.yml` 改） |
| 网络 | 部署过程**离线可用**（镜像打包在 tarball 内，不拉取远端镜像） |

---

## Release 包结构

下载的 `infill-deploy-<sha>.tar.gz` 解压后：

```
.
├── infill-image.tar.gz      # 已构建的 Docker 镜像（gzip）
├── docker-compose.yml       # 编排文件，引用 image: infill:latest
├── deploy.sh                # 一键脚本：load 镜像 → up -d
└── data/
    └── catalog.yaml.example # 产品目录示例（首次部署会复制为 catalog.yaml）
```

`deploy.sh` 做的事：
1. `docker load < infill-image.tar.gz` — 把镜像导入本地
2. 如 `data/catalog.yaml` 不存在，从 `.example` 复制一份
3. `docker compose up -d` — 后台启动

---

## 部署后操作

| 任务 | 命令 |
|---|---|
| 查看日志 | `docker compose logs -f` |
| 停止服务 | `docker compose down` |
| 重启服务 | `docker compose restart` |
| 升级到新版本 | 下载新 tarball 解压覆盖（保留 `data/`），再次跑 `./deploy.sh` |
| 修改产品目录 | 编辑 `data/catalog.yaml`，然后在网页上点"重新加载目录"按钮 |
| 备份数据 | 整个 `data/` 目录（含 `data.db` 和 `catalog.yaml`） |

数据持久化目录：`./data`（通过 docker volume 挂载到容器内 `/app/data`）。

---

## 健康检查 / 验证部署

启动后 10 秒内以下命令应返回 HTTP 200：

```bash
curl -fsS http://localhost:8000/                          # 前端首页
curl -fsS http://localhost:8000/api/products               # 产品列表
curl -fsS http://localhost:8000/api/printers               # 打印机列表
```

如果 `curl` 失败，看容器状态：

```bash
docker compose ps         # 容器应是 running 状态
docker compose logs --tail=50 app
```

---

## 常见问题排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `docker: command not found` | 未装 Docker | 装 Docker Engine 后重试 |
| `docker compose` 报未知命令 | 仅有 Compose v1 (`docker-compose`) | 升级到 Docker Compose v2 |
| 端口 8000 已占用 | 其他服务占用 | 改 `docker-compose.yml` 中 `ports: ["NEW_PORT:8000"]` |
| 启动后 catalog 为空 | 用户未编辑 `data/catalog.yaml` | 编辑后点网页上"重新加载目录" |
| 升级后数据丢失 | 解压时覆盖了 `data/` | 升级前备份；只覆盖 `infill-image.tar.gz` / `docker-compose.yml` / `deploy.sh` 三个文件 |
| 架构不匹配（exec format error） | 镜像是 amd64，服务器是 arm64 | 用源码自行 `docker build`（见下文） |

---

## 从源码构建（替代方案）

适用于：服务器架构和 release 镜像不匹配，或想跑最新未发布的 commit。

```bash
git clone https://github.com/kyoicn/infill.git
cd infill
docker compose up -d --build
# 首次启动后：
cp data/catalog.yaml.example data/catalog.yaml  # 如不存在
```

---

## 项目结构（简版）

```
backend/    FastAPI + SQLAlchemy + SQLite，同时托管前端静态文件
frontend/   React + TypeScript + Ant Design + Vite
data/       运行时数据（catalog.yaml + data.db），通过 volume 挂载
release/    本地构建产物（已 gitignore）
scripts/    bundle.sh — 生成 release tarball
docs/       详细设计文档（STATUS.md 为整体状态概览）
```

更详细的功能、数据模型、算法说明见 [docs/STATUS.md](docs/STATUS.md)。

---

## 本地开发

端口约定（在 `vite.config.ts` + `scripts/qa-server.sh` 同步固定）：

| 服务 | dev 端口 | 备注 |
|---|---|---|
| 前端（vite dev） | `5173` | `/api` 代理到后端 |
| 后端（uvicorn） | `8765` | 避开 8000 常见冲突；生产 docker 仍是 `8000` |

vite 配了 `strictPort: true`，5173 被占就直接报错退出，绝不静默跳 5174。

一键起：

```bash
bash scripts/qa-server.sh start   # 起前后端
bash scripts/qa-server.sh stop    # 关
```

不走脚本：

```bash
(cd backend && uvicorn app.main:app --reload --port 8765) &
(cd frontend && npm run dev) &
```

QA 入口：http://localhost:5173

---

## Release 发布流程（仅维护者）

```bash
./scripts/bundle.sh                                    # 产物在 release/
SHA=$(git rev-parse --short HEAD)
git tag "$SHA" && git push origin "$SHA"
gh release create "$SHA" "release/infill-deploy-${SHA}.tar.gz" \
  --title "$SHA" --generate-notes
```
