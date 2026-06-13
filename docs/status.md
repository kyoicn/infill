# 项目状态

> 自动生成的项目状态摘要。
> 最后更新：2026-06-13 14:22:22 (UTC+8)

## 概述

本项目是面向个人 3D 打印小作坊的生产管理系统，覆盖产品目录、订单管理、组件库存、打印机排班、系统配置五大模块。核心价值是「晚间盘点 10 分钟，自动生成第二天可直接执行的多打印机排班表」。目前代码已全量实现，处于 backfill PRD 后待首次 QA 的阶段。

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端框架 | React | 19.2.4 |
| 前端 UI | Ant Design (antd) | 6.3.4 |
| 前端路由 | react-router-dom | 7.13.2 |
| 前端图标 | @ant-design/icons | 6.1.1 |
| 前端构建 | Vite + TypeScript | 8.0.1 / 5.9.3 |
| 后端框架 | FastAPI | 0.115.12 |
| 后端服务器 | Uvicorn | 0.34.2 |
| ORM | SQLAlchemy | 2.0.40 |
| 数据校验 | Pydantic | 2.11.1 |
| 目录解析 | PyYAML | 6.0.3 |
| 数据库 | SQLite（`backend/data.db`） | — |
| 容器化 | Docker + docker-compose | — |

## 架构

单体全栈应用，前后端分离但打包在同一 Docker 镜像中：

- **后端**（`backend/app/`）：FastAPI 应用，`main.py` 注册所有路由，`lifespan` 启动时从 `data/catalog.yaml` 差量同步目录进 SQLite。路由层（`routers/`）薄，核心业务逻辑在 `services/`。排班算法分层：`scheduler_core.py` 是单一纯函数算法核心（含 `_sync_penalty()` additive 同步惩罚、`schedule_greedy()` 三策略共用贪心主循环、two_phase 凑整放弃逻辑、`SURPLUS_TARGET_PRODUCTS`/`DEFAULT_CHANGEOVER_MINUTES`/`CAPACITY_SAFETY_MARGIN`/`SYNC_PENALTY_CHANGEOVER_MULT` 常量集中源）；`scheduler.py` 仅保留 DB 服务层与 `_persist_scheduled()` 共享持久化辅助。
- **前端**（`frontend/src/`）：React SPA，`App.tsx` 配置路由，六个页面组件（`pages/`）对应六条路由，所有接口调用经 `api/client.ts` 集中管理。
- **数据流**：`catalog.yaml` → `load_catalog()` → SQLite（目录、库存初始行）→ FastAPI REST API → React 前端状态 → 用户界面。订单发货和排班任务完成是库存减少/增加的两个唯一入口。
- **部署**：Docker 内后端静态服务前端 `dist/`，数据库与 catalog 文件通过卷挂载持久化。

## CUJ 状态

每行记录三个独立维度的最新已知状态：**Impl**（代码是否存在）、**QA**（工程验证）、**PM**（产品判断）。

| CUJ | PRD | 优先级 | Impl | QA | PM |
|-----|-----|--------|------|----|----|
| CUJ-1：浏览只读产品目录 | prd-000 | P0 | merged | — | — |
| CUJ-2：编辑 catalog.yaml 后重新加载 | prd-000 | P0 | merged | — | — |
| CUJ-1：录入新订单 | prd-001 | P0 | merged | — | — |
| CUJ-2：查看与管理待处理订单队列 | prd-001 | P0 | merged | — | — |
| CUJ-3：标记订单发货并自动扣减库存 | prd-001 | P0 | merged | — | — |
| CUJ-4：查看已发货订单历史 | prd-001 | P1 | merged | — | — |
| CUJ-1：查看组件库存与富余 | prd-002 | P0 | merged | — | — |
| CUJ-2：手动调整库存数量 | prd-002 | P0 | merged | — | — |
| CUJ-3：Dashboard 库存预警与库存/需求总览 | prd-002 | P1 | merged | — | — |
| CUJ-1：生成排班表 | prd-003 | P0 | merged | PASS | — |
| CUJ-2：查看排班（列表 + 甘特图 + 总结） | prd-003 | P0 | merged | — | — |
| CUJ-3：确认排班并按批次执行 | prd-003 | P0 | merged | — | — |
| CUJ-4：手动编辑草稿排班 | prd-003 | P1 | merged | — | — |
| CUJ-5：设收菜闹钟 | prd-003 | P1 | merged | — | — |
| CUJ-1：管理打印机 | prd-004 | P0 | merged | — | — |
| CUJ-2：配置操作时间窗口 | prd-004 | P0 | merged | — | — |
| CUJ-3：配置换版时间 | prd-004 | P0 | merged | — | — |
| CUJ-4：重置数据库 | prd-004 | P1 | merged | — | — |

**列值说明：**
- `Impl`：`not started`（无代码）| `in progress`（部分代码）| `merged`（代码已存在并可构建）
- `QA`：`PASS` | `FAIL` | `BLOCKED` | `NOT_RUN` | `WAIVED` | `—`（尚无 QA 运行）
- `PM`：`Satisfied` | `Caveats` | `Not done` | `—`（尚无 PM 评审）

CUJ **完全完成**的条件：Impl=`merged` AND QA=`PASS` AND PM=`Satisfied`。本轮迭代由 `docs/qa-report.md` 给出 prd-003 CUJ-1 的权威 QA 判定（PASS）；其余 CUJ 的 QA 仅做冒烟回归未变更状态，PM 评审尚未开始。

## 核心数据类型

| 类型 | 关键字段 | 用途 |
|------|----------|------|
| `Component` | `id, name, description, colors(JSON)` | 打印组件台账，目录来源 |
| `PrintConfig` | `id, plate_name, component_id, quantity, duration_minutes` | 打印盘配置，排班任务的基本单元 |
| `Product` | `id, name, description` | 销售产品，关联 BOM |
| `ProductComponent` | `product_id, component_id, color, quantity` | 产品 BOM 明细 |
| `Inventory` | `id, component_id, color, quantity` | 组件+颜色级库存，唯一性靠逻辑保证 |
| `Order` | `id, created_at, status, shipped_at` | 订单头，status ∈ {pending, shipped} |
| `OrderItem` | `id, order_id, product_id, quantity` | 订单明细行 |
| `Printer` | `id, name` | 打印机台账，无唯一约束 |
| `ScheduleConfig` | `id, day_of_week, windows(JSON)` | 每星期几的操作时间窗口，DB 有唯一约束 |
| `SystemConfig` | `id, key, value` | 通用 KV 配置，当前只用 `changeover_minutes` |
| `PrintPlan` | `id, date, start_time, duration_hours, status, created_at` | 排班表头，status ∈ {draft, confirmed} |
| `PrintBatch` | `id, plan_id, start_time, batch_order, status` | 一组同时启动的任务，status ∈ {pending, started, completed} |
| `PrintTask` | `id, batch_id, printer_id, print_config_id, color, is_surplus, start_time, end_time, status` | 单台打印机的单个任务，status ∈ {pending, completed, cancelled, failed} |

## 数据流

```
catalog.yaml
    │ load_catalog()（启动 lifespan / POST /api/catalog/reload）
    ▼
SQLite: Component / PrintConfig / Product / ProductComponent / Inventory（初始 quantity=0）
    │
    ├── 订单录入 POST /api/orders ──────────────────────┐
    │                                                   │
    ├── 发货 POST /api/orders/{id}/ship                  │
    │        └── Inventory quantity -= BOM×数量          │
    │                                                   │
    ├── 排班生成 POST /api/schedule/generate              │
    │        └── 消费 Inventory surplus + 待处理订单需求  │
    │                                                   │
    ├── 任务完成 POST /api/schedule/tasks/{id}/complete   │
    │        └── Inventory quantity += 盘产量            │
    │                                                   │
    └─────────────────────────────────────────────────▶ React SPA
                GET /api/* → 前端内存状态 → Ant Design UI
```

富余计算（`GET /api/inventory/surplus`）= 当前库存 − 全部待处理订单 BOM 折算需求，组件+颜色粒度，不折算为可组装产品数。排班算法的供给起点（`_get_initial_supply`）= 库存 + 比当前更早已排班的产出，口径与库存页不同。

## 文件结构

```
infill/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 应用入口，lifespan，静态文件服务
│   │   ├── models.py            # SQLAlchemy ORM 模型（全部 13 张表）
│   │   ├── schemas.py           # Pydantic 请求/响应 schema
│   │   ├── database.py          # SQLite 连接与 SessionLocal
│   │   ├── routers/
│   │   │   ├── catalog.py       # GET /api/components、/api/products、POST /api/catalog/reload
│   │   │   ├── orders.py        # 订单 CRUD + POST /ship
│   │   │   ├── inventory.py     # GET/POST adjust/PUT set/GET surplus
│   │   │   ├── printers.py      # 打印机 CRUD
│   │   │   ├── config.py        # 操作窗口 upsert、换版时间 upsert、POST reset-db
│   │   │   └── schedule.py      # 排班生成、确认、执行状态机
│   │   └── services/
│   │       ├── catalog.py       # load_catalog()：YAML 差量同步入 DB
│   │       ├── scheduler.py     # DB 服务层 + _persist_scheduled 共享持久化辅助
│   │       ├── scheduler_core.py# 纯函数算法核心：三策略共用 schedule_greedy + additive 同步惩罚 + 常量单一源
│   │       └── migrate.py       # 数据库迁移辅助
│   ├── tests/
│   │   └── test_scheduler.py    # scheduler_core 单元测试（850 行）
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.tsx              # 路由配置（Layout + Sider）
│       ├── api/client.ts        # 全部 API 方法集中定义
│       └── pages/
│           ├── Dashboard.tsx    # 仪表盘：统计卡 + 库存/需求总览
│           ├── Products.tsx     # 产品目录：三张只读表 + 重新加载
│           ├── Orders.tsx       # 订单管理：三 Tab + 新增弹窗 + 发货
│           ├── Inventory.tsx    # 库存管理：整表行内编辑
│           ├── Schedule.tsx     # 排班中心：生成表单 + 列表 + 甘特图 + 执行 + 闹钟
│           └── Settings.tsx     # 系统设置：打印机 + 窗口 + 换版时间 + DB 重置
├── data/
│   └── catalog.yaml             # 目录单一数据源（组件/打印盘/产品 BOM）
├── docs/
│   ├── prd/                     # PRD 索引与五份 PRD（backfill，2026-06-13）
│   ├── design/                  # 设计文档（catalog、frontend、orders-inventory、scheduler、system）
│   ├── specs.md                 # 原始详细设计规格（基准文档）
│   ├── schedule_specs.md        # 排班算法活文档
│   └── project-overview.md      # 原 STATUS.md，项目整体长篇报告
├── Dockerfile
└── docker-compose.yml
```

## 近期活动

本轮 dev-cycle 聚焦排班算法的统一与修复，相关提交（自旧 → 新）：

| 提交 | 性质 | 摘要 |
|------|------|------|
| `3875ae5` refactor | Task-C | `SURPLUS_TARGET_PRODUCTS` 集中到 `scheduler_core.py = 20`，删除其余两处副本 |
| `733ac88` fix | Task-B | two_phase partial 分支改为「凑整放弃」（单 `break`），消除孤儿长盘产出 |
| `b9fdc83` refactor | Task-A | 统一 `_sync_penalty()` 为 additive equivalent-idle 公式；product_first/utilization 贪心主循环下沉到 `schedule_greedy()`；删除 `scheduler.py._pick_task` 与内联循环；抽出 `_persist_scheduled()` 共享持久化辅助 |
| `8b0f35a` docs | Task-F | `schedule_specs.md` §9.3 改写为 additive 公式；§5.1/§10 明确 hard-FIFO + skip-if-stock-suffices；§7.3 surplus = 20；常量（`DEFAULT_CHANGEOVER_MINUTES`、`CAPACITY_SAFETY_MARGIN`、`SYNC_PENALTY_CHANGEOVER_MULT`）集中说明 |
| `c44ca23` chore | code review | 删除未使用 import；收紧 printers 类型标注 |

行为变化：`sync_strength` 语义现在三种策略下完全一致（additive equivalent-idle，不再是旧的 multiplicative 强压机制）。QA 报告（`docs/qa-report.md`）已就本轮 prd-003 CUJ-1 给出 PASS 终判：74/74 单测全绿；前端两轮独立 walk 完全一致；红线（不动 routers/前端/DB schema/富余口径）未触犯。

## 已知问题与待办

以下问题来自各 PRD 的 Open Questions 与代码现状，供 QA/PM 评审时参考：

**排班算法（prd-003）**
- `_pick_task` 双实现分歧：`product_first`/`utilization` 用 multiplicative 同步惩罚，`two_phase` 用 additive 惩罚，`schedule_specs.md §9.3` 描述已过时，对用户的表现是同一 `sync_strength` 在不同策略下行为不一致。
- 操作窗口默认值在 `scheduler.py:53` 与 `Settings.tsx` 两处各自硬编码，存在漂移风险。
- `changeover_minutes` 默认值 15 分散在三处（`scheduler.py`、`schedule.start_batch`、前端），无单一常量。
- 任务 `status=pending` 在前端渲染为「进行中」，与批次 pending 的「待开始」文案不一致。
- 完成入库时若无匹配 `Inventory` 行，库存不增但 toast 仍报 `+N`（数字与实际不符）。
- 跨夜批次时间解析（>24:00）与闹钟收菜换算可能偏差。

**库存管理（prd-002）**
- 富余未折算为「可组装产品数」，与 `specs.md §8.1` 的产品级富余意图存在差距。
- 富余口径不含已排班产出，与排班总结页的口径不一致，同一组件可能在两处看到不同数字。
- `Inventory` 表 `(component_id, color)` 无 DB 唯一约束，靠逻辑保证。
- 整表编辑保存（`Promise.all`）非原子，中途失败会部分落库。

**订单管理（prd-001）**
- 批量创建订单为 N 个独立请求，中途失败产生部分落库且无清晰反馈。
- 删除已发货订单不回补库存，无撤销发货入口。
- 发货失败报错用组件 id 而非名称，可读性差。
- `shipped_at` 已落库但列表无对应列展示。

**产品目录（prd-000）**
- 三个 GET 接口无 `.catch()`，失败时页面静默空态无提示。
- 改名按名称匹配，等同删旧建新，可能产生重复记录。
- `load_catalog` 无单元测试。

**系统配置（prd-004）**
- 打印机改名无 UI 入口（后端接口存在但未接入）。
- 重置数据库不 seed 默认操作窗口/换版时间，重置后排班继续走硬编码 fallback。
- 保存空窗口（`windows=[]`）与「未配置」在 UI 上显示相同文案，但算法行为相反。
- 操作窗口和换版时间保存无错误 toast 兜底。
- 规格文档（specs.md §8.6）把富余生产开关归入系统设置，当前实现为排班请求参数，不持久化。
