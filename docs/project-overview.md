# 项目状态

> 自动生成的项目状态摘要。
> 最后更新：2026-05-05

## 概览

这是一个面向个人3D打印小作坊的生产管理系统，核心功能是管理产品目录、订单、组件库存，并自动生成多台打印机的排班表。项目当前处于功能基本完整阶段：前后端均已实现，排班算法已完成三种调度策略并具备单元测试覆盖，支持 Docker 一键部署。

---

## 技术栈

| 层 | 选型 | 版本 |
|---|---|---|
| 前端框架 | React + TypeScript | React 19, TS 5.9 |
| 前端 UI | Ant Design | 6.3.4 |
| 前端路由 | react-router-dom | 7.13.2 |
| 前端构建 | Vite | 8.0 |
| 后端框架 | FastAPI | 0.115.12 |
| 后端服务器 | Uvicorn | 0.34.2 |
| ORM | SQLAlchemy | 2.0.40 |
| 数据验证 | Pydantic | 2.11.1 |
| 数据库 | SQLite | — |
| 产品目录格式 | YAML (PyYAML 6.0.3) | — |
| 容器化 | Docker + docker-compose | — |

注：前端未引入 AntV/G2（specs.md 中有规划），甘特图尚未实现。

---

## 架构

### 整体结构

```
infill/
├── backend/
│   └── app/
│       ├── main.py              # FastAPI 入口，同时托管前端静态文件
│       ├── database.py          # SQLite 连接配置
│       ├── models.py            # SQLAlchemy ORM 模型（10 张表）
│       ├── schemas.py           # Pydantic 请求/响应 schema
│       ├── routers/             # API 路由（按业务域拆分）
│       │   ├── catalog.py       # 产品目录（只读展示 + 重新加载）
│       │   ├── orders.py        # 订单管理
│       │   ├── inventory.py     # 库存管理
│       │   ├── printers.py      # 打印机管理
│       │   ├── schedule.py      # 排班生成与管理
│       │   └── config.py        # 系统配置（操作窗口、换料时间等）
│       └── services/
│           ├── catalog.py       # YAML 目录加载逻辑
│           ├── migrate.py       # 数据库迁移
│           ├── scheduler.py     # 排班算法服务（DB 层，协调数据读写）
│           └── scheduler_core.py # 排班核心算法（纯函数，无 DB 依赖）
├── frontend/
│   └── src/
│       ├── api/client.ts        # API 客户端封装
│       ├── App.tsx              # 路由配置
│       ├── components/Layout.tsx
│       └── pages/
│           ├── Dashboard.tsx
│           ├── Products.tsx
│           ├── Orders.tsx
│           ├── Inventory.tsx
│           ├── Schedule.tsx
│           └── Settings.tsx
├── data/
│   ├── catalog.yaml             # 产品目录（唯一数据源，用户直接编辑）
│   └── catalog.yaml.example
├── Dockerfile
├── docker-compose.yml
└── scripts/bundle.sh            # 离线打包部署脚本
```

### 部署模式

- **开发模式**：前端 Vite dev server（localhost:5173）代理 `/api` 到后端（localhost:8000）
- **生产/Docker 模式**：后端 FastAPI 同时托管前端构建产物（`/`）和 API（`/api/`），单进程单端口（8000）

---

## 功能状态

### 已实现

**产品目录**
- 从 `data/catalog.yaml` 加载组件、打印配置（打印盘）、产品 BOM
- 支持组件颜色属性（`colors` 字段，JSON 列表）
- 网页只读展示，提供"重新加载目录"按钮热更新

**订单管理**
- 新增订单（选择产品和数量）
- 待处理订单列表（按 `created_at` FIFO 排序）
- 标记发货：自动扣减对应组件库存（按 BOM 计算）
- 已发货订单历史查看

**库存管理**
- 各组件各颜色的实时库存跟踪
- 手动调整库存数量
- 展示库存相对于待处理订单的富余情况（折算为可组装产品数）

**排班生成**
- 根据订单需求、产品 BOM、当前库存自动生成打印机排班表
- 三种调度策略（见下文）
- 可配置参数：排班日期、开始时间（默认 00:00）、排班时长、富余生产开关、指定产品过滤、同步强度（`sync_strength` 0~100 滑块）
- 排班结果分批次（`PrintBatch`）展示，每批显示启动时间和各打印机任务
- 批次状态管理：pending → started → completed
- 单任务状态标记（completed）
- 列表视图已实现

**调度算法（scheduler_core.py）**
- 策略一 `product_first`（默认）：维护模拟库存，按产品完成度评分优先凑齐可发货产品
- 策略二 `utilization`：纯最小空闲时间，最大化打印机利用率
- 策略三 `two_phase`（智能规划）：两阶段法，阶段 1 全局规划各组件配比，阶段 2 贪心排程；支持溢出复用
- 同步强度机制（`sync_strength`）：锚定首台打印机时长，后续打印机按偏差惩罚倾向选相近时长任务
- 操作窗口约束：任务只能在配置的时间窗口内启动，跨窗口运行，跨天自动拼接
- 换料时间约束（`changeover_minutes`，默认 15 分钟）
- 富余生产（`surplus_enabled`）：瓶颈算法补充限制组装的瓶颈组件，上限 `SURPLUS_TARGET_PRODUCTS = 20`
- 产品过滤（`target_product_ids`）：限定本次排班只考虑指定产品
- 需求计算：FIFO 逐订单，初始供给 = 库存 + 早于排班日的已排班产出

**算法单元测试（backend/tests/test_scheduler.py，850 行）**
- 覆盖 `find_next_start`、`idle_after`、`product_completion_score`、`try_assemble`、`pick_task`、`schedule_tasks`、`plan_two_phase`、`count_complete_products` 等核心纯函数
- 测试场景包括简单桌子 BOM、混合时长真实场景、两阶段溢出复用

**系统配置**
- 打印机管理（增减打印机，支持批量创建）
- 操作时间窗口配置（按星期几设置多个时间段）
- 换料时间配置
- 富余生产开关
- 数据库重置功能

**部署**
- Docker 一键部署（docker-compose.yml）
- 离线打包部署脚本（scripts/bundle.sh）
- 数据持久化：`data/` 目录通过 volume 挂载

**闹钟功能**
- Schedule 页面支持设置闹钟提醒（用于提醒收菜时间）

### 进行中 / 近期重点

- **排班算法精化**（最近 5 个 commit 的主要焦点）：
  - 同步强度参数（`sync_strength`）实现：加法惩罚叠加机制 + 动态批次启动时机
  - `scheduler_core.py` 提取：将核心算法从 `scheduler.py` 分离为纯函数模块，并配套单元测试
  - `two_phase` 策略引入：两阶段全局规划
  - 产品过滤功能

### 规划中 / 尚未实现

- **甘特图视图**：specs.md 和 schedule_specs.md 均有描述，前端 Schedule.tsx 有 `viewMode` 状态（list/gantt），但甘特图渲染组件尚未实现（AntV/G2 未引入依赖）
- **排班手动调整**：specs.md 描述了删除/替换任务、增减批次的能力，当前界面和 API 支持有限
- **Dashboard 仪表盘**：页面文件存在但功能深度待核查（甘特图概览、库存预警等）
- **未来扩展（明确不在当前范围）**：云部署、多用户、打印机实时监控、电商平台对接、历史统计

---

## 核心数据类型

| 类型 | 文件 | 关键字段 | 用途 |
|---|---|---|---|
| `Component` | models.py | `name`, `colors` (JSON) | 可打印组件，支持多颜色 |
| `PrintConfig` | models.py | `plate_name`, `component_id`, `quantity`, `duration_minutes` | 打印盘配置（一次打印 N 个，耗时 M 分钟） |
| `Product` | models.py | `name` | 最终产品 |
| `ProductComponent` | models.py | `product_id`, `component_id`, `color`, `quantity` | 产品 BOM |
| `Order` / `OrderItem` | models.py | `status` (pending/shipped), `created_at` | 订单队列 |
| `Inventory` | models.py | `component_id`, `color`, `quantity` | 组件库存（含颜色维度） |
| `PrintPlan` | models.py | `date`, `start_time`, `duration_hours`, `status` (draft/confirmed) | 排班表头 |
| `PrintBatch` | models.py | `plan_id`, `start_time`, `batch_order`, `status` | 批次（一组同时启动的任务） |
| `PrintTask` | models.py | `batch_id`, `printer_id`, `print_config_id`, `color`, `is_surplus`, `start_time`, `end_time`, `status` | 单条打印任务 |
| `ConfigInfo` | scheduler_core.py | `id`, `component_id`, `quantity`, `duration_minutes` | 算法层的打印配置纯数据表示 |
| `ScheduledTask` | scheduler_core.py | `printer_index`, `config_id`, `color`, `is_surplus`, `start_min`, `end_min`, `batch_index` | 算法层的已排程任务（分钟时间戳） |
| `DemandKey` | scheduler_core.py | `(component_id, color)` | 组件需求维度（二元组） |

---

## 数据流

```
data/catalog.yaml
  └─ 后端启动 / "重新加载目录" → catalog.py 解析 → 写入 Component / PrintConfig / Product / ProductComponent 表

用户录入订单 → Order / OrderItem 表

用户发货 → 扣减 Inventory（按 BOM 计算各组件数量）

生成排班：
  scheduler.py._get_initial_supply()
    → 读取 Inventory + 已排班产出
    → 传入 scheduler_core
  scheduler.py._build_demand_pool()
    → 按 FIFO 遍历 Order，计算各 (component_id, color) 净需求
    → 生成任务池（需求任务 + 富余任务）
  scheduler_core.plan_two_phase() / 贪心调度循环
    → 输出 list[ScheduledTask]（分钟时间戳）
  scheduler.py
    → 将 ScheduledTask 写入 PrintPlan / PrintBatch / PrintTask 表

前端 Schedule.tsx
  → api.generatePlan() → 展示批次列表
  → 用户逐批标记 started / completed
```

---

## 近期活动

最近 5 次提交均集中在排班算法层：

| Commit | 内容 |
|---|---|
| `75089ee` | 同步惩罚改为加法叠加；批次启动时机动态化（dynamic batch start timing） |
| `4ef0720` | 提取 `scheduler_core.py` 纯函数层；新增 850 行单元测试 |
| `15592ef` | 前端 Schedule.tsx 新增 `sync_strength` 滑块控件 |
| `9ba3ca5` | 新增 `two_phase` 调度策略；调整 UI 布局 |
| `b7f1252` | 优化任务调度逻辑（scheduler.py 重构） |

活跃变更区域：`backend/app/services/scheduler*.py`、`backend/tests/`、`frontend/src/pages/Schedule.tsx`、`docs/schedule_specs.md`。

---

## 已知问题与待办

- **甘特图未实现**：`Schedule.tsx` 中 `viewMode` 有 `'gantt'` 选项，但对应渲染逻辑缺失；AntV/G2 未加入 `package.json`
- **排班手动调整有限**：specs.md 描述了删除任务、替换任务、增减批次的编辑能力，目前 API 和 UI 支持程度待核查
- **`SURPLUS_TARGET_PRODUCTS`** 在 `scheduler.py` 和 `scheduler_core.py` 中各定义了一次（均为 20），存在重复常量
- **Dashboard 页面**：文件存在，但甘特图概览、低库存预警等规划功能的实现程度不明确，需核查
- **`ScheduleConfig` 默认窗口硬编码**：`scheduler.py` 第 53 行在数据库无配置时 fallback 到 `[(480, 720), (750, 1080), (1110, 1380)]`，应通过初始化迁移写入默认值
