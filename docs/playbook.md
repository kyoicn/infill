# 运行手册

## 环境要求

| 依赖 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.10+ | 后端运行时 |
| Node.js | 18+ | 前端构建和开发 |
| npm | 8+ | 随 Node.js 安装 |

---

## 首次安装

### 1. 安装后端依赖

```bash
cd backend
pip install -r requirements.txt
```

依赖列表：
- fastapi — Web 框架
- uvicorn — ASGI 服务器
- sqlalchemy — ORM
- pydantic — 数据校验

### 2. 安装前端依赖

```bash
cd frontend
npm install
```

---

## 启动服务

需要**同时运行后端和前端**两个进程。建议开两个终端窗口。

### 终端 1：启动后端

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- `--reload`：代码修改后自动重启（开发模式）
- `--host 0.0.0.0`：允许局域网内其他设备访问（如不需要可省略）
- `--port 8000`：后端端口，默认 8000

启动成功后会看到类似输出：
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
```

首次启动时会自动在 `backend/` 目录下创建 `data.db` 数据库文件。

### 终端 2：启动前端

```bash
cd frontend
npm run dev
```

启动成功后会看到类似输出：
```
VITE v6.x.x  ready in xxx ms

➜  Local:   http://localhost:5173/
➜  Network: http://192.168.x.x:5173/
```

### 3. 打开浏览器

访问 **http://localhost:5173** 即可使用。

前端开发服务器已配置代理，所有 `/api` 请求会自动转发到后端的 `http://localhost:8000`。

---

## 停止服务

在各自的终端窗口按 `Ctrl + C` 即可停止。

---

## 数据库

- 数据库文件位于 `backend/data.db`（SQLite）
- 首次启动后端时自动创建
- 备份：直接复制 `data.db` 文件即可
- 重置：删除 `data.db` 文件后重启后端，会自动生成空数据库

---

## 后端 API 文档

后端启动后，可以访问自动生成的 API 文档：

- Swagger UI：**http://localhost:8000/docs**
- ReDoc：**http://localhost:8000/redoc**

---

## 目录结构

```
infill/
├── docs/
│   ├── requirements.md      # 原始需求
│   ├── specs.md              # 详细设计规格（开发基准）
│   └── playbook.md           # 本文件
├── backend/
│   ├── requirements.txt      # Python 依赖
│   ├── data.db               # SQLite 数据库（运行后生成）
│   └── app/
│       ├── main.py           # FastAPI 应用入口
│       ├── database.py       # 数据库连接配置
│       ├── models.py         # SQLAlchemy 数据模型
│       ├── schemas.py        # Pydantic 请求/响应模型
│       ├── routers/          # API 路由
│       │   ├── components.py # 组件管理
│       │   ├── products.py   # 产品管理
│       │   ├── orders.py     # 订单管理
│       │   ├── inventory.py  # 库存管理
│       │   ├── printers.py   # 打印机管理
│       │   ├── schedule.py   # 排班管理
│       │   └── config.py     # 系统配置
│       └── services/
│           └── scheduler.py  # 排班算法
└── frontend/
    ├── package.json
    ├── vite.config.ts        # Vite 配置（含 API 代理）
    └── src/
        ├── main.tsx          # 应用入口
        ├── App.tsx           # 路由配置
        ├── api/
        │   └── client.ts     # API 客户端
        ├── components/
        │   └── Layout.tsx    # 侧边栏布局
        └── pages/
            ├── Dashboard.tsx # 仪表盘
            ├── Products.tsx  # 产品目录
            ├── Orders.tsx    # 订单管理
            ├── Inventory.tsx # 库存管理
            ├── Schedule.tsx  # 排班中心
            └── Settings.tsx  # 系统设置
```

---

## 常见问题

### 端口被占用

如果 8000 或 5173 端口被占用：

```bash
# 后端换端口
uvicorn app.main:app --reload --port 8001

# 前端换端口
npm run dev -- --port 3000
```

如果后端换了端口，需要同步修改 `frontend/vite.config.ts` 中的代理地址。

### 数据库损坏或需要重置

```bash
rm backend/data.db
# 重启后端即可
```

### 局域网内其他设备访问

确保后端启动时使用了 `--host 0.0.0.0`，然后用本机局域网 IP 访问前端页面即可（如 `http://192.168.1.100:5173`）。
