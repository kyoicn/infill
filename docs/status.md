# 项目状态

> 自动生成的项目状态摘要。
> 最后更新：2026-06-23 00:06:15 (UTC+8)

## 概述

本项目是面向个人 3D 打印小作坊的生产管理系统，覆盖产品目录、订单管理、组件库存、打印机排班、系统配置、产品录入、自动导入订单、打印机状态监测八大模块。核心价值是「晚间盘点 10 分钟，自动生成第二天可直接执行的多打印机排班表」。Iter5 刚刚交付 prd-007「打印机状态与每日利用率监测」全 2 个 CUJ — 引入 Bambu Lab 局域网 MQTT 监测链路（DB schema 演进 + 守护进程 + Broadcaster + Sampler + 利用率纯函数 + REST snapshot + WebSocket 增量 + 状态页 4 卡片 + 24h 时间轴 bar + Settings 编辑弹窗）。QA Retry 1 PASS（首轮 2 HIGH / 2 MEDIUM / 3 LOW 全闭环），CUJ-2 含 3 条 AC 因 MVP 范围排除真打印机访问而 WAIVED，等待用真硬件做接受测试。等待 dev-cycle Phase 5 PM Review。

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端框架 | React | 19.2.4 |
| 前端 UI | Ant Design (antd) | 6.3.4 |
| 前端路由 | react-router-dom | 7.13.2 |
| 前端图标 | @ant-design/icons | 6.1.1 |
| 前端构建 | Vite + TypeScript | 8.0.1 / 5.9.3 |
| WebUSB ADB | @yume-chan/adb + adb-daemon-webusb | 2.6.0 / 2.3.2 |
| 浏览器扩展 | Chrome Manifest V3 (MV3) | — |
| 后端框架 | FastAPI | 0.115.12 |
| 后端服务器 | Uvicorn | 0.34.2 |
| ORM | SQLAlchemy | 2.0.40 |
| 数据校验 | Pydantic | 2.11.1 |
| 目录解析 | PyYAML | 6.0.3 |
| 图像处理 | Pillow | ≥10.0,<12.0 |
| HTTP 客户端（LLM） | httpx | ≥0.27 |
| 文件上传 | python-multipart | ≥0.0.9 |
| **MQTT 客户端**（prd-007 新加） | **paho-mqtt** | **≥2.1.0,<3.0** |
| 数据库 | SQLite（`backend/data.db`） | — |
| LLM Provider | DeepSeek（multimodal chat completions） | — |
| ADB 客户端 | Android Platform Tools `adb` 子进程 + WebUSB | — |
| 容器化 | Docker + docker-compose | — |

## 架构

单体全栈应用，前后端分离但打包在同一 Docker 镜像中；Chrome 扩展独立分发（zip 由后端 `/static/extensions/` 静态托管）：

- **后端**（`backend/app/`）：FastAPI 应用，`main.py` 注册全部路由（catalog / orders / inventory / printers / config / schedule / intake / auto_import / **printer_status**），`lifespan` 启动时：① `auto_migrate(engine)` 自动 ALTER 旧库（含 prd-007 Printer 三凭证列） + `Base.metadata.create_all()` 建新表（含 `printer_status_sample`）；② 从 `data/catalog.yaml` 差量同步目录入 SQLite；③ 调 `ensure_order_auto_import_schema_exists` 跑 partial unique index 迁移 + 挂载 `/static/extensions`；④ **新**：实例化 `Broadcaster / Sampler / MqttDaemon` 写 `app.state.printer_status_*`，调 `daemon.startup()` 为每台三凭证齐全打印机起 paho-mqtt client + 后台心跳 / 离线检测 task；进程退出反向清理。
- **核心服务分层**：
  - 排班算法：`scheduler_core.py`（纯函数算法核心）+ `scheduler.py`（DB 服务层 + `_persist_scheduled()` 共享持久化辅助）。
  - 产品录入：`services/intake.py`（会话/文件管理、启发式分类、撞名检测、5 阶段事务 merge + 回滚）+ `services/intake_llm.py`（DeepSeek `chat_completion()`，被 auto_import 复用）。
  - 自动导入：`services/auto_import_*`（adb_client / sku_match / xianyu_parser 等） + `routers/auto_import.py` 13 端点 + `-redoN` override + diagnostics。
  - **打印机状态（prd-007 新增）**：`services/printer_mqtt_daemon.py`（每机一个 paho-mqtt VERSION2 client + reconcile_one / unsubscribe_one 增量同步）+ `services/printer_status_sampler.py`（30s 心跳兜底 + 90s 离线检测 + 进程内 `Broadcaster` set + per-client asyncio.Queue maxsize=100 drop-oldest fanout）+ `services/printer_utilization.py`（纯函数：samples → working_minutes + timeline 段；跨日延展、相邻同色合并、1440 截断）+ `routers/printer_status.py`（`GET /api/printers/status/snapshot` + `WS /api/ws/printers/status`）。
- **前端**（`frontend/src/`）：React SPA，`App.tsx` + `components/Layout.tsx` 配置路由，**九条路由**对应九个页面组件（新增 `/printers/status`），所有接口调用经 `api/client.ts` 集中管理（已扩 `api.getPrinterStatusSnapshot` + `Printer*` 类型）。产品录入页 `pages/Intake.tsx` 是父级状态机（5 个 mode）；自动导入父容器 `pages/AutoImport.tsx`；**打印机状态新增**`pages/PrinterStatus.tsx`（4 卡片网格 + mount 立即 snapshot + WS hook + 重连后自动补齐 + 顶部三态指示）+ `pages/printer_status/`（PrinterCard / Timeline24h / usePrinterStatusWS / constants）+ `pages/EditPrinterModal.tsx`（独立四字段弹窗，复用于 Settings 打印机管理）。
- **浏览器扩展**（`extension/`）：Chrome MV3 scaffold（manifest + background SW + content_xhs），由 `scripts/build-extension.sh` 打包 zip 自动镜像到 `backend/static/extensions/infill-xhs-scraper-v0.1.0.zip`。
- **数据流**：
  - 老链路：`catalog.yaml` ⇄ `load_catalog()` ⇄ SQLite ⇄ FastAPI REST ⇄ React 状态 → UI；订单发货 / 排班任务完成是库存增减的两个唯一入口；产品录入 merge 是写 catalog.yaml 唯一入口；自动导入产生 `Order(source_platform, external_order_id, auto_import_batch_id, llm_confidence)` 经 partial unique index 防重，单事务批量 insert 失败整批回滚。
  - **新增（prd-007）**：打印机 → MQTT（TLS 8883 / device/{serial}/report） → 守护进程 `on_message` → Sampler 写 `printer_status_sample` 表 + 同步 publish 到 Broadcaster set → 全部 WS 客户端就地 patch 卡片；前端 mount 时**先**调 `GET snapshot` 拉首次内容 → **再**打开 WS 接增量 → 断线指数退避（1/2/4/8/16/30s 上限）重连成功后再调一次 snapshot 补齐。
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
| **CUJ-1：配置打印机网络凭证** | **prd-007** | **P0** | **merged** | **PASS** | **—** |
| **CUJ-2：查看打印机状态页** | **prd-007** | **P0** | **in progress（QA 大部 PASS；3 条 AC WAIVED — 需真打印机做最终验收）** | **PASS** | **—** |

**列值说明：**
- `Impl`：`not started`（无代码）| `in progress`（部分代码 / 部分 AC 待真硬件接受测试）| `merged`（代码已存在并可构建且 QA 通路完整）
- `QA`：`PASS` | `FAIL` | `BLOCKED` | `NOT_RUN` | `WAIVED` | `—`（尚无 QA 运行）
- `PM`：`Satisfied` | `Caveats` | `Not done` | `—`（尚无 PM 评审）

CUJ **完全完成**的条件：Impl=`merged` AND QA=`PASS` AND PM=`Satisfied`。

**关于 prd-007 CUJ-2 标记说明**：代码全部已落地、CUJ-2 在 dev 模式下走通 5 场景（A/B/C/D/E）+ 10 条可手测 AC 全 PASS；**但 3 条 AC 受限于本轮 scope（不真起 MQTT 连接）**而记为 WAIVED，QA report 与 loop-state.md 一致写明等真打印机做最终接受测试：
- **AC 8**「1 秒内徽章变『打印中』」 — WS broadcaster→client 链路已被 E2E 间接覆盖（`test_ws_state_change_event_flows_broadcaster_to_client`），但 MQTT→后端→WS 全程时延需真硬件复现。
- **AC 11**「access_code 改回 → 该机徽章 ≤30 秒回真实状态」 — 依赖真打印机。
- **Edge case「WS 重连屡次失败 → 红色"实时连接断开"」** — 触发条件耗时长（90s+ 累计退避），代码路径已实现并被单测间接验证；可拆专项手测。

## 核心数据类型

| 类型 | 关键字段 | 用途 |
|------|----------|------|
| `Component` | `id, name, description, colors(JSON)` | 打印组件台账，目录来源 |
| `PrintConfig` | `id, plate_name, component_id, quantity, duration_minutes` | 打印盘配置 |
| `Product` | `id, name, description` | 销售产品 |
| `ProductComponent` | `product_id, component_id, color, quantity` | 产品 BOM 明细 |
| `Inventory` | `id, component_id, color, quantity` | 组件+颜色级库存 |
| `Order` | `id, created_at, status, shipped_at, source_platform, external_order_id, auto_import_batch_id, llm_confidence` | 订单头，status ∈ {pending, shipped} |
| `OrderItem` | `id, order_id, product_id, quantity` | 订单明细行 |
| **`Printer`**（prd-007 扩展） | `id, name, ip, serial, access_code` | 打印机台账；**新增三个 nullable 凭证列**：`ip(String 64)` / `serial(String 32)` / `access_code(String 16)`；任一为 NULL 即「未配置」，不订阅 MQTT |
| **`PrinterStatusSample`**（prd-007 新增） | `id, printer_id(FK CASCADE), ts(indexed), state` | 打印机状态采样行；state ∈ {running, pause, idle, offline}；复合索引 `(printer_id, ts)`；删 Printer 时 FK 级联清空 |
| `ScheduleConfig` | `id, day_of_week, windows(JSON)` | 每星期几的操作时间窗口 |
| `SystemConfig` | `id, key, value` | 通用 KV 配置（`changeover_minutes` + `xianyu_adb_config`(JSON)） |
| `PrintPlan` / `PrintBatch` / `PrintTask` | — | 排班表头 / 批次 / 单台机任务 |
| `UploadedImage` / `RecognizeRequest` / `MergeRequest`（schemas_intake） | — | 产品录入相关 |
| `IntakeError` | `error_kind, message, raw_preview?` | LLM/merge 失败统一错误 |
| `ScanRequest` / `PreviewBatch` / `CommitRequest`（auto_import） | — | 自动导入批量载荷 |
| `XianyuAdbConfig` / `Diagnostic` | — | ADB endpoint 配置 / 4 项实时检查 |
| **`PrinterUpdate`**（prd-007 新增 Pydantic） | `name?, ip?, serial?, access_code?` 全 Optional | `PUT /api/printers/{id}` 切到此 schema；`model_dump(exclude_unset=True)` 实现「未传字段保留旧值」语义（access_code 不传 = 不动） |
| **`PrinterOut`**（prd-007 扩展） | 追加 `ip / serial / access_code_masked` | `access_code_masked` 形如 `****1234`（前位掩码 + 末 4 位明文）；原值永不出现在 API 响应或日志 |
| **`PrinterStatusOut`**（prd-007 新增） | `printer_id, name, state, today_working_minutes, today_total_minutes=1440, last_state_change_ts?, timeline[]` | `state` ∈ {running, pause, idle, offline, unconfigured}；`unconfigured` 仅在响应里出现、不落 sample |
| **`TimelineSegment`**（prd-007 新增） | `start_minute(0~1440), end_minute(0~1440), state` | 24h 时间轴分段；相邻同色已合并，前端 DOM 节点数最小化 |
| **`PrinterStatusEvent`**（prd-007 新增 WS payload） | `type="state_change", printer_id, state, ts` | server→client WS 增量；仅状态实际变化时发，心跳样本不广播 |

## 数据流

```
catalog.yaml
    │ load_catalog()（启动 lifespan / POST /api/catalog/reload / merge 阶段 ④）
    ▼
SQLite: Component / PrintConfig / Product / ProductComponent / Inventory（初始 quantity=0）
    │
    ├── 订单录入 / 发货 / 排班生成 / 任务完成（老链路同前几轮，略）
    │
    ├── 产品录入 → upload → recognize（LLM）→ merge（5 阶段事务 + 备份回滚）
    │
    ├── 自动导入 → 小红书扩展 / 闲鱼 ADB 截屏 → LLM SKU 匹配 → 单事务 commit
    │
    └── 打印机状态（prd-007 新增）─────────────────────────────────────────
            ① lifespan 启动：拉三凭证齐全的 Printer 行 → 每机 paho-mqtt 2.x
               VERSION2 client → connect_async(IP, 8883, TLS insecure)
               → subscribe device/{serial}/report → loop_start 后台线程
            ② on_message：解析 print.gcode_state → _normalize_gcode_state
               → RUNNING/PAUSE→running/pause；IDLE/PREPARE/FINISH/FAILED→idle
               → Sampler.on_event(printer_id, state, ts)
            ③ Sampler 内存表：last_event_ts / last_state per printer
               - 状态变化 → 写 sample + Broadcaster.publish(state_change event)
               - 同状态 30s 心跳兜底 → 仅写 sample，不广播
               - asyncio.create_task 心跳 loop：每 30s 扫一次；>90s 无推送
                 → 写 offline sample + 广播 offline
            ④ Broadcaster: set[asyncio.Queue maxsize=100]，慢消费者 drop-oldest
            ⑤ WS /api/ws/printers/status: 双协程 (_send_loop / _recv_loop)
               server 端 25s 心跳 ping 防代理超时
            ⑥ GET /api/printers/status/snapshot:
               - 对每台 Printer：查今日 00:00 ~ now 的 sample + 一条之前的
                 baseline → compute_today_snapshot(samples, now, today_start)
               - 输出 today_working_minutes(0~1440 截断) + timeline 段列表
               - 三凭证任一为空 → state="unconfigured" 跳过算法
            ⑦ PUT /api/printers/{id} 走 PrinterUpdate(Optional, exclude_unset)
               → db.commit() → reconcile_one(printer_id)
                 → 旧 client loop_stop+disconnect → 新建（如三凭证齐）
            ⑧ DELETE /api/printers/{id}: unsubscribe_one(printer_id) 先 try/except 容错
               （daemon 异常不阻止 DB 删行）→ db.delete → FK CASCADE 清 sample
            ⑨ 前端 PrinterStatus.tsx mount:
               a. 立刻 loadSnapshot() 渲染 4 卡片
               b. 打开 WS（指数退避 1/2/4/8/16/30s 上限）
               c. WS onopen + onReconnect → 再 loadSnapshot 补齐
               d. on state_change event → 就地 patch 卡片徽章 + 时间轴末段
            ⑩ Vite dev proxy /api 配 ws: true + changeOrigin（5173→8765）
```

## 文件结构

```
infill-intake/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口；lifespan 启动 MqttDaemon/Sampler/Broadcaster
│   │   ├── models.py            # ORM；Printer +3 凭证列；新增 PrinterStatusSample
│   │   ├── schemas.py           # Pydantic 核心；PrinterOut 扩 access_code_masked；新增 PrinterUpdate
│   │   ├── schemas_intake.py    # intake 模块 schema
│   │   ├── schemas_auto_import.py # auto_import 模块 schema
│   │   ├── schemas_printer_status.py # prd-007 新增：PrinterStatusOut / TimelineSegment / PrinterStatusEvent
│   │   ├── schemas_catalog_edit.py
│   │   ├── database.py          # SQLite 连接 + auto_migrate 自动加列
│   │   ├── routers/
│   │   │   ├── catalog.py / orders.py / inventory.py / printers.py / config.py / schedule.py / intake.py / auto_import.py
│   │   │   └── printer_status.py # prd-007 新增：GET snapshot + WS endpoint
│   │   └── services/
│   │       ├── catalog.py / scheduler.py / scheduler_core.py
│   │       ├── intake.py / intake_llm.py
│   │       ├── adb_client.py / auto_import.py / auto_import_llm.py
│   │       ├── catalog_edit.py / migrate.py
│   │       ├── printer_mqtt_daemon.py    # prd-007 新增：MqttDaemon + reconcile_one / unsubscribe_one
│   │       ├── printer_status_sampler.py # prd-007 新增：Sampler 心跳 + 离线检测 + Broadcaster fanout
│   │       └── printer_utilization.py    # prd-007 新增：纯函数 samples→timeline+working_minutes
│   ├── tests/
│   │   ├── test_scheduler.py / test_intake.py / test_auto_import.py（前几轮）
│   │   ├── test_printer_status_schema.py / test_printer_status_schemas.py
│   │   ├── test_printer_mqtt_daemon.py / test_printer_status_sampler.py / test_printer_utilization.py
│   │   ├── test_printer_status_router.py / test_printers_router_credentials.py
│   │   └── test_printer_status_e2e.py    # QA Retry 1 加：凭证生命周期 / 利用率 / WS 推送 3 个 E2E
│   └── requirements.txt（+ paho-mqtt>=2.1.0,<3.0）
├── extension/                   # 小红书千帆 MV3 扩展
├── frontend/
│   ├── vite.config.ts           # dev proxy /api → 8765 + ws: true + changeOrigin（QA Retry 1 修）
│   └── src/
│       ├── App.tsx              # 路由表新增 /printers/status
│       ├── components/Layout.tsx# 菜单新增「打印机状态」入口（Dashboard 与 Settings 之间）
│       ├── api/
│       │   ├── client.ts        # 新增 PrinterStatusSnapshot / PrinterStatusEvent / PrinterUpdateBody 等 5 类型 + api.getPrinterStatusSnapshot
│       │   └── extension.ts     # chrome.runtime.sendMessage 封装
│       └── pages/
│           ├── Dashboard / Products / Orders / Inventory / Schedule / Settings / Intake / AutoImport / AutoImportSettings
│           ├── EditPrinterModal.tsx       # prd-007 新增：四字段弹窗（名称 + IP + Serial + 访问码 password）
│           ├── PrinterStatus.tsx          # prd-007 新增：主页（mount snapshot + WS + 三态指示 + Empty 重试）
│           ├── printer_status/
│           │   ├── PrinterCard.tsx        # 单卡片：徽章 + 工时 + 利用率 + Timeline24h
│           │   ├── Timeline24h.tsx        # 24h DOM 分段 bar + 「现在」竖线
│           │   ├── usePrinterStatusWS.ts  # 裸 WebSocket + 指数退避 (1/2/4/8/16/30s)
│           │   └── constants.ts           # STATE_COLORS / STATE_LABELS 单点定义
│           ├── intake/                    # 5 个 CUJ 子组件
│           ├── auto_import/               # 自动导入 8 子组件
│           └── products/
├── data/
│   ├── catalog.yaml             # 目录单一数据源
│   ├── catalog.yaml.bak.*       # merge 阶段自动备份
│   └── intake_tmp/              # 上传会话临时目录
├── scripts/
│   └── build-extension.sh
├── docs/
│   ├── prd/                     # 8 份 PRD（含 prd-007）
│   ├── design/                  # 设计文档（新增 design-printer-status.md）
│   ├── qa-artifacts/            # QA 截图证据（含 iter5-23-32-57 / iter5-23-53-35）
│   ├── qa-report.md             # 当前 QA 终判：iter5 prd-007 Retry 1 PASS
│   ├── pm-review.md             # 历史 PM 终判（最新：iter4 prd-006 — prd-007 PM 待启动）
│   ├── loop-state.md / tasks.md / status.md
│   ├── specs.md / schedule_specs.md / playbook.md / project-overview.md
├── Dockerfile
└── docker-compose.yml
```

## 近期活动

Iter5 全部聚焦 prd-007「打印机状态与每日利用率监测」从 0 到 1 交付，按 Group 4 组共 10 task 分次合入 + 一轮 QA 回归修复，时间线（自旧 → 新）：

| 提交 | Group | 摘要 |
|------|-------|------|
| `9b82211` docs | — | prd-007 PRD 落定（2 CUJ × 数据模型 × API 约定 × 设计决策）|
| `b8a330d` docs | — | design-printer-status.md（DB schema / MQTT daemon / Broadcaster / utilization / 前端集成）|
| `9b73dc6` docs | — | tasks.md 4 组 / 10 task 分解（TL 硬性约束 10 条编入）|
| `e1834e5` feat | G1 T1.1 | `Printer` 加 ip/serial/access_code 三 nullable 列 + 新表 `PrinterStatusSample`（FK CASCADE + 复合索引）+ auto_migrate 列集补三个新列 |
| `1d5dd95` feat | G1 T1.2 | requirements paho-mqtt 2.x + `PrinterUpdate(Optional + extra=forbid)` + `schemas_printer_status.py`（Snapshot / Timeline / Event）+ access_code 掩码 validator |
| `e238b7f` feat | G2 T2.1 | `printer_mqtt_daemon.py` MqttDaemon 主体（paho-mqtt VERSION2 callback / TLS insecure / bblp + access_code / connect_async + loop_start / reconcile_one 旧 disconnect → 新连）+ 日志严格 masked code |
| `525fe47` feat | G2 T2.2 | `printer_status_sampler.py` — Broadcaster fanout（set + asyncio.Queue 100 drop-oldest）+ Sampler 30s 心跳兜底 + 90s 离线检测 + 同状态去重广播 |
| `d49c14b` feat | G2 T2.3 | `printer_utilization.py` 纯函数 compute_today_snapshot（跨午夜延展、相邻同色合并、1440 截断、空样本默认 offline）|
| `dab859f` feat | G3 T3.1 | `routers/printer_status.py` — `GET /api/printers/status/snapshot`（逐机查 sample + utilization）+ `WS /api/ws/printers/status`（双协程 send/recv + 25s server ping）+ lifespan 注入 daemon/sampler/broadcaster |
| `786b1d6` feat | G3 T3.2 | `PUT /api/printers/{id}` 切到 `PrinterUpdate` + `exclude_unset` 部分更新 + commit 之后调 reconcile_one；DELETE 之前 unsubscribe_one |
| `1dc6147` feat | G4 T4.1 | `client.ts` 加 5 类型（Printer / PrinterUpdateBody / PrinterStatusSnapshot / TimelineSegment / PrinterStatusEvent）+ `getPrinterStatusSnapshot()` + `updatePrinter` partial 签名 |
| `83bbea4` feat | G4 T4.2 | Settings 打印机管理表加「编辑」按钮 + `EditPrinterModal`（四字段 + Input.Password + 「未配置」徽标）+ access_code「不传即保留」前端实现 |
| `f58a639` feat | G4 T4.3 | `PrinterStatus.tsx` 主页 + 4 卡片 / 24h DOM bar + `usePrinterStatusWS` hook 指数退避 + 路由 + 菜单 + 三态指示 |
| `f8e1f60` chore | G4 | 占位类型清理 — `printer_status/types.ts` 删除，import 切到 `../api/client` |
| `ea725b9` chore | — | gitignore 根级 `data.db`（root cwd 兜底）|
| `c469c8f` chore | TL fix | TL code review fix — DELETE `unsubscribe_one` 加 try/except 容错（daemon 异常不阻止 DB 删行）+ `EditPrinterModal` catch 块改用类型守卫去 `e: any` |
| `0b4436e`-ish QA init | QA | iter5 prd-007 QA gate 初判 FAIL（CUJ-2 2 HIGH / 2 MEDIUM / 3 LOW；CUJ-1 PASS） |
| `928020f` fix | Retry1 | **HIGH×2 修**：PrinterStatus.tsx mount `useEffect → loadSnapshot()` 独立调（不再绑死 onReconnect）+ vite.config.ts `proxy['/api']` 切 `{ target: 'http://localhost:8765', ws: true, changeOrigin: true }`；**LOW×3 修**：Space.direction→orientation / Modal.destroyOnClose→destroyOnHidden / Spin.tip 重写为 `<Spin/>` + 文字 div |
| `998db4a` merge | Retry1 | Merge QA Retry 1 fix（CUJ-2 HIGH×2 + LOW×3 全闭环；MEDIUM×2 验证阻塞解除）|
| `f72a695` test | QA | 加 3 个 prd-007 E2E：`test_credential_lifecycle_drives_snapshot_state` / `test_samples_drive_utilization_today_working_minutes` / `test_ws_state_change_event_flows_broadcaster_to_client` |
| `438c4ef` docs | QA | iter5 prd-007 QA gate PASS（Retry 1）— qa-report.md 终判 + loop-state.md 升级 |

**QA 终判**（详见 `docs/qa-report.md`）：iter5 Retry 1 **PASS**。CUJ-1 两轮 PASS（10/10 AC 全通过）；CUJ-2 五场景 A/B/C/D/E 两轮一致 PASS，10/13 AC 直接 PASS + 3 条 AC（#8、#11、edge「WS 重连屡次失败」）WAIVED 等真打印机硬件做接受测试。Backend 416 passed / 2 skipped（baseline 344 → +66 prd-007 单测 + 3 QA Retry 1 E2E = 416）；Frontend `npm run build` 通过；console 0 antd 弃用 warning（3 条全清）。

## 已知问题与待办

### Iter5（prd-007）转入下轮

**3 条 WAIVED AC — 需真打印机硬件接受测试**（不阻塞 QA PASS，但 PM 评审需明确 caveat）

- CUJ-2 AC #8「1 秒内徽章变『打印中』」 — WS 链路 E2E 已覆盖，但 MQTT→后端→WS 全程时延需真硬件复现。
- CUJ-2 AC #11「access_code 改回 → ≤30 秒切回真实状态」 — 「凭证错→离线」场景 B 已用 fake IP 验证，但「凭证改回→恢复」需真打印机。
- CUJ-2 Edge case「WS 重连屡次失败 → 红色『实时连接断开』」 — 触发条件耗时长（90s+ 累计退避），代码路径在 `usePrinterStatusWS.ts:67` 已实现并间接单测覆盖；可拆专项手测 / RTL mock。

**TL Phase 3.6 review carry-over（不阻塞 QA / 性能 / 部署）**

- `[P2]` snapshot 端点 **N+1 查询**：对每台 Printer 各跑一次 SQL 拉今天 sample；4 台机数据量小，但符合 prd-006 的同类 carry-over 模式 — `backend/app/routers/printer_status.py`
- `[P2]` **lifespan race window**：守护进程 startup 后、第一条 MQTT 推送到达前的 ≤3 秒窗口内，前端 snapshot 看到 offline；PRD Edge Case 已 ack 为「首次连接窗口假象」，但 race 期间若用户恰好按重试可能引发误读 — `backend/app/services/printer_mqtt_daemon.py` + `printer_status_sampler.py`
- `[LOW]` MQTT TLS `verify_mode = CERT_NONE` + `tls_insecure_set(True)` — Bambu 自签证书，社区标准做法，局域网中间人风险被设计明确接受（design §7.1）
- `[LOW]` 守护进程**无 watchdog 自动重启** — 异步任务崩溃后只能靠 backend 重启恢复（PRD 关键约束 #1 + design §7.2 已 ack）
- `[LOW]` `access_code` **明文存 DB**（单用户本地部署的设计取舍；API 响应 + 日志 + git 均不出现原值）

### Iter4 prd-006 transferred（等待 PM Review 一并处理）

- `[LOW][BUG]` `POST /api/auto-import/xhs/probe` 仍是占位实现 — `backend/app/routers/auto_import.py:99`
- `[LOW][VISUAL_DEVIATION]` 下载扩展按钮文案缺 "(12 KB)" size 后缀 — `frontend/src/pages/auto_import/XhsTab.tsx:387`
- iter4 manual NOT_RUN：CUJ-1 5 步进度真实 Chrome 扩展验证 / CUJ-2 真 ADB + emulator + LLM key 验证 / CUJ-3 完整 scan happy path UI 视觉
- TL carry-over：scan 端点 N+1 查询、串行 LLM 调用、payload-size limits、扩展硬编码 `http://localhost:8000`、CORS `allow_origins=["*"]`

### Iter3 prd-005 transferred（不阻塞）

- AntD 5.x→6.x deprecation 警告（`Alert.message → title`、`Drawer.width → size`、`Statistic.valueStyle → styles.content`） — 控制台噪音
- `MergeStats` Pydantic schema 中英文键漂移（`/api/intake/merge` 未设 `response_model`）

### 排班算法 prd-003（iter1 PM Review 留底）

- `status=pending` 文案漂移；完成入库无匹配 Inventory 行时 toast 假报 `+N`；操作窗口默认值在 `scheduler.py:53` 与 `Settings.tsx` 两处硬编码；`changeover_minutes` 默认 15 散在三处；跨夜批次 >24:00 解析与收菜闹钟换算可能偏差；CUJ-1 AC4 NOT_RUN

### 库存管理 prd-002 / 订单 prd-001 / 目录 prd-000 / 系统配置 prd-004

延续 iter3 status.md 的列表，未在本轮触碰：富余未折算为可组装产品数 / 富余口径不含已排班产出 / `Inventory(component_id, color)` 无 DB 唯一约束 / 批量创建订单非原子 / 删已发货订单不回补库存 / 发货失败报组件 id 不报名称 / `shipped_at` 落库无列展示 / GET 接口无 `.catch()` / 改名按名称匹配等同删旧建新 / `load_catalog` 无单测 / 重置数据库不 seed 默认窗口 / **打印机改名无 UI 入口（已由本轮 prd-007 关闭，借「编辑」按钮入口实现改名）** / 保存空窗口与「未配置」UI 文案相同但算法相反 / 操作窗口与换版时间无错误 toast 兜底

### 下一步建议

- **prd-007 等待首次 PM Review**：CUJ-1 + CUJ-2 已 `Impl=merged + QA=PASS`，需 PM 对凭证管理 UX + 状态页 4 卡片可读性 + 3 条 WAIVED AC（真打印机接受测试）给出判读；通过则升 frontmatter `active → completed`。
- **prd-006 仍待首次 PM Review**：4 CUJ 全 `Impl=merged + QA=PASS`，PM 需关注扩展未装态引导、ADB 三项诊断可读性、预览页 chips/默认勾选规则、`-redoN` 改判路径可见性。
- **prd-005 已完整闭环**（5/5 CUJ 三维满足）— frontmatter 已升 `active → completed`。
- **prd-000/001/002/003/004 全部待首次 PM Review**（仅 prd-003 CUJ-1 已完成判读）。
- **真打印机接受测试窗口**：等用户拿真 Bambu Lab 打印机时一次性跑：(a) AC 8 真 MQTT push <1s；(b) AC 11 改 access_code 凭证回正路径；(c) WS 屡次失败 90s+ 红色降级文案；(d) 跨午夜利用率归零观察。
