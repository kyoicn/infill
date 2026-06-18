# 项目状态

> 自动生成的项目状态摘要。
> 最后更新：2026-06-18 18:04:17 (UTC+8)

## 概述

本项目是面向个人 3D 打印小作坊的生产管理系统，覆盖产品目录、订单管理、组件库存、打印机排班、系统配置、产品录入六大模块。核心价值是「晚间盘点 10 分钟，自动生成第二天可直接执行的多打印机排班表」。Iter3 刚刚交付 prd-005「产品录入」全部 5 个 CUJ（QA 经 2 轮 retry 验证 PASS），目前等待 PM 评审。

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
| 图像处理 | Pillow | ≥10.0,<12.0 |
| HTTP 客户端（LLM） | httpx | ≥0.27 |
| 文件上传 | python-multipart | ≥0.0.9 |
| 数据库 | SQLite（`backend/data.db`） | — |
| LLM Provider | DeepSeek（multimodal chat completions） | — |
| 容器化 | Docker + docker-compose | — |

## 架构

单体全栈应用，前后端分离但打包在同一 Docker 镜像中：

- **后端**（`backend/app/`）：FastAPI 应用，`main.py` 注册所有路由（catalog / orders / inventory / printers / config / schedule / intake），`lifespan` 启动时从 `data/catalog.yaml` 差量同步目录进 SQLite。路由层（`routers/`）薄，核心业务逻辑在 `services/`。排班算法分层：`scheduler_core.py` 是单一纯函数算法核心（含 `_sync_penalty()` additive 同步惩罚、`schedule_greedy()` 三策略共用贪心主循环、two_phase 凑整放弃逻辑、`SURPLUS_TARGET_PRODUCTS`/`DEFAULT_CHANGEOVER_MINUTES`/`CAPACITY_SAFETY_MARGIN`/`SYNC_PENALTY_CHANGEOVER_MULT` 常量集中源）；`scheduler.py` 仅保留 DB 服务层与 `_persist_scheduled()` 共享持久化辅助。产品录入算法分层：`services/intake.py` 负责会话/文件管理、启发式分类、撞名检测、5 阶段事务 merge + 回滚；`services/intake_llm.py` 封装 DeepSeek provider（多图单请求、HTTP 错误 → error_kind 映射、JSON 解析容错）；`schemas_intake.py` 单独存放 intake 端点 Pydantic 模型。
- **前端**（`frontend/src/`）：React SPA，`App.tsx` + `components/Layout.tsx` 配置路由，七个页面组件对应七条路由，所有接口调用经 `api/client.ts` 集中管理。产品录入页 `pages/Intake.tsx` 是父级状态机（5 个 mode：upload / recognize / draft / color / merge），把 sessionId / assemblyImages / produceImages 等子状态提升到父组件以保证「返回上一步」时图与基名不丢；5 个子组件在 `pages/intake/`（Upload / Recognizing / Draft / Color / Preview + IntakeError / Success），共享 `colorPalette.ts` 11 色常用色板 + `durationFormat.ts` 时长格式化 + `errorMessages.ts` 错误文案。
- **数据流**：`catalog.yaml` ⇄ `load_catalog()` ⇄ SQLite（目录、库存初始行）⇄ FastAPI REST API ⇄ React 前端状态 → 用户界面。订单发货和排班任务完成是库存减少/增加的两个唯一入口。产品录入 merge 是写 catalog.yaml 的唯一入口（5 阶段事务：撞名兜底 → 备份 → append + 复读 → load_catalog → 失败回滚）。
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
| CUJ-1：生成排班表 | prd-003 | P0 | merged | PASS | Satisfied |
| CUJ-2：查看排班（列表 + 甘特图 + 总结） | prd-003 | P0 | merged | — | — |
| CUJ-3：确认排班并按批次执行 | prd-003 | P0 | merged | — | — |
| CUJ-4：手动编辑草稿排班 | prd-003 | P1 | merged | — | — |
| CUJ-5：设收菜闹钟 | prd-003 | P1 | merged | — | — |
| CUJ-1：管理打印机 | prd-004 | P0 | merged | — | — |
| CUJ-2：配置操作时间窗口 | prd-004 | P0 | merged | — | — |
| CUJ-3：配置换版时间 | prd-004 | P0 | merged | — | — |
| CUJ-4：重置数据库 | prd-004 | P1 | merged | — | — |
| CUJ-1：上传截图 + 自动分类 | prd-005 | P0 | merged | PASS | — |
| CUJ-2：触发 LLM 识别 | prd-005 | P0 | merged | PASS | — |
| CUJ-3：草稿校对 BOM + 打印盘 | prd-005 | P0 | merged | PASS | — |
| CUJ-4：颜色矩阵 + 多配色变体 | prd-005 | P0 | merged | PASS | — |
| CUJ-5：合并到 catalog.yaml | prd-005 | P0 | merged | PASS | — |
| CUJ-1：扫描小红书千帆订单 | prd-006 | P0 | not started | — | — |
| CUJ-2：扫描闲鱼订单 | prd-006 | P0 | not started | — | — |
| CUJ-3：预览校对 + 一键导入 | prd-006 | P0 | not started | — | — |
| CUJ-4：自动导入设置 | prd-006 | P1 | not started | — | — |

**列值说明：**
- `Impl`：`not started`（无代码）| `in progress`（部分代码）| `merged`（代码已存在并可构建）
- `QA`：`PASS` | `FAIL` | `BLOCKED` | `NOT_RUN` | `WAIVED` | `—`（尚无 QA 运行）
- `PM`：`Satisfied` | `Caveats` | `Not done` | `—`（尚无 PM 评审）

CUJ **完全完成**的条件：Impl=`merged` AND QA=`PASS` AND PM=`Satisfied`。当前仅 prd-003 CUJ-1 满足完全完成；prd-005 全部 5 个 CUJ 经 iter3 + retry 1 + retry 2 三轮 QA 后判定为 PASS（所有 MEDIUM+ bug 已闭环），等待首次 PM 评审。

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
| `UploadedImage`（schemas_intake） | `session_id, image_id, role(assembly\|produce), filename, size_bytes` | 单张上传截图元数据，role 来自启发式分类 |
| `RecognizeRequest`（schemas_intake） | `session_id, product_base_name, image_ids[]` | LLM 识别请求载荷 |
| `MergeRequest`（schemas_intake） | `session_id, product_base_name, components[], plates[], variants[]` | catalog.yaml 5 阶段合并请求 |
| `IntakeError` | `error_kind, message, raw_preview?` | LLM/merge 失败统一错误，error_kind ∈ {http_401, http_5xx, timeout, parse_failed, conflict, write_failed, yaml_invalid, rollback, network} |

## 数据流

```
catalog.yaml
    │ load_catalog()（启动 lifespan / POST /api/catalog/reload / merge 阶段 ④）
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
    ├── 产品录入 POST /api/intake/upload                  │
    │        └── data/intake_tmp/<session_id>/*.png      │
    │              + 启发式分类 (assembly|produce)        │
    │                                                   │
    ├── LLM 识别 POST /api/intake/recognize               │
    │        └── DeepSeek multi-image base64 单请求       │
    │              → JSON parse → 撞名检测 → 草稿        │
    │                                                   │
    ├── 合并 POST /api/intake/merge ─────────────────────│
    │        ① 撞名兜底 → ② backup → ③ append + 复读     │
    │        ④ load_catalog → ⑤ 失败回滚                  │
    │        └── DB 立即可见新组件/打印盘/产品             │
    │                                                   │
    └─────────────────────────────────────────────────▶ React SPA
                GET /api/* → 前端内存状态 → Ant Design UI
```

富余计算（`GET /api/inventory/surplus`）= 当前库存 − 全部待处理订单 BOM 折算需求，组件+颜色粒度，不折算为可组装产品数。排班算法的供给起点（`_get_initial_supply`）= 库存 + 比当前更早已排班的产出，口径与库存页不同。

## 文件结构

```
infill-intake/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 应用入口，lifespan，静态文件服务
│   │   ├── models.py            # SQLAlchemy ORM 模型（全部 13 张表）
│   │   ├── schemas.py           # Pydantic 请求/响应 schema（核心模块）
│   │   ├── schemas_intake.py    # Pydantic schema（intake 模块单独文件）
│   │   ├── database.py          # SQLite 连接与 SessionLocal
│   │   ├── routers/
│   │   │   ├── catalog.py       # GET /api/components、/api/products、POST /api/catalog/reload
│   │   │   ├── orders.py        # 订单 CRUD + POST /ship
│   │   │   ├── inventory.py     # GET/POST adjust/PUT set/GET surplus
│   │   │   ├── printers.py      # 打印机 CRUD
│   │   │   ├── config.py        # 操作窗口 upsert、换版时间 upsert、POST reset-db
│   │   │   ├── schedule.py      # 排班生成、确认、执行状态机
│   │   │   └── intake.py        # 产品录入：upload / provider-status / recognize / merge / recent-logs
│   │   └── services/
│   │       ├── catalog.py       # load_catalog()：YAML 差量同步入 DB
│   │       ├── scheduler.py     # DB 服务层 + _persist_scheduled 共享持久化辅助
│   │       ├── scheduler_core.py# 纯函数算法核心：三策略共用 schedule_greedy + additive 同步惩罚 + 常量单一源
│   │       ├── intake.py        # 会话目录、启发式分类、撞名检测、do_merge 5 阶段事务 + 回滚 + recent-logs
│   │       ├── intake_llm.py    # DeepSeek provider：多图单请求、HTTP→error_kind 映射、JSON 容错解析
│   │       └── migrate.py       # 数据库迁移辅助
│   ├── tests/
│   │   ├── test_scheduler.py    # scheduler_core 单元测试（131 测试）
│   │   └── test_intake.py       # intake 全 5 CUJ + provider + merge 事务（71 测试）
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.tsx              # 顶层路由（Layout + Sider）
│       ├── components/Layout.tsx# 左侧 Sider 七项菜单
│       ├── api/client.ts        # 全部 API 方法集中定义
│       └── pages/
│           ├── Dashboard.tsx    # 仪表盘：统计卡 + 库存/需求总览
│           ├── Products.tsx     # 产品目录：三张只读表 + 重新加载
│           ├── Orders.tsx       # 订单管理：三 Tab + 新增弹窗 + 发货
│           ├── Inventory.tsx    # 库存管理：整表行内编辑
│           ├── Schedule.tsx     # 排班中心：生成表单 + 列表 + 甘特图 + 执行 + 闹钟
│           ├── Settings.tsx     # 系统设置：打印机 + 窗口 + 换版时间 + DB 重置
│           ├── Intake.tsx       # 产品录入：父级状态机（upload/recognize/draft/color/merge）+ 提升的 Upload state
│           └── intake/
│               ├── Upload.tsx          # CUJ-1：拖拽 + 启发式分类 + 蓝橙双栏 + mini dropzone
│               ├── Recognizing.tsx     # CUJ-2：识别中 + 取消（cancelledByUserRef 防误触错误页）+ 错误态
│               ├── Draft.tsx           # CUJ-3：BOM 表 + 打印盘表 + 原图 drawer + 撞名 alert
│               ├── Color.tsx           # CUJ-4：颜色矩阵 + 11 色 popover + 多配色变体
│               ├── Preview.tsx         # CUJ-5：合并摘要 + 暗黑 YAML 预览 + 确认合并
│               ├── Success.tsx         # CUJ-5：成功页（备份路径 + 写入/重新加载耗时 + 跳产品目录）
│               ├── IntakeError.tsx     # 通用错误页（recognize / merge 两个 variant）
│               ├── colorPalette.ts     # 11 色常用色板常量
│               ├── durationFormat.ts   # 时长 mm → Xh Ym 格式化
│               └── errorMessages.ts    # error_kind → 中文文案映射
├── data/
│   ├── catalog.yaml             # 目录单一数据源（组件/打印盘/产品 BOM）
│   ├── catalog.yaml.bak.*       # merge 阶段自动产生的备份（时间戳）
│   ├── intake/                  # QA / 开发样本图片（按产品分目录）
│   └── intake_tmp/              # 上传会话临时目录（按 session_id 分子目录，定期 cleanup）
├── docs/
│   ├── prd/                     # PRD 索引与六份 PRD（含 prd-005 intake）
│   ├── design/                  # 设计文档（catalog、frontend、orders-inventory、scheduler、system）
│   ├── ux/prd-005-intake/       # 五个 CUJ 的 HTML mock 多变体
│   ├── qa-artifacts/            # QA 截图证据（按 iter + 时间戳分子目录）
│   ├── qa-report.md             # 当前 QA 终判：iter3 retry 2 PASS（prd-005 全 5 CUJ）
│   ├── pm-review.md             # 当前 PM 终判：iter1 仅评 prd-003 CUJ-1（Satisfied）
│   ├── specs.md                 # 原始详细设计规格（基准文档）
│   ├── schedule_specs.md        # 排班算法活文档（与代码同步）
│   ├── playbook.md              # 部署与开发模式运行手册
│   └── project-overview.md      # 原 STATUS.md，项目整体长篇报告
├── Dockerfile
└── docker-compose.yml
```

## 近期活动

Iter3 全部聚焦 prd-005「产品录入」的从 0 到 1 交付，时间线（自旧 → 新）：

| 提交 | 性质 | 摘要 |
|------|------|------|
| `0dd921e` docs | 新增 PRD-005 产品录入（含 5 CUJ AC + UX mock） |
| `0dd7280` feat | 后端基础设施：schemas_intake + routers/intake 骨架 + services 占位 |
| `79265a6` feat | 前端基础设施：Intake.tsx 父状态机 + 7 个子组件骨架 + 路由接入 |
| `b2d79d2` feat | CUJ-1 backend：/api/intake/upload + 启发式分类（暗色面板阈值）+ /api/intake/provider-status |
| `1f253d3` feat | CUJ-1 frontend：upload mode UI（蓝橙双栏 + 拖拽 + mini dropzone + 启发式即时分类） |
| `d529ed7` feat | CUJ-2 frontend：recognizing mode + error mode UI |
| `0f578d2` feat | CUJ-2 backend：/api/intake/recognize + DeepSeek provider（多图单请求 + HTTP→error_kind 映射）+ 撞名检测 |
| `f185117` fix | 校准启发式阈值（140）+ provider 名 case |
| `9df2280` feat | CUJ-3：草稿校对 — BOM + 打印盘 + 撞名 alert + 原图 drawer |
| `1c5cb68` feat | CUJ-4：颜色矩阵 + 多配色变体（11 色 popover + 段 1/2/3 + 汇总条 + ⎘ 复制） |
| `41be754` feat | CUJ-5 backend：/api/intake/merge 5 阶段事务（撞名→备份→append+复读→load_catalog→失败回滚）+ recent-logs |
| `062ebb3` feat | CUJ-5 frontend：合并预览 + 成功 + 失败页（含 recent-logs Modal） |
| `a7cf11f` fix | 扩展 color / colorContext schema 串联 session_id + image_id |
| `1cb61fc` test | 新增 end-to-end smoke test：upload → recognize → merge 完整链路 |
| `fdc1e19` fix | 加固 session_id / image_id 路径安全（防目录穿越）+ 对齐 recent-logs response shape |
| `1eee605` fix | **QA bug 修复**：把 Upload 子组件 state 提升到 Intake.tsx 父组件（修 HIGH state-loss）+ stepIndex 按 variant 区分 recognize/merge（修 MEDIUM 步骤指示器误高亮） |
| `558849d` fix | **QA bug 修复**：取消按钮不再触发假「连接超时」错误页（cancelledByUserRef sentinel + `.catch` 早返回） |

**QA 终判**（详见 `docs/qa-report.md`）：iter3 retry 2 PASS，全部 5 个 CUJ 经 2 轮（initial 发现 1 HIGH + 1 MEDIUM；retry 1 关闭原 bugs 但新发现 1 MEDIUM 取消误触；retry 2 关闭最后 1 MEDIUM）后无 MEDIUM+ 残余。Backend 202/202 pytest 全绿；Frontend `npm run build` 无 TS 错误。

## 已知问题与待办

**iter3 转入下轮的 LOW 问题（不阻塞）**

- AntD deprecation 警告 4 处（`Alert.message` → `title`、`Drawer.width` → `size`、`Spin.tip` → `description`、`Statistic.valueStyle` → `styles.content`）— 仅控制台噪音，无功能影响。
- `MergeStats` Pydantic schema 声明中文键，但 `services/intake.py::do_merge` 返回英文键（`components_added` 等）。当前 `/api/intake/merge` 端点未设 `response_model`，FastAPI 不强制校验，前端 `Success.tsx` 用英文键直接读，端到端可用；schema 是「死文档」。建议下轮统一对齐：要么后端改中文键，要么 schema 改英文并显式设 `response_model=MergeResponse`。
- iter3 QA 共 9 类 manual-NOT_RUN 场景（识别中三阶段灯 / Drawer 大图 / 撞名 alert / 校验红边 / ⎘ 复制变体 / 自定义新色名 dedupe / 变体名重复 / merge 失败页 UI / recent-logs Modal）— 自动化测试已覆盖核心路径，但 UI 交互未实测。iter4 建议补 Playwright E2E。

**排班算法（prd-003）— 来自 iter1 PM Review 的优先级**

- 任务 `status=pending` 在前端渲染为「进行中」，与批次 pending 的「待开始」文案不一致（用户每日执行期最易误读）。
- 完成入库时若无匹配 `Inventory` 行，库存不增但 toast 仍报 `+N`（数字与实际不符）— 「UI 撒谎」型缺陷。
- 操作窗口默认值在 `scheduler.py:53` 与 `Settings.tsx` 两处各自硬编码，存在漂移风险。
- `changeover_minutes` 默认值 15 分散在三处（`scheduler_core.DEFAULT_CHANGEOVER_MINUTES` 已集中，但 `routers/schedule.py.start_batch` 与前端 `Schedule.tsx.changeoverMin` 仍内联），无单一常量。
- 跨夜批次时间解析（>24:00）与闹钟收菜换算可能偏差。
- prd-003 CUJ-1 AC4（指定产品过滤的清除按钮 + 提示）NOT_RUN，待下次 manual walk 补测。

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

**下一步建议**

- prd-005 已工程闭环（QA PASS），等待首次 PM Review 给出 5 个 CUJ 的产品判读。若 PM 判 Satisfied，prd-005 可视为完结。
- prd-003 CUJ-2/3/4/5 与 prd-000/001/002/004 全部 CUJ 仍待首次 PM Review（仅 prd-003 CUJ-1 已完成判读）。
