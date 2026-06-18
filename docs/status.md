# 项目状态

> 自动生成的项目状态摘要。
> 最后更新：2026-06-18 22:46:44 (UTC+8)

## 概述

本项目是面向个人 3D 打印小作坊的生产管理系统，覆盖产品目录、订单管理、组件库存、打印机排班、系统配置、产品录入、自动导入订单七大模块。核心价值是「晚间盘点 10 分钟，自动生成第二天可直接执行的多打印机排班表」。Iter4 刚刚交付 prd-006「自动导入订单」全部 4 个 CUJ，QA 经 retry 1 后判定 PASS（5 个 MEDIUM+ bug 全部闭环），等待首次 PM 评审。

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端框架 | React | 19.2.4 |
| 前端 UI | Ant Design (antd) | 6.3.4 |
| 前端路由 | react-router-dom | 7.13.2 |
| 前端图标 | @ant-design/icons | 6.1.1 |
| 前端构建 | Vite + TypeScript | 8.0.1 / 5.9.3 |
| 浏览器扩展 | Chrome Manifest V3 (MV3) | — |
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
| ADB 客户端 | Android Platform Tools `adb` 子进程 | — |
| 容器化 | Docker + docker-compose | — |

## 架构

单体全栈应用，前后端分离但打包在同一 Docker 镜像中；Chrome 扩展独立分发（zip 由后端 `/static/extensions/` 静态托管）：

- **后端**（`backend/app/`）：FastAPI 应用，`main.py` 注册所有路由（catalog / orders / inventory / printers / config / schedule / intake / auto_import），`lifespan` 启动时从 `data/catalog.yaml` 差量同步目录进 SQLite + 调 `ensure_order_auto_import_schema_exists` 跑 `Order` 表 4 列 + partial unique index 迁移 + 挂载 `/static/extensions`。路由层（`routers/`）薄，核心业务逻辑在 `services/`。排班算法分层：`scheduler_core.py` 是单一纯函数算法核心；`scheduler.py` 仅保留 DB 服务层与 `_persist_scheduled()` 共享持久化辅助。产品录入算法分层：`services/intake.py` 负责会话/文件管理、启发式分类、撞名检测、5 阶段事务 merge + 回滚；`services/intake_llm.py` 封装 DeepSeek provider 的 `chat_completion()`（已抽出供 auto_import 复用）。自动导入分层：`services/auto_import_*`（adb_client / sku_match / xianyu_parser 等）封装 ADB 子进程、LLM SKU 匹配、闲鱼截屏解析；`routers/auto_import.py` 13 个 endpoint 单事务 commit + `-redoN` override + diagnostics 整套。
- **前端**（`frontend/src/`）：React SPA，`App.tsx` + `components/Layout.tsx` 配置路由，八条路由对应八个页面组件，所有接口调用经 `api/client.ts` 集中管理（已扩 `api.autoImport.*`）。`extension.ts` 封装 `chrome.runtime.sendMessage` 调扩展。产品录入页 `pages/Intake.tsx` 是父级状态机（5 个 mode）。自动导入父容器 `pages/AutoImport.tsx`（mode: tabs / scanning / preview）+ `pages/auto_import/`（XhsTab / XianyuTab / ScanningProgress / ScreencapGrid / PreviewTable / SkuPicker / SuccessPanel / FailurePanel）+ 独立设置页 `pages/AutoImportSettings.tsx`。
- **浏览器扩展**（`extension/`）：Chrome MV3 scaffold（manifest + background SW + content_xhs），由 `scripts/build-extension.sh` 打包 zip 自动镜像到 `backend/static/extensions/infill-xhs-scraper-v0.1.0.zip`。
- **数据流**：`catalog.yaml` ⇄ `load_catalog()` ⇄ SQLite（目录、库存初始行）⇄ FastAPI REST API ⇄ React 前端状态 → 用户界面。订单发货和排班任务完成是库存减少/增加的两个唯一入口。产品录入 merge 是写 catalog.yaml 的唯一入口。自动导入产生 `Order(source_platform, external_order_id, auto_import_batch_id, llm_confidence)` 经 partial unique index 防重，单事务批量 insert 失败整批回滚。
- **部署**：Docker 内后端静态服务前端 `dist/` + `/static/extensions/*.zip`，数据库与 catalog 文件通过卷挂载持久化。

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
| CUJ-1：上传截图 + 自动分类 | prd-005 | P0 | merged | PASS | Satisfied |
| CUJ-2：触发 LLM 识别 | prd-005 | P0 | merged | PASS | Satisfied |
| CUJ-3：草稿校对 BOM + 打印盘 | prd-005 | P0 | merged | PASS | Satisfied |
| CUJ-4：颜色矩阵 + 多配色变体 | prd-005 | P0 | merged | PASS | Satisfied |
| CUJ-5：合并到 catalog.yaml | prd-005 | P0 | merged | PASS | Satisfied |
| CUJ-1：扫描小红书千帆订单 | prd-006 | P0 | merged | PASS | — |
| CUJ-2：扫描闲鱼订单 | prd-006 | P0 | merged | PASS | — |
| CUJ-3：预览校对 + 一键导入 | prd-006 | P0 | merged | PASS | — |
| CUJ-4：自动导入设置 | prd-006 | P1 | merged | PASS | — |

**列值说明：**
- `Impl`：`not started`（无代码）| `in progress`（部分代码）| `merged`（代码已存在并可构建）
- `QA`：`PASS` | `FAIL` | `BLOCKED` | `NOT_RUN` | `WAIVED` | `—`（尚无 QA 运行）
- `PM`：`Satisfied` | `Caveats` | `Not done` | `—`（尚无 PM 评审）

CUJ **完全完成**的条件：Impl=`merged` AND QA=`PASS` AND PM=`Satisfied`。当前 prd-003 CUJ-1 与 prd-005 全部 5 个 CUJ 满足完全完成；prd-006 全部 4 个 CUJ 经 iter4 initial（FAIL）+ retry 1（PASS）两轮 QA 后判定 PASS（5 个 MEDIUM+ bug 全部闭环），等待首次 PM 评审。

## 核心数据类型

| 类型 | 关键字段 | 用途 |
|------|----------|------|
| `Component` | `id, name, description, colors(JSON)` | 打印组件台账，目录来源 |
| `PrintConfig` | `id, plate_name, component_id, quantity, duration_minutes` | 打印盘配置，排班任务的基本单元 |
| `Product` | `id, name, description` | 销售产品，关联 BOM |
| `ProductComponent` | `product_id, component_id, color, quantity` | 产品 BOM 明细 |
| `Inventory` | `id, component_id, color, quantity` | 组件+颜色级库存，唯一性靠逻辑保证 |
| `Order` | `id, created_at, status, shipped_at, source_platform, external_order_id, auto_import_batch_id, llm_confidence` | 订单头，status ∈ {pending, shipped}；后 4 列由 prd-006 引入，partial unique index `(source_platform, external_order_id) WHERE source_platform IS NOT NULL` 防重 |
| `OrderItem` | `id, order_id, product_id, quantity` | 订单明细行 |
| `Printer` | `id, name` | 打印机台账，无唯一约束 |
| `ScheduleConfig` | `id, day_of_week, windows(JSON)` | 每星期几的操作时间窗口，DB 有唯一约束 |
| `SystemConfig` | `id, key, value` | 通用 KV 配置，当前用 `changeover_minutes` + `xianyu_adb_config`(JSON) |
| `PrintPlan` | `id, date, start_time, duration_hours, status, created_at` | 排班表头，status ∈ {draft, confirmed} |
| `PrintBatch` | `id, plan_id, start_time, batch_order, status` | 一组同时启动的任务，status ∈ {pending, started, completed} |
| `PrintTask` | `id, batch_id, printer_id, print_config_id, color, is_surplus, start_time, end_time, status` | 单台打印机的单个任务，status ∈ {pending, completed, cancelled, failed} |
| `UploadedImage`（schemas_intake） | `session_id, image_id, role(assembly\|produce), filename, size_bytes` | 单张上传截图元数据，role 来自启发式分类 |
| `RecognizeRequest`（schemas_intake） | `session_id, product_base_name, image_ids[]` | LLM 识别请求载荷 |
| `MergeRequest`（schemas_intake） | `session_id, product_base_name, components[], plates[], variants[]` | catalog.yaml 5 阶段合并请求 |
| `IntakeError` | `error_kind, message, raw_preview?` | LLM/merge 失败统一错误，error_kind ∈ {http_401, http_5xx, timeout, parse_failed, conflict, write_failed, yaml_invalid, rollback, network} |
| `ScanRequest`/`PreviewBatch`/`CommitRequest`（auto_import） | `source_platform, raw_orders[], llm_matches[], batch_id, items[], overrides[]` | 自动导入 scan / preview / commit 单事务批量载荷 |
| `XianyuAdbConfig` | `device_type(mumu\|bluestacks\|leidian\|usb), pc_ip, port` | ADB endpoint 配置，存 SystemConfig JSON |
| `Diagnostic` | `name, ok, hint?` | ADB probe / test-adb 的 4 项实时检查（adb_installed / ping / tcp_port / device_state）|

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
    ├── 自动导入 — 小红书 ────────────────────────────────│
    │        ① 前端 chrome.runtime.sendMessage(ping)      │
    │        ② POST /xhs/extension-status + /xhs/probe    │
    │        ③ content_xhs 抓 DOM → POST /xhs/scan        │
    │        ④ 后端 LLM 串行 match_listing_to_sku         │
    │        ⑤ 返回 PreviewBatch（stateless 前端态）       │
    │                                                   │
    ├── 自动导入 — 闲鱼 ──────────────────────────────────│
    │        ① POST /xianyu/probe + diagnostics 4 项检查 │
    │        ② POST /xianyu/screencap → ADB exec-out      │
    │              screencap -p → png 字节 → 异步队列     │
    │        ③ LLM 单图解析 raw_listing → PreviewBatch     │
    │        ④ 1.5s 轮询 /xianyu/scan-status              │
    │                                                   │
    ├── 自动导入 commit POST /auto-import/commit ────────│
    │        └── 单事务批量 insert Order + OrderItem      │
    │              任一 SKU 缺失整批回滚（零写入）         │
    │              重复用 -redoN 后缀绕过 unique index    │
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
│   │   ├── main.py              # FastAPI 应用入口，lifespan，静态文件服务，/static/extensions 挂载
│   │   ├── models.py            # SQLAlchemy ORM 模型（含 Order 新 4 列）
│   │   ├── schemas.py           # Pydantic 请求/响应 schema（核心模块）
│   │   ├── schemas_intake.py    # Pydantic schema（intake 模块单独文件）
│   │   ├── schemas_auto_import.py # Pydantic schema（auto_import 模块单独文件）
│   │   ├── database.py          # SQLite 连接与 SessionLocal
│   │   ├── migrations.py        # ensure_order_auto_import_schema_exists（partial unique index）
│   │   ├── routers/
│   │   │   ├── catalog.py       # GET /api/components、/api/products、POST /api/catalog/reload
│   │   │   ├── orders.py        # 订单 CRUD + POST /ship
│   │   │   ├── inventory.py     # GET/POST adjust/PUT set/GET surplus
│   │   │   ├── printers.py      # 打印机 CRUD
│   │   │   ├── config.py        # 操作窗口 upsert、换版时间 upsert、POST reset-db
│   │   │   ├── schedule.py      # 排班生成、确认、执行状态机
│   │   │   ├── intake.py        # 产品录入：upload / provider-status / recognize / merge / recent-logs
│   │   │   └── auto_import.py   # 自动导入 13 endpoints：xhs/* + xianyu/* + sku-search + commit
│   │   └── services/
│   │       ├── catalog.py       # load_catalog()：YAML 差量同步入 DB
│   │       ├── scheduler.py     # DB 服务层 + _persist_scheduled 共享持久化辅助
│   │       ├── scheduler_core.py# 纯函数算法核心
│   │       ├── intake.py        # 会话目录、启发式分类、撞名检测、do_merge 5 阶段事务
│   │       ├── intake_llm.py    # DeepSeek provider：chat_completion()（被 auto_import 复用）
│   │       ├── auto_import_adb_client.py    # subprocess adb 封装 + 4 项 diagnostics
│   │       ├── auto_import_sku_match.py     # match_listing_to_sku：全量 catalog 注入 LLM
│   │       ├── auto_import_xianyu_parser.py # 单图 LLM 解析 raw_listing
│   │       └── migrate.py       # 数据库迁移辅助
│   ├── tests/
│   │   ├── test_scheduler.py    # scheduler_core 单元测试
│   │   ├── test_intake.py       # intake 全 5 CUJ + provider + merge 事务
│   │   └── test_auto_import.py  # auto_import 全 4 CUJ + E2E + retry-1 fix 测试
│   └── requirements.txt
├── extension/
│   ├── manifest.json            # Chrome MV3 配置（permissions: tabs/storage/scripting）
│   ├── background.js            # service worker，host=http://localhost:8000（硬编码 LOW 项）
│   ├── content_xhs.js           # 小红书千帆 DOM 抓取
│   └── README.md
├── frontend/
│   └── src/
│       ├── App.tsx              # 顶层路由（Layout + Sider）
│       ├── components/Layout.tsx# 左侧 Sider 八项菜单
│       ├── api/
│       │   ├── client.ts        # 全部 API 方法集中定义（含 api.autoImport.*）
│       │   └── extension.ts     # chrome.runtime.sendMessage 封装
│       └── pages/
│           ├── Dashboard.tsx    # 仪表盘：统计卡 + 库存/需求总览
│           ├── Products.tsx     # 产品目录
│           ├── Orders.tsx       # 订单管理：三 Tab + 新增 + 发货 + 入口跳转自动导入
│           ├── Inventory.tsx    # 库存管理
│           ├── Schedule.tsx     # 排班中心
│           ├── Settings.tsx     # 系统设置
│           ├── Intake.tsx       # 产品录入：父级状态机
│           ├── AutoImport.tsx   # 自动导入父容器（mode: tabs/scanning/preview）
│           ├── AutoImportSettings.tsx # 自动导入设置页
│           ├── intake/          # 5 个 CUJ 子组件 + 共享 helper
│           └── auto_import/
│               ├── XhsTab.tsx          # CUJ-1：小红书 tab + 扩展状态 + 未装态下载按钮
│               ├── ScanningProgress.tsx# CUJ-1：5 步进度条
│               ├── XianyuTab.tsx       # CUJ-2：ADB 状态 + 三项诊断（联动 diagnostics）
│               ├── ScreencapGrid.tsx   # CUJ-2：缩略图网格 + 1.5s 轮询
│               ├── PreviewTable.tsx    # CUJ-3：预览主表 + 空 batch 空态 + chips
│               ├── SkuPicker.tsx       # CUJ-3：360px 三段浮窗 + 搜索
│               ├── SuccessPanel.tsx    # CUJ-3：成功页
│               └── FailurePanel.tsx    # CUJ-3：失败页
├── data/
│   ├── catalog.yaml             # 目录单一数据源
│   ├── catalog.yaml.bak.*       # merge 阶段自动产生的备份
│   ├── intake/                  # QA / 开发样本图片
│   └── intake_tmp/              # 上传会话临时目录
├── scripts/
│   └── build-extension.sh       # 打包 zip → backend/static/extensions/
├── docs/
│   ├── prd/                     # PRD 索引与七份 PRD
│   ├── design/                  # 设计文档（含 design-auto-import.md）
│   ├── ux/prd-006-auto-import-orders/ # 4 个 CUJ 的 HTML mock
│   ├── qa-artifacts/            # QA 截图证据
│   ├── qa-report.md             # 当前 QA 终判：iter4 retry 1 PASS（prd-006 全 4 CUJ）
│   ├── pm-review.md             # 当前 PM 终判：iter3（prd-005 全 5 CUJ Satisfied）
│   ├── specs.md                 # 原始详细设计规格
│   ├── schedule_specs.md        # 排班算法活文档
│   ├── playbook.md              # 部署与开发模式运行手册
│   └── project-overview.md      # 项目整体长篇报告
├── Dockerfile
└── docker-compose.yml
```

## 近期活动

Iter4 全部聚焦 prd-006「自动导入订单」从 0 到 1 交付（小红书 Chrome 扩展 + 闲鱼 ADB 截屏双通道），按 Group 分次合入 + 一轮 QA 回归修复，时间线（自旧 → 新）：

| 提交 | Group | 摘要 |
|------|-------|------|
| `908f9f9` docs | — | 设计 + PRD 落定（4 CUJ × 11 mocks + design-auto-import.md）|
| `ca8e847` refactor | G1 | LLM provider 抽 `chat_completion()` 给 auto-import 复用（intake 71 测试零回归）|
| `93227fd` feat | G1 | `Order` schema 4 列 + partial unique index helper `ensure_order_auto_import_schema_exists` |
| `3dfc17d` feat | G1 | Chrome MV3 扩展 scaffold（manifest + background SW + content_xhs + build 脚本）|
| `374587b` feat | G2 | ADB client 子进程封装 + 4 项诊断 + SystemConfig CRUD（CUJ-4 后端）|
| `1bc1e68` feat | G2 | LLM SKU 匹配（`match_listing_to_sku` 全量 catalog 注入）+ 闲鱼截屏解析 + SKU 搜索 |
| `9404e9f` feat | G2 | `routers/auto_import.py` 13 endpoints + 单事务 commit + `-redoN` override 后缀 |
| `cbb8bf2` feat | G3 | frontend `api.autoImport.*` + `extension.ts` chrome.runtime 封装 + `@types/chrome` |
| `b211c94` feat | G3 | CUJ-4 AutoImportSettings 页（双卡 + ADB 测试 + 扩展状态）+ entry buttons |
| `0040d6d` refactor | G3 | 把 AutoImportSettings 的 local stub 替换为正式的 `api.autoImport` + `extension.ts` |
| `90d20a6` feat | G4 | CUJ-1：AutoImport 父容器 + XhsTab + ScanningProgress（含 6 个 sibling stub）|
| `7dd8ead` feat | G4 | CUJ-2：XianyuTab + ScreencapGrid + 1.5s 轮询 |
| `19ae738` feat | G4 | CUJ-3：PreviewTable + SkuPicker + Success/Failure 面板 |
| `058aa88` feat | G5 | `main.py.lifespan` 串入 ensure helper + router 注册 + `/static/extensions` 挂载 |
| `679d939` chore | G5 | build-extension.sh 自动镜像到 `backend/static/extensions/` + README + checklist 全勾 |
| `c73409f` docs | — | mark prd-006 CUJ-1/2/3/4 as merged + iter4 activity log |
| `3882d5e` test | — | E2E flows — scan→commit happy path + partial unique index + xianyu screencap |
| `973fae5` docs | — | tl architecture review + planner task split for prd-006 |
| `258051d` fix | TL | unwrap `match_listing_to_sku` tuple in router scan loop（TL review 修复）|
| `0b4436e` test+docs | QA | iter4 QA gap 测试 + qa-report + fix-tasks + loop-state（initial verdict FAIL）|
| `cce7b19` fix | Retry1 | **QA bug 修复**：XhsTab 下载扩展按钮（MEDIUM × 2 closed）+ PreviewTable 空 batch 空态（MEDIUM × 1 closed）|
| `1b5f35f` fix | Retry1 | **QA bug 修复**：`adb_connected` 改用 `diagnostics[device_state].ok`（HIGH × 2 closed）+ 前端 XianyuTab `allDiagsOk` 防御 + 新增 `TestQAFixAdbConnectedTruth` 4 测 |
| `5d10710` merge | Retry1 | Merge worktree-agent 闭环 retry 1 fix（QA verdict FAIL → PASS）|

**QA 终判**（详见 `docs/qa-report.md`）：iter4 retry 1 **PASS**，全部 4 个 CUJ 经 2 轮（initial 发现 2 HIGH + 3 MEDIUM；retry 1 全部闭环并新增 4 个回归测试）后无 MEDIUM+ 残余。Backend 344 passed / 2 skipped（baseline 340 → 344，+4 = `TestQAFixAdbConnectedTruth`）；Frontend `npx tsc -b` 通过。

## 已知问题与待办

**iter4 转入下轮的 LOW 问题（不阻塞 PASS）**

- `[LOW][BUG]` `POST /api/auto-import/xhs/probe` 仍是占位实现（永远返回 `has_xhs_tab=true`，不真探活）— 实际扩展探活由前端 `chrome.runtime.sendMessage` 完成，后端这一调用可视为「保留 hook」。`backend/app/routers/auto_import.py:99`
- `[LOW][VISUAL_DEVIATION]` 下载扩展按钮文案缺 "(12 KB)" size 后缀，与 `cuj-1-no-extension.html` mock 微差异 — `frontend/src/pages/auto_import/XhsTab.tsx:387`
- `[LOW][BUG]` AntD `Spin.tip` deprecation console warning（iter3 carry-over）— 全局 Spin 使用点
- iter4 manual NOT_RUN 覆盖空白：CUJ-1 扫描中 5 步进度 / 取消 / 错误态等需真实 Chrome 扩展环境；CUJ-2 截屏卡片渲染 / 缩略图状态 / 完成解析需真实 ADB 设备 + emulator + LLM key；CUJ-3 预览 UI 需先走完整 scan happy path；CUJ-4 边界态 + 故障跳转的视觉细节

**TL Review carry-over（性能 / 安全 / 部署，与下一个 prd 一起处理）**

- N+1 重复查询：scan 端点对每条 raw_order 都查一次 DB — `backend/app/routers/auto_import.py:160`
- 串行 LLM 调用：CUJ-1 每个 product 一次 `chat_completion()` — `backend/app/routers/auto_import.py:175`
- 无 payload-size limits — 全部 `/api/auto-import/*` 端点
- 硬编码后端 URL `http://localhost:8000` — `extension/background.js`
- CORS `allow_origins=["*"]` — `backend/app/main.py:53`

**iter3 转入的 LOW 问题（不阻塞）**

- AntD deprecation 警告 4 处（`Alert.message` → `title`、`Drawer.width` → `size`、`Spin.tip` → `description`、`Statistic.valueStyle` → `styles.content`）— 仅控制台噪音。
- `MergeStats` Pydantic schema 声明中文键，但 `services/intake.py::do_merge` 返回英文键 — `/api/intake/merge` 端点未设 `response_model`，FastAPI 不强制校验，前端用英文键直接读。

**排班算法（prd-003）— 来自 iter1 PM Review 的优先级**

- 任务 `status=pending` 在前端渲染为「进行中」，与批次 pending 的「待开始」文案不一致。
- 完成入库时若无匹配 `Inventory` 行，库存不增但 toast 仍报 `+N`。
- 操作窗口默认值在 `scheduler.py:53` 与 `Settings.tsx` 两处各自硬编码，存在漂移风险。
- `changeover_minutes` 默认值 15 分散在三处（`scheduler_core.DEFAULT_CHANGEOVER_MINUTES` 已集中，但 `routers/schedule.py.start_batch` 与前端 `Schedule.tsx.changeoverMin` 仍内联）。
- 跨夜批次时间解析（>24:00）与闹钟收菜换算可能偏差。
- prd-003 CUJ-1 AC4（指定产品过滤的清除按钮 + 提示）NOT_RUN。

**库存管理（prd-002）**

- 富余未折算为「可组装产品数」。
- 富余口径不含已排班产出，与排班总结页的口径不一致。
- `Inventory` 表 `(component_id, color)` 无 DB 唯一约束。
- 整表编辑保存（`Promise.all`）非原子。

**订单管理（prd-001）**

- 批量创建订单为 N 个独立请求，中途失败产生部分落库。
- 删除已发货订单不回补库存，无撤销发货入口。
- 发货失败报错用组件 id 而非名称。
- `shipped_at` 已落库但列表无对应列展示。

**产品目录（prd-000）**

- 三个 GET 接口无 `.catch()`。
- 改名按名称匹配，等同删旧建新。
- `load_catalog` 无单元测试。

**系统配置（prd-004）**

- 打印机改名无 UI 入口。
- 重置数据库不 seed 默认操作窗口/换版时间。
- 保存空窗口与「未配置」UI 显示相同文案但算法行为相反。
- 操作窗口和换版时间保存无错误 toast 兜底。

**下一步建议**

- **prd-006 等待首次 PM Review**：CUJ-1/2/3/4 全部 `Impl=merged` + `QA=PASS`，需 PM 对 4 个 CUJ 给出产品判读（重点关注：扩展未装态引导清晰度、ADB 三项诊断对作坊主可读性、预览页 chips/checkbox 默认勾选规则是否符合「最少点击导入」体感、`-redoN` 改判路径在 UI 上是否够显眼）。
- prd-005 已完整闭环（Impl=merged + QA=PASS + PM=Satisfied 全 5 CUJ）— 状态 active → completed。
- prd-003 CUJ-2/3/4/5 与 prd-000/001/002/004 全部 CUJ 仍待首次 PM Review（仅 prd-003 CUJ-1 已完成判读）。
