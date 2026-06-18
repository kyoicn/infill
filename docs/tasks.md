# Task Plan

Last updated: 2026-06-18 19:13:56 (UTC+8)

## Current State

prd-006「自动导入订单」处于 **not started** 状态（4 个 CUJ 全部待实施）。设计文档 `docs/design/design-auto-import.md` 已落定全部架构决策（partial unique index、`-redoN` override、纯前端预览批次、LLM provider 复用、Chrome 扩展 + ADB 双通道）。本轮目标：从零到一交付 prd-006 全部 4 个 CUJ 的工程闭环（含 Chrome 扩展 sub-project + 后端 router + ADB 集成 + LLM SKU 匹配 + 4 个前端页面 + 单事务 commit + 集成测试），上线后用户可在 `/orders/import` 与 `/settings/auto-import` 完成两个渠道的扫单 → 校对 → 一键导入到 prd-001 待处理队列。

实施前提：
- `OpenAICompatibleVisionProvider` 必须抽出底层 `chat_completion()` 让 intake / auto-import 共用（**Group 1 阻塞所有后续**）。
- `Order` 表需补 4 列 + 一个 partial unique index（**Group 1 阻塞 Group 2 commit**）。
- Chrome 扩展是独立子项目（`extension/` 目录），可并行开发但 ID 由 `.env` 的 `VITE_INFILL_EXT_ID` 注入。

锁定决策（不再讨论）：预览批次纯前端态，无后端 TTL；DashScope 复用 `QWEN_*` env；菜单保持平级 7 项，自动导入入口由 `/orders` 与 `/settings` 页内按钮跳转；`-redoN` 后缀作为 override 写入方案；commit 单事务全或无；必填三件套 `platform / external_order_id / buyer_nickname` 缺失则丢弃；`@types/chrome` + 严格类型，禁 `any`。

---

## Parallel Group 1（基础设施 — 阻塞后续全部组）

### Task 1.1: LLM provider 抽出 `chat_completion()` 底层方法

- **Do**:
  1. 在 `backend/app/services/intake_llm.py::OpenAICompatibleVisionProvider` 中新增公开方法：
     ```python
     def chat_completion(
         self,
         messages: list[dict],
         *,
         json_object: bool = False,
         max_tokens: int = 4096,
         temperature: float = 0.1,
         timeout_seconds: int = 120,
     ) -> str:
         """调底层 OpenAI 兼容 chat/completions，返回 message.content 字符串。
         失败抛 LLMProviderError(error_kind, message, raw_preview)。"""
     ```
     从现有 `recognize()` 中抽出「构造 httpx Client → POST → 状态码判定 → 提取 choices[0].message.content」整段（即第 270-347 行的 HTTP + 错误码映射 + content 提取部分），保留全部 error_kind 映射（`no_api_key` / `timeout` / `http_401` / `http_5xx` / `image_too_large` / `schema_invalid`）。当 `json_object=True` 时加 `response_format={"type": "json_object"}`，否则省略。
  2. 重写 `recognize()` 内部委托给 `chat_completion()`：
     - 构造 messages（system + user 多图）
     - 调 `content = self.chat_completion(messages, json_object=True)`
     - 现有 `_strip_markdown_json` + `json.loads` + schema 校验（components/plates）保持不变
  3. **零行为变更**：所有 71 个 intake 测试必须通过（`pytest backend/tests/test_intake.py`）。
- **Files**:
  - `backend/app/services/intake_llm.py`（重构 `OpenAICompatibleVisionProvider`）
- **Done when**:
  - `chat_completion(messages, *, json_object=True)` 公开方法存在并返回 `str`
  - `recognize(...)` 内部委托给 `chat_completion()`，外部签名与返回结构不变
  - `pytest backend/tests/test_intake.py` 71 测试全绿
  - 新增 ≤ 3 个针对 `chat_completion()` 的单测（mock httpx，覆盖正常返回 / 401 / 5xx / timeout / json_object 开关）

### Task 1.2: `Order` 表 schema 扩展 + partial unique index 启动期补建

- **Do**:
  1. 在 `backend/app/models.py::Order` 类追加 4 个 nullable 列：
     - `platform: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)`
     - `external_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)`
     - `buyer_nickname: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)`
     - `external_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)`
  2. 在 `backend/app/services/catalog.py` 追加新函数 `ensure_order_auto_import_schema_exists(engine: Engine) -> bool`（参考现有 `ensure_order_notes_column_exists`）：
     - 用 `inspect(engine)` 检查 4 列与索引是否已存在
     - 缺失列 → `ALTER TABLE orders ADD COLUMN <name> <type> DEFAULT NULL`
     - 缺失 `uq_orders_platform_external` 索引 → `CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_platform_external ON orders(platform, external_order_id) WHERE platform IS NOT NULL AND external_order_id IS NOT NULL`
     - 幂等返回 bool（任一字段被新增过则 True）
  3. 在 `backend/app/schemas.py::OrderOut` / `OrderCreate` 等相关 schema 中追加 4 个 optional 字段（`Optional[str]` / `Optional[datetime]`），现有手工录单端点保持不传即 `None`。
  4. 不在本任务中调用新函数（由 Task 5.1 wiring 阶段串入 `main.py.lifespan`，确保顺序正确）。
- **Files**:
  - `backend/app/models.py`
  - `backend/app/services/catalog.py`
  - `backend/app/schemas.py`
- **Done when**:
  - 4 个新字段写入 `models.py`、schema 通过启动加载
  - `ensure_order_auto_import_schema_exists(engine)` 函数存在且幂等
  - 新增单测 `backend/tests/test_auto_import_migration.py` 覆盖：①「空 DB → 调一次返回 True，再调返回 False」②「已有 4 列但无索引 → 调一次创建索引」③「手工录单 `(NULL, NULL)` 多条不冲突」
  - 现有 `pytest backend/tests/test_orders.py` 全绿（兼容性验证）

### Task 1.3: Chrome 扩展子项目（独立 `extension/` 子目录）

- **Do**:
  1. 在仓库根新建 `extension/` 子目录（与 `frontend/` / `backend/` 同级）。
  2. 写 `extension/manifest.json`（Manifest V3）：
     - `name`: "infill 小红书千帆抓单"
     - `version`: "0.1.0"
     - `permissions`: `["tabs", "scripting"]`
     - `host_permissions`: `["*://*.qianfan.xiaohongshu.com/*"]`
     - `externally_connectable.matches`: `["http://localhost:5173/*", "http://localhost:8000/*"]`（生产部署 IP 后续替换）
     - `background.service_worker`: `"background.js"`
     - `content_scripts`: 配置 `*qianfan.xiaohongshu.com/*` → `content_xhs.js`，`run_at: document_idle`
  3. 写 `extension/background.js`（service worker）：
     - `chrome.runtime.onMessageExternal.addListener` 处理两种 `action`：
       - `ping` → `sendResponse({ok: true, version: chrome.runtime.getManifest().version})`
       - `scrape_xhs` → 调 `chrome.tabs.query` 找千帆 tab → `chrome.scripting.executeScript` 注入 `extractQianfanOrders()` 抓 DOM → 把 `raw_orders` POST 给 `http://<INFILL_HOST>:8000/api/auto-import/xhs/scan` → 透传响应给前端 via `sendResponse`
     - 无千帆 tab → `sendResponse({ok: false, error_kind: "extension_no_xhs_tab"})`
  4. 写 `extension/content_xhs.js`（DOM 抓取函数）：
     - 顶部常量定义 CSS 选择器（订单卡片容器 / external_order_id / 商品标题 / 件数 / 买家昵称 / 下单时间）
     - `extractQianfanOrders()` 返回 `Array<{external_order_id, buyer_nickname, external_created_at, products: [{listing_title, quantity}]}>`
     - 选择器找不到 → 返回空数组并打 `console.error`
     - 选择器具体路径可先用合理猜测（如 `[data-order-id]` / `.order-card .buyer-name` / `.product-title`）+ TODO 注释「实际选择器待千帆 DOM 真实样本验证 / 后续 QA 阶段校准」
  5. 写 `scripts/build-extension.sh`：
     - 用 `zip` 把 `extension/manifest.json` `background.js` `content_xhs.js` 打成 `release/extension/infill-xhs-scraper-v<version>.zip`
     - 输出到 stdout 的 zip 路径供 CI / 手动复制使用
  6. 写 `extension/README.md`：3 步开发者加载方法（`chrome://extensions/` → 开发者模式 → 加载已解压扩展）+ 「装好后复制扩展 ID 到 frontend/.env 的 `VITE_INFILL_EXT_ID`」。
- **Files**:
  - `extension/manifest.json`（新增）
  - `extension/background.js`（新增）
  - `extension/content_xhs.js`（新增）
  - `extension/README.md`（新增）
  - `scripts/build-extension.sh`（新增）
  - `.gitignore`（追加 `release/extension/`）
- **Done when**:
  - `extension/` 目录可用 Chrome「加载已解压扩展程序」装入（手动验证 `chrome.runtime.getManifest().version === "0.1.0"`）
  - `bash scripts/build-extension.sh` 产出 `release/extension/infill-xhs-scraper-v0.1.0.zip` 且解压后内含 manifest + 两个 JS 文件
  - 装入 Chrome 后从 DevTools Console 跑 `chrome.runtime.sendMessage("<EXT_ID>", {action:"ping"}, r=>console.log(r))` 收到 `{ok:true, version:"0.1.0"}`
  - 参考 `docs/ux/prd-006-auto-import-orders/cuj-1-no-extension.html` 的安装引导文案

---

## Parallel Group 2（后端业务 — 依赖 Group 1）

### Task 2.1: ADB client 子进程封装 + 闲鱼 config 端点 + 诊断逻辑

- **Do**:
  1. 新建 `backend/app/services/adb_client.py`：
     - `class AdbClient(adb_path: str = None)`：从 `os.environ.get("ADB_PATH", "adb")` 读路径
     - `is_installed() -> bool`：`subprocess.run([adb, "version"], timeout=5)`
     - `connect(endpoint: str, timeout_s=5) -> tuple[bool, str]`：解析 `adb connect` 输出
     - `list_devices() -> list[AdbDevice]`：`adb devices -l` → `[(serial, state)]`
     - `screencap(serial: str, dest_path: str) -> bytes`：`adb -s <serial> shell screencap -p /sdcard/infill_<uuid>.png` → `adb pull` → `adb rm` → 读 dest 字节
     - `class AdbDevice: serial, state, properties` dataclass
     - 全部子进程调用包 try/except 处理 `FileNotFoundError` / `TimeoutExpired` / `CalledProcessError`
  2. 新建 `backend/app/services/auto_import.py` 模块（仅放置本任务相关函数；其它任务追加）：
     - `def diagnose_adb(device_type, pc_ip, port) -> list[Diagnostic]`：按 design-auto-import.md §5.3 实现 4 项检查（ADB 装 / ping / nc / device state），返回 `[Diagnostic(label, ok, hint)]`
     - `def get_adb_config(db) -> dict`：从 `SystemConfig` 读 `auto_import_adb_device_type` / `_pc_ip` / `_port`，缺失返回默认 `{device_type: "mumu", pc_ip: "", port: 7555}`
     - `def set_adb_config(db, device_type, pc_ip, port)`：upsert 三个 `SystemConfig` row
     - 默认端口表 `DEFAULT_PORTS = {"mumu": 7555, "bluestacks": 5555, "ldplayer": 5555, "usb": 5037}` 作为模块常量
  3. 新建 `backend/app/schemas_auto_import.py` 放置全部 Pydantic schema：
     - `AdbConfig(device_type, pc_ip, port)`、`Diagnostic(label, ok, hint)`、`TestAdbResponse(ok, connected, device_serial, system, diagnostics)`
     - 其它 CUJ 的 schema 一并在此文件占位（与 Task 2.3 共用）
- **Files**:
  - `backend/app/services/adb_client.py`（新增）
  - `backend/app/services/auto_import.py`（新增，仅 ADB 部分）
  - `backend/app/schemas_auto_import.py`（新增）
- **Done when**:
  - `AdbClient` 可被单元测试用 `monkeypatch.setattr(subprocess, "run", fake)` mock
  - `diagnose_adb()` 4 种诊断状态组合覆盖（ADB 未装 / ping fail / port closed / device offline / OK）通过 ≥ 5 个单测
  - `get_adb_config / set_adb_config` 用 in-memory SQLite 单测验证 upsert
  - 单测落在 `backend/tests/test_auto_import.py`（新增）下的 `TestAdbClient` + `TestDiagnoseAdb` + `TestAdbConfig`

### Task 2.2: LLM SKU 匹配（auto_import_llm）+ sku-search + extension-status

- **Do**:
  1. 新建 `backend/app/services/auto_import_llm.py`：
     - `SKU_MATCH_SYSTEM_PROMPT`：硬编码 prompt（详见 design-auto-import.md §3.3），含 `{table_rows}` 占位
     - `def match_listing_to_sku(listing_title, catalog_skus, *, timeout_seconds=30) -> tuple[str|None, float, str]`：
       - `provider = get_active_provider()`，None 则抛 `LLMProviderError("no_api_key", ...)`
       - 渲染 system prompt（catalog_skus 全部注入），user message 仅传 `listing_title`
       - 调 `provider.chat_completion(messages, json_object=True)`
       - 解析返回 JSON `{matched_sku_code, confidence, reasoning}`，返回三元组
     - `def parse_xianyu_screenshot(image_bytes) -> list[dict]`：调 `provider.chat_completion()` with vision payload（含图）+ prompt「解析闲鱼订单列表截图」→ 返回 `[{external_order_id, buyer_nickname, external_created_at, products: [...]}]`
  2. 在 `backend/app/services/auto_import.py` 追加：
     - `def search_skus(db, q: str, limit: int = 10) -> list[dict]`：在 `Product.name` / `Product.sku` 用 `LIKE` + 简单拼音 fallback（先做汉字 + sku；拼音可放 TODO），返回 `[{sku, name}]`
     - `def get_extension_status() -> dict`：从 `os.environ.get("VITE_INFILL_EXT_ID")`（实际由前端 .env 读，但后端可读 `INFILL_EXT_ID` 兜底）返回 `{configured: bool, expected_version: "0.1.x", env_var_name: "VITE_INFILL_EXT_ID"}` — 实际探活由前端 `chrome.runtime.sendMessage` 完成，后端仅返回配置状态
- **Files**:
  - `backend/app/services/auto_import_llm.py`（新增）
  - `backend/app/services/auto_import.py`（追加 LLM + SKU 搜索部分）
- **Done when**:
  - `match_listing_to_sku` 用 `FakeProvider` mock `chat_completion()` 覆盖：① 返回有效 JSON ② 返回 markdown 包裹 JSON ③ matched_sku_code = null（confidence < 0.55）④ HTTP 401 → 抛 `LLMProviderError`
  - `parse_xianyu_screenshot` 用 fake provider 覆盖：① 正常解析 ② JSON schema 缺失 → 抛错
  - `search_skus` 单测覆盖：汉字模糊 / sku code 精确 / 空查询 / limit 截断
  - 单测落在 `backend/tests/test_auto_import.py` 下的 `TestSkuMatch` + `TestXianyuParse` + `TestSkuSearch`

### Task 2.3: Router 与扫描/commit 业务编排（routers/auto_import.py）

- **Do**:
  1. 新建 `backend/app/routers/auto_import.py`，挂前缀 `/api/auto-import`：
     - **CUJ-1 端点**：
       - `GET /xhs/extension-status` → 调 `get_extension_status()`
       - `POST /xhs/probe` → 返回 `{ok: true, has_xhs_tab: true}`（实际 has_xhs_tab 探活在前端通过扩展完成；本端点是占位 / 未来扩展可用）
       - `POST /xhs/scan` → 接收 `{batch_id, raw_orders}`，做：
         a. 必填三件套校验（`external_order_id` / `buyer_nickname` / `products`），缺失项归入 `dropped: [{external_order_id, reason: "missing_required_fields"}]`
         b. 查 DB 标记 `is_duplicate` + `existing_order_id`（按 `(platform, external_order_id)` 查 `Order`）
         c. 对每条 product.listing_title 串行调 `match_listing_to_sku()`，失败 → confidence=0、matched=null（不阻塞整批）
         d. 返回 `{ok: true, batch_id, items, dropped, stats: {total, dropped_count, duplicate_count, high_conf, mid_conf, low_conf}}`
     - **CUJ-2 端点**（异步背景任务用 `asyncio.create_task`）：
       - `POST /xianyu/probe` → 调 `diagnose_adb(...)`，返回 `{ok, adb_connected, device_serial, diagnostics}`
       - `POST /xianyu/screencap` → 调 `adb_client.screencap(...)` → 落到 `data/auto_import_tmp/<batch_id>/screen_<seq>.png` → spawn LLM 解析 task → 返回 `{ok, screen_id, seq}`。后端维护 `BATCH_SCREENS: dict[batch_id, ScreenState]` in-memory state
       - `GET /xianyu/scan-status?batch_id=<>` → 返回 `{batch_id, screens: [...], parsed_orders: [...]}`
       - `POST /xianyu/finish-scan` → 接收 `{batch_id}`，await 所有未完成 LLM task → 按 `external_order_id` 去重 → 调 `match_listing_to_sku` 二次匹配 → 清理 tmp 目录 → 返回 batch（与 xhs/scan 同 schema）
       - `POST /cancel-scan` → 接收 `{batch_id}`，abort + 清理 tmp
     - **CUJ-3 端点**：
       - `POST /sku-search` → 接收 `{q, limit}` → 调 `search_skus(db, q, limit)`
       - `POST /commit` → 接收 `{batch_id, items: [CommitItem]}`，**单事务**：
         a. `with db.begin():` 开事务
         b. 遍历每个 item，按 `(platform, external_order_id)` 查 DB
         c. 重复 + `override_duplicate=True` → 找下一个 `-redoN` 后缀（`SELECT external_order_id LIKE 'orig-redo%'` → 取 max +1）
         d. 重复 + `override_duplicate=False` → 跳过，计入 `skipped.duplicate`
         e. 校验所有 `product_sku` 存在；任一不存在 → `db.rollback()` + 返回 `{ok: false, error_kind: "commit_sku_not_found", error}`
         f. 创建 `Order(status='pending', created_at=now(), external_created_at=item.external_created_at, platform=..., external_order_id=...,buyer_nickname=...)` + 一对多 `OrderItem(product_id, quantity)`
         g. 全部成功 → `db.commit()`，返回 `{ok: true, stats: {新增, 重复跳过, 手动跳过, SKU匹配率}, created_order_ids, total_ms}`
     - **CUJ-4 端点**：
       - `GET /xianyu/config` / `PUT /xianyu/config` → 调 `get_adb_config / set_adb_config`
       - `POST /xianyu/test-adb` → 接收 `{device_type, pc_ip, port}` → 调 `diagnose_adb(...)` + `adb_client.list_devices()` 取 serial / system → 返回 `TestAdbResponse`
  2. 在 `backend/app/schemas_auto_import.py` 补全：`RawOrder`、`PreviewItem`、`PreviewProduct`、`ScanResponse`、`CommitProduct`、`CommitItem`、`CommitRequest`、`CommitResponse`、`SkuSearchRequest`、`SkuSearchResponse`、`ScreencapResponse`、`ScanStatusResponse`、`FinishScanRequest`、`ProbeXhsResponse`、`ProbeXianyuResponse`、`ExtensionStatusResponse`。
  3. **不**在 `main.py` 注册 router（Group 5 wiring 阶段串入）。
- **Files**:
  - `backend/app/routers/auto_import.py`（新增）
  - `backend/app/services/auto_import.py`（追加 scan / commit / batch state）
  - `backend/app/schemas_auto_import.py`（补全 schema）
- **Done when**:
  - `/api/auto-import/xhs/scan` 用 FastAPI `TestClient` + mock LLM + mock DB 通过：必填缺失丢弃 / 重复检测 / LLM 失败行红色
  - `/api/auto-import/commit` 单事务覆盖：① 50 单全成功 ② 1 单 sku 不存在 → rollback + 0 写入 ③ `-redoN` 算法（连续 override → redo1 / redo2）④ 重复且无 override → 静默跳过
  - 所有端点返回 `{ok: bool, ...}` 结构（与 intake 一致），不抛 HTTPException
  - 单测落在 `backend/tests/test_auto_import.py` 下的 `TestXhsScan` + `TestXianyuFinishScan` + `TestCommit` + `TestRedoSuffix`

---

## Parallel Group 3（前端基础设施 — 依赖 Group 2 端点设计）

### Task 3.1: api/client.ts 追加 autoImport 子对象 + extension.ts 封装 + 类型定义

- **Do**:
  1. 在 `frontend/src/api/client.ts` 追加 `api.autoImport.*` 子对象（保持与现有 `api.intake.*` 风格一致），覆盖 Task 2.3 的全部端点：
     - `xhs.extensionStatus()` / `xhs.probe()` / `xhs.scan(payload)`
     - `xianyu.probe()` / `xianyu.screencap(batchId)` / `xianyu.scanStatus(batchId)` / `xianyu.finishScan(batchId)` / `xianyu.testAdb(payload)` / `xianyu.getConfig()` / `xianyu.putConfig(payload)`
     - `cancelScan(batchId)` / `skuSearch(q, limit)` / `commit(payload)`
     - 全部参数与返回值用 `interface AutoImport*` 严格类型（禁 `any`）— 与 design-auto-import.md §2 schema 对齐
  2. 新建 `frontend/src/api/extension.ts`：
     - 顶部 `const EXT_ID = import.meta.env.VITE_INFILL_EXT_ID as string | undefined`
     - 导出 `pingExtension(timeoutMs?: number): Promise<{ok: boolean; version?: string}>`
     - 导出 `scrapeXhs(batchId: string): Promise<{ok: boolean; scan_response?: AutoImportScanResponse; error_kind?: string}>`
     - 用 `@types/chrome` 的 `chrome.runtime.sendMessage` 类型；`EXT_ID` 未定义时返回 `{ok: false, error_kind: "no_ext_id"}`
  3. 在 `frontend/package.json` 加 `@types/chrome` 依赖（`pnpm add -D @types/chrome` 或对应 npm）
  4. 在 `frontend/.env.example`（如不存在则新建）写 `VITE_INFILL_EXT_ID=<install extension and paste the ID here>`
- **Files**:
  - `frontend/src/api/client.ts`（追加 `autoImport` 段）
  - `frontend/src/api/extension.ts`（新增）
  - `frontend/package.json`（加依赖）
  - `frontend/.env.example`（新增 / 追加）
- **Done when**:
  - `npm run build` (tsc -b) 无 TS 错误，且 autoImport 段无任何 `any` 类型
  - `extension.ts` 在非 Chrome 环境（`window.chrome` 未定义）返回 `{ok: false}` 而不抛异常
  - `interface AutoImportPreviewItem` 等关键类型导出供其它模块复用

### Task 3.2: AutoImportSettings 页（CUJ-4）+ 入口按钮

- **Do**:
  1. 新建 `frontend/src/pages/settings/AutoImportSettings.tsx`（路由 `/settings/auto-import`）：
     - 顶部面包屑「系统设置 / 自动导入」+ 标题「自动导入设置」+ 副标题
     - 左右两张并列卡片，间距 24px
     - **左卡片「小红书千帆 · Chrome 扩展」**：
       - 卡片头 chip「小红书」红 `#ff2442`
       - 状态行：调 `api.autoImport.xhs.extensionStatus()` 拿 `configured: bool`，再调 `pingExtension()` 实测
       - configured + ping ok → 绿点「● 扩展已检测到 · v0.1.x」 + 「扩展 ID: ABC...XYZ (已就绪)」（truncate 显示）+ 「重新检测」secondary 按钮
       - configured 但 ping 失败 → 蓝点「● 扩展未检测到」+ 安装引导（下载 link → `/static/extensions/infill-xhs-scraper-v0.1.0.zip` + 4 步加载步骤）+ 「我已安装，重新检测」按钮
       - **未 configured**（`VITE_INFILL_EXT_ID` 为空）→ 蓝色 setup 块「● 未配置 — 请在 frontend/.env 设置 `VITE_INFILL_EXT_ID=<扩展ID>` 后重启前端」
       - **不提供输入框**（按锁定决策 #2，扩展 ID 走构建时 env）
     - **右卡片「闲鱼 · Android ADB」**：
       - 卡片头 chip「闲鱼」橙 `#ff7a00`
       - 表单：「设备类型」AntD `Select`（mumu / bluestacks / ldplayer / usb）/ 「PC IP」`Input` / 「端口号」`InputNumber`
       - 设备类型 onChange → 自动填默认端口（MuMu=7555 / 蓝叠=5555 / 雷电=5555 / USB=5037）
       - 「测试 ADB 连接」橙 primary 按钮 → 调 `api.autoImport.xianyu.testAdb(payload)`，按钮 loading，结果回显（绿框「连接成功 · 序列号 · 系统」/ 红框「连接失败」+ 三项诊断 list）
       - 「保存配置」secondary 按钮 → 调 `api.autoImport.xianyu.putConfig(payload)`，成功 toast「已保存自动导入配置」
       - 表单未改时「保存配置」disabled
     - 页底灰底说明条：「LLM 匹配阈值固定（≥0.85 高 / 0.55~0.84 中 / <0.55 低）。LLM API key 走 `.env` 配置」+ 若 `extensionStatus` 返回中含 `no_api_key` 提示则变红色 alert
  2. 在 `frontend/src/App.tsx` 注册两条 route：`/settings/auto-import` → `AutoImportSettings`，以及 `/orders/import` → `AutoImport`（为 Task 4.1 提前预留，避免 App.tsx 二次冲突）。`AutoImport` 模块此 task 内可先 `lazy import` + 占位空组件；Task 4.1 替换实现。
  3. 在 `frontend/src/pages/Settings.tsx` 顶部加一个 section 卡片「自动导入设置」+ 「打开自动导入设置 →」按钮跳 `/settings/auto-import`
  4. **不修改 `Layout.tsx` 的菜单**（保持 7 项扁平，按锁定决策 #4）
- **Files**:
  - `frontend/src/pages/settings/AutoImportSettings.tsx`（新增）
  - `frontend/src/App.tsx`（追加 2 条 route）
  - `frontend/src/pages/Settings.tsx`（顶部加跳转 section）
- **Done when**:
  - 浏览器访问 `/settings/auto-import` 渲染两张并列卡片，匹配 `docs/ux/prd-006-auto-import-orders/cuj-4-initial.html` 的视觉布局
  - 「设备类型」切换自动填默认端口
  - 「测试 ADB 连接」按 mock 后端响应正确显示绿/红诊断块
  - `/settings` 页顶部新增的「打开自动导入设置」按钮可点击跳转
  - `tsc -b` 无错

---

## Parallel Group 4（前端 CUJ 页面 — 依赖 Group 3 client.ts + extension.ts）

### Task 4.1: AutoImport 父容器 + XhsTab（CUJ-1）

- **Do**:
  1. 新建 `frontend/src/pages/AutoImport.tsx` 作为父状态机（参考 `pages/Intake.tsx` 模式）：
     - `AutoImportMode` discriminated union：`tabs` / `scanning_xhs` / `scanning_xianyu` / `preview` / `committing` / `success` / `failure`
     - 顶部面包屑「订单管理 / 自动导入 / <动态后缀>」+ 标题「自动导入」+ 副标题
     - 双 tab 切换栏「小红书千帆」红 / 「闲鱼」橙，sticky state（xhsState / xianyuState 分别存）
     - 根据 mode 渲染对应子组件
     - 替换 Task 3.2 在 App.tsx 中预留的 `/orders/import` 路由占位
  2. 新建 `frontend/src/pages/auto_import/XhsTab.tsx`（CUJ-1）：
     - 入页并发：① `pingExtension()` ② `api.autoImport.xhs.probe()`
     - 左侧 sticky 控制栏 360px：状态指示器三态（● 就绪 / ● 扩展未装 / ● 未发现千帆 tab）+ 「开始扫描」红 primary 按钮（仅就绪态启用）
     - 点「开始扫描」→ 切换到 `scanning_xhs` mode → 调 `scrapeXhs(batchId)`（扩展把数据 POST 给后端，扩展再把响应回传前端）
     - 主区右侧 5 步纵向进度卡片（① 连接扩展 ② 定位千帆 tab ③ 抓取 DOM ④ 解析订单 ⑤ LLM 匹配 SKU），用乐观推进（每步在前端收到对应响应后置 ✓）
     - 扫描完成 → 调用父组件 setMode 切到 `preview`
     - 错误态：扩展未装显示蓝色 setup 块；未发现千帆 tab 显示黄色 warning 块；LLM 失败显示「跳过 SKU 匹配」选项（所有行视为低置信度）
     - 「闲鱼扫描进行中」时「开始扫描」disabled + tooltip
  3. 新建 `frontend/src/pages/auto_import/ScanningProgress.tsx`（5 步通用进度卡片，xhs 用；闲鱼有独立 ScreencapGrid，不共享）
  4. 在 `frontend/src/pages/Orders.tsx` 顶部加按钮「自动导入 →」跳 `/orders/import`
  5. **不**修改 Layout 菜单
- **Files**:
  - `frontend/src/pages/AutoImport.tsx`（新增父容器）
  - `frontend/src/pages/auto_import/XhsTab.tsx`（新增）
  - `frontend/src/pages/auto_import/ScanningProgress.tsx`（新增）
  - `frontend/src/App.tsx`（仅修改预留的 `/orders/import` route 的 import 指向）
  - `frontend/src/pages/Orders.tsx`（加跳转按钮）
- **Done when**:
  - `/orders/import` 默认渲染小红书 tab，匹配 `docs/ux/prd-006-auto-import-orders/cuj-1-initial.html`
  - 扩展未装态匹配 `cuj-1-no-extension.html`
  - 无千帆 tab 态匹配 `cuj-1-no-xhs-tab.html`
  - 扫描中态匹配 `cuj-1-scanning.html`（5 步进度 + 取消按钮）
  - 切到闲鱼 tab 再切回小红书，扫描中 state 不丢
  - `tsc -b` 无错

### Task 4.2: XianyuTab（CUJ-2）+ 截屏缩略图条 + 异步轮询

- **Do**:
  1. 新建 `frontend/src/pages/auto_import/XianyuTab.tsx`（CUJ-2）：
     - 入页调 `api.autoImport.xianyu.probe()` → 状态指示器「● ADB 就绪」/「● ADB 错」
     - 左侧控制栏 360px：状态指示器 + 「设备类型」只读 + 「PC IP / endpoint」只读 + 「编辑」link 跳 `/settings/auto-import`
     - 灰底操作说明块 4 步引导（手动滚 + 逐次点截屏 + 手动判断停 + 点完成）
     - 底部两按钮：「截屏」橙 secondary（ADB 就绪时启用）+ 「完成截屏，开始解析」橙 primary（≥1 张截屏时启用）+ 「取消」灰
     - 点「截屏」→ 调 `api.autoImport.xianyu.screencap(batchId)`，按钮短暂 disabled + spinner，返回后立即重新启用
  2. 新建 `frontend/src/pages/auto_import/ScreencapGrid.tsx`：
     - 顶部计数「已截屏 N 张」
     - 缩略图条（横向 4 列 N 行网格，120×80px），每张缩略图带状态徽章（🔄 解析中 / ● 已解析 / ! 解析失败 / ✗ 截屏失败）
     - 缩略图条下方「正在解析第 X 张」文案
     - 再下方已解析订单 mini 卡片列表（每条 mini 摘要：买家昵称 + 商品标题 + 数量）
     - 每 1.5s 轮询 `api.autoImport.xianyu.scanStatus(batchId)` 拿 screens + parsed_orders 实时更新
  3. 在 `XianyuTab.tsx` 点「完成截屏，开始解析」→ 按钮 loading → 调 `api.autoImport.xianyu.finishScan(batchId)` → 切到父 `preview` mode
  4. ADB 错态渲染红色 err 块 + 三项诊断 + 「重新测试 ADB」按钮 + 「打开设置页修改 endpoint」link
  5. 「小红书扫描进行中」时本 tab 按钮 disabled + tooltip
- **Files**:
  - `frontend/src/pages/auto_import/XianyuTab.tsx`（新增）
  - `frontend/src/pages/auto_import/ScreencapGrid.tsx`（新增）
- **Done when**:
  - `/orders/import` 切到闲鱼 tab 渲染匹配 `docs/ux/prd-006-auto-import-orders/cuj-2-initial.html`
  - 截屏中渲染匹配 `cuj-2-captured.html` + `cuj-2-parsing.html`
  - ADB 错态匹配 `cuj-2-no-adb.html`
  - 轮询 scan-status 后缩略图徽章实时更新（🔄 → ● 或 !）
  - 「完成截屏」后切到 preview mode
  - `tsc -b` 无错

### Task 4.3: PreviewTable（CUJ-3）+ SkuPicker + Success/Failure 面板

- **Do**:
  1. 新建 `frontend/src/pages/auto_import/PreviewTable.tsx`（CUJ-3 主表格）：
     - 顶部页面头 + 来源 chip + 副标题
     - 4 chips 行（高/中/低置信度 + 重复订单），每个 chip 可点击「只看本类」筛选
     - 主表格列：`[checkbox] 平台 / 外部订单号 / 买家+下单时间 / 商品`
     - 行底色规则：白 / 浅黄 `#fffbe6` / 浅红 `#fff1f0` / 灰 `#f0f0f0`
     - 默认勾选规则：高/中+非重复 → 勾；低/重复 → 不勾
     - 低置信度行 checkbox disabled + tooltip
     - 商品子行：`[置信度 badge] [SkuPicker] [× 件数 InputNumber] [✕ 删除]` + 末尾「+ 添加商品」虚线按钮
     - 重复行第 3 列含灰色 `重复` tag + 「改判为新单 →」link → Modal 二次确认
     - 底部 sticky 工具栏：「将导入 X 单 · 共 Y 件」+「全选新单 / 全不选 / 反选」link + 「取消」/「导入勾选的 X 单」primary
  2. 新建 `frontend/src/pages/auto_import/SkuPicker.tsx`：
     - AntD `Popover` 360px 宽
     - 三段：① 当前匹配 + 原文标题灰底等宽框 ② LLM 推荐前 3 候选（confidence 降序）③ 底部 `Input.Search` → debounced 调 `api.autoImport.skuSearch(q)` 显示前 10 候选
     - 选中后回填，置信度显示「手选」
     - 底部 link「找不到对应 SKU？请先到产品录入」跳 `/intake`
  3. 新建 `frontend/src/pages/auto_import/SuccessPanel.tsx`（CUJ-3 成功页）：
     - 绿 `✓` + 标题「N 单已入待处理队列」+ 副标题
     - 4 stat 网格（新增 / 跳过重复 / 手动跳过 / SKU 匹配率）
     - 灰底批次详情条（来源 / 扫描时间 / 扫描方式 / 总耗时 / batch_id / 平均置信度）
     - 前 5 单 ID + 「查看全部 →」跳 `/orders`
     - 底部「前往订单管理」primary / 「继续导入<另一平台>」secondary
  4. 新建 `frontend/src/pages/auto_import/FailurePanel.tsx`（CUJ-3 失败页）：
     - 红 `!` + 标题「导入失败 — 未写入任何订单」+ 等宽错误详情
     - 底部「返回预览继续校对」primary / 「丢弃本批」secondary（二次确认）
  5. 「导入勾选的 X 单」点击 → 调 `api.autoImport.commit(payload)` → 成功切 success，失败切 failure
- **Files**:
  - `frontend/src/pages/auto_import/PreviewTable.tsx`（新增）
  - `frontend/src/pages/auto_import/SkuPicker.tsx`（新增）
  - `frontend/src/pages/auto_import/SuccessPanel.tsx`（新增）
  - `frontend/src/pages/auto_import/FailurePanel.tsx`（新增）
- **Done when**:
  - PreviewTable 渲染匹配 `docs/ux/prd-006-auto-import-orders/cuj-3-initial.html` 的 12 行样本布局
  - SkuPicker 浮窗匹配 mock 的三段结构
  - SuccessPanel 匹配 `cuj-3-success.html`
  - 行底色 / checkbox 状态逻辑符合 Acceptance Criteria（参见 PRD CUJ-3 §AC）
  - 「全选新单 / 全不选 / 反选」bulk actions 行为正确（重复 / 含未匹配商品的行不能被强制勾上）
  - `tsc -b` 无错

---

## Parallel Group 5（Wiring + 测试 + 扩展构建 — 全部依赖前面组）

### Task 5.1: main.py lifespan 串入 + router 注册 + .env.example + .gitignore

- **Do**:
  1. 在 `backend/app/main.py.lifespan` 中按顺序串入新调用（**必须严格顺序**）：
     ```
     auto_migrate(engine)
     Base.metadata.create_all(bind=engine)
     ensure_sku_column_exists(engine)
     ensure_order_notes_column_exists(engine)
     ensure_order_auto_import_schema_exists(engine)  # ← 新增
     load_catalog(db)
     ```
  2. 在 `backend/app/main.py` 注册 router：`from app.routers import auto_import` + `app.include_router(auto_import.router)`
  3. 把 `release/extension/infill-xhs-scraper-v0.1.0.zip` 复制到 `backend/static/extensions/`（构建期产物；运行期由 FastAPI 静态 mount 暴露 `/static/extensions/...`）。若 `backend/static/extensions/` 不存在则创建。在 `main.py` 中追加 `app.mount("/static/extensions", StaticFiles(directory=...), name="extensions")`（或扩展现有 SPA fallback 让 zip 文件直接命中）。
  4. 更新 `.env.example`（仓库根或 backend/.env.example）追加：
     ```
     LLM_PROVIDER=qwen
     QWEN_API_KEY=
     QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
     QWEN_MODEL=qwen-omni-turbo
     ADB_PATH=adb
     ```
  5. 更新 `.gitignore` 追加 `data/auto_import_tmp/` + `backend/static/extensions/` + `release/extension/`
  6. **不**修改 Layout 菜单（按锁定决策 #4）
- **Files**:
  - `backend/app/main.py`
  - `.env.example`（根或 backend/）
  - `.gitignore`
  - `backend/static/extensions/`（新目录）
- **Done when**:
  - `uvicorn app.main:app` 启动成功，stdout 显示「目录已加载」+ partial unique index 已创建（首次启动时）
  - `GET /api/auto-import/xhs/extension-status` 返回 200（即便未配置 ext id 也是 ok json）
  - `GET /static/extensions/infill-xhs-scraper-v0.1.0.zip` 返回 200 且文件大小 > 0
  - 旧手工录单功能（`POST /api/orders`）零回归

### Task 5.2: 后端集成测试（FastAPI TestClient）

- **Do**:
  1. 在 `backend/tests/test_auto_import.py` 追加 `TestE2E` 类（不重复 Group 2 单元测试覆盖过的部分），用 FastAPI `TestClient` 做端到端：
     - `test_xhs_scan_to_commit_happy_path`: mock LLM → POST 5 个 raw_orders → 拿到 batch → POST commit → 验证 5 条 Order 已写入 DB + 5 条 OrderItem
     - `test_commit_atomicity`: 5 单中 1 单 sku 不存在 → 整批 rollback → DB 中 0 条新 Order
     - `test_commit_redo_suffix`: 同一 external_order_id override 两次 → DB 中存在 `XHS-001 / XHS-001-redo1 / XHS-001-redo2` 三条记录
     - `test_commit_dedupe_skip_no_override`: 重复且 `override_duplicate=False` → 静默跳过，stats.重复跳过=1
     - `test_partial_unique_index_allows_manual`: 多条 `(platform=None, external_order_id=None)` 的人工录单不冲突
     - `test_xianyu_screencap_async_flow`: mock adb_client + LLM → POST screencap × 3 → GET scan-status 看到 3 screens + parsed_orders → POST finish-scan → 拿到完整 batch
- **Files**:
  - `backend/tests/test_auto_import.py`（追加 E2E 部分；Group 2 已建文件）
- **Done when**:
  - `pytest backend/tests/test_auto_import.py` 全绿（含 Group 2 单测 + 本任务 E2E）
  - `pytest backend/` 全部测试集合无回归（含 intake / orders / scheduler）
  - 覆盖 design-auto-import.md §测试策略 中所有「TestXxx」用例

### Task 5.3: Chrome 扩展构建脚本增强 + 文档完善

- **Do**:
  1. 在 `scripts/build-extension.sh` 增强：
     - 检测 `extension/manifest.json` 的 version
     - 打 zip → `release/extension/infill-xhs-scraper-v<ver>.zip`
     - 自动复制到 `backend/static/extensions/infill-xhs-scraper-v<ver>.zip`（确保 `backend/static/extensions/` 目录存在）
     - 输出最终路径
  2. 更新 `extension/README.md` 增加：
     - 「构建」段：`bash scripts/build-extension.sh`
     - 「分发」段：构建产物自动到 `backend/static/extensions/`，前端的 download link 指向该路径
     - 「获取扩展 ID」段：装入 Chrome 后从 `chrome://extensions/` 复制 ID → 粘到 `frontend/.env` 的 `VITE_INFILL_EXT_ID`
     - 「DOM 选择器维护」段：千帆改版后更新 `content_xhs.js` 顶部选择器常量 → 升 manifest version → 重新构建分发
  3. 在 `docs/design/design-auto-import.md` 附录 B 实施前 Checklist 中勾选 ✓ 已完成项（仅修改文档勾选状态，不改其它内容）
- **Files**:
  - `scripts/build-extension.sh`
  - `extension/README.md`
  - `docs/design/design-auto-import.md`（仅勾选 checklist）
- **Done when**:
  - `bash scripts/build-extension.sh` 产出 zip 且自动 copy 到 `backend/static/extensions/`
  - `extension/README.md` 文档完整
  - 启动后端后 `curl http://localhost:8000/static/extensions/infill-xhs-scraper-v0.1.0.zip -o /tmp/x.zip && unzip -l /tmp/x.zip` 显示 manifest + JS 文件

### Task 5.4: 更新 docs/status.md（CUJ 状态翻牌）

- **Do**:
  1. 修改 `docs/status.md` 的 CUJ 状态表：把 prd-006 CUJ-1/2/3/4 的 Impl 从 `not started` 改为 `merged`
  2. 在「近期活动」表底部追加 5 行新提交概述（Group 1~5 各一行）
  3. 在「文件结构」段追加 `extension/` 子目录 + `backend/app/routers/auto_import.py` + `backend/app/services/auto_import.py` + `auto_import_llm.py` + `adb_client.py` + `frontend/src/pages/auto_import/` 子目录
  4. 在「核心数据类型」表追加 `PreviewBatch / PreviewItem / PreviewProduct / CommitItem / AdbConfig`
  5. 在「下一步建议」段记录「prd-006 等待首轮 QA」
  - **不**改 QA / PM 列（由 QA / PM 流程后续填写）
- **Files**:
  - `docs/status.md`
- **Done when**:
  - prd-006 全 4 CUJ 显示 Impl=merged
  - 文件结构 / 数据类型 / 近期活动段反映 prd-006 实现现状
  - Markdown 渲染无破坏

---

## Conflict Risks

- **`backend/app/main.py`**：仅 Task 5.1 修改（lifespan + router 注册）。Group 1/2/3/4 不动它，避免冲突。
- **`backend/app/services/intake_llm.py`**：仅 Task 1.1 重构（抽 `chat_completion`）。Task 2.2 仅**消费** `chat_completion`，不修改文件。Group 1 完成是 Group 2 开始的前置 — 已串行化。
- **`backend/app/services/auto_import.py`**：Task 2.1（ADB + config 段）和 Task 2.2（LLM SKU 匹配 + sku-search 段）+ Task 2.3（scan / commit 段）都写这个文件。**缓解**：每个 task 写不同函数 / 不同 section，按文件顶部用 `# ==== 段名 ====` 注释分隔。三 task 写完后 git 合并冲突只会出现在 import 段；建议三 task 分别先 `git fetch && git rebase main` 再合。
- **`backend/app/schemas_auto_import.py`**：Task 2.1（AdbConfig / Diagnostic / TestAdbResponse）+ Task 2.3（PreviewItem / CommitItem 等大段 schema）都写。**缓解**：Task 2.1 先写自己的 schema 段（占据文件前半），Task 2.3 在文件后半追加 — 顺序冲突小。
- **`backend/tests/test_auto_import.py`**：Group 2 三 task + Task 5.2 都新增测试类。**缓解**：每 task 用自己专属 `Test<Section>` 类名（`TestAdbClient` / `TestSkuMatch` / `TestCommit` / `TestE2E`），不互相覆盖。
- **`frontend/src/api/client.ts`**：仅 Task 3.1 追加 `autoImport` 子对象。Group 4 仅消费，不改文件。
- **`frontend/src/App.tsx`**：Task 3.2 一次加 2 条 route（`/settings/auto-import` 与 `/orders/import`，后者先指 lazy 占位）；Task 4.1 仅替换占位 import 为真实 `AutoImport` 模块 — 修改的是同一行的 import 路径而非 routes 数组，冲突最小。
- **`frontend/src/pages/Orders.tsx` / `Settings.tsx`**：Orders 仅 Task 4.1 加按钮；Settings 仅 Task 3.2 加 section。各自独占。
- **`.gitignore` / `.env.example`**：Task 1.3 加 `release/extension/`，Task 5.1 加更多条目。两 task 不同 group，Task 5.1 后做不冲突。
- **`extension/manifest.json` / `scripts/build-extension.sh`**：Task 1.3 创建初版；Task 5.3 增强 build 脚本。两 task 不同 group，串行。
- **`docs/status.md` / `docs/design/design-auto-import.md`**：仅 Task 5.4 / Task 5.3 修改，无并行冲突。

**预计效率收益**：15 任务分 5 组（3 + 3 + 2 + 3 + 4），相比纯串行的 15 倍开销，本计划约为 **5 倍组深度**——若每任务 2 小时则总 wall-clock ~10 小时 vs 串行 ~30 小时，节省 ~3 倍。

---

## QA Fix Tasks（iter4 QA Gate — Retry 1 后 5 个 MEDIUM+ 全部 CLOSED）

由 iter4 QA gate（2026-06-18 22:20:32 UTC+8）首轮发现，Retry 1（2026-06-18 22:43:16 UTC+8）已闭环验证。

### HIGH（已修复 — closed）

- [x] **QA-fix [HIGH][BUG]**: probe / test-adb 端点的 `adb_connected` 仅按 `bool(list_devices())` 判定，未校验配置 endpoint — 修法：`xianyu_probe` / `xianyu_test_adb` 用 `diagnostics[name=device_state].ok` 作为 `adb_connected` 真值；list_devices 中筛 serial 起始于 pc_ip 且 state=device 的设备 — fixed by 1b5f35f, verified by TestQAFixAdbConnectedTruth (4 tests) + live curl + Playwright walk × 2 — source: qa-report.md 2026-06-18 22:20:32 (UTC+8), closed: 2026-06-18 22:43:16 (UTC+8)
- [x] **QA-fix [HIGH][BUG]**: 前端 XianyuTab 加 `allDiagsOk = diagnostics.every(d => d.ok)` 防御性检查，require `resp.ok && resp.adb_connected && allDiagsOk` 才进 idle — fixed by 1b5f35f, verified by Playwright walk × 2（pc_ip="" → 渲染 "ADB 未连接" + error block + 两按钮 disabled） — source: qa-report.md 2026-06-18 22:20:32 (UTC+8), closed: 2026-06-18 22:43:16 (UTC+8)

### MEDIUM（已修复 — closed）

- [x] **QA-fix [MEDIUM][BUG]**: XhsTab `NoExtensionBlock` 加 primary blue「下载扩展 zip」按钮（size large、`href=/static/extensions/infill-xhs-scraper-v0.1.0.zip download`、marginTop 16）— fixed by cce7b19, verified by DOM query + live download endpoint curl（content-type: application/zip） — source: qa-report.md 2026-06-18 22:20:32 (UTC+8), closed: 2026-06-18 22:43:16 (UTC+8)
- [x] **QA-fix [MEDIUM][VISUAL_DEVIATION]**: 与 `cuj-1-no-extension.html` mock 比对 — 同上修法已合并；视觉一致（按钮位置 / 颜色 / 文案对齐；mock "(12 KB)" 后缀降级为 LOW 残留） — fixed by cce7b19 — source: qa-report.md 2026-06-18 22:20:32 (UTC+8), closed: 2026-06-18 22:43:16 (UTC+8)
- [x] **QA-fix [MEDIUM][BUG]**: PreviewTable `rows.length === 0` 时居中渲染「未抓取到任何订单」+ 副文案「请检查千帆 tab 是否打开，或闲鱼是否截取到订单页」+ 「返回扫描页」按钮（onClick=onCancel） — fixed by cce7b19, verified by React fiber 反射注入 + Playwright walk × 2（xhs 与 xianyu 来源） — source: qa-report.md 2026-06-18 22:20:32 (UTC+8), closed: 2026-06-18 22:43:16 (UTC+8)

### LOW（积压）

- [ ] **QA-fix [LOW][BUG]**: `POST /api/auto-import/xhs/probe` 占位实现（永远 has_xhs_tab=true），违反 AC 「探查千帆 tab」语义；或真做或去掉 — backend/app/routers/auto_import.py:99 — source: qa-report.md 2026-06-18 22:20:32 (UTC+8)
- [ ] **QA-fix [LOW][BUG]**: AntD `Spin tip` deprecation console warning（carry-over from iter3）— 替换 `tip` → `description` 属性 — source: qa-report.md 2026-06-18 22:20:32 (UTC+8)
