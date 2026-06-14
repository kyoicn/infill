# Task Plan

Last updated: 2026-06-14 01:49:12 (UTC+8)

## Current State

- 上轮迭代（iter2）是纯测试加固，0 行生产代码改动，全量 131 个单测通过。`docs/qa-report.md` 无任何 BUG / REGRESSION / FABRICATION（也没有 LOW 级遗留）。
- prd-000 / 001 / 002 / 003 / 004 五份 PRD 全部 `merged`；prd-005「产品录入」（5 个 P0 CUJ）全部 `not started`。
- 本轮目标：交付 prd-005-intake 完整端到端链路。设计权威文件已就绪：`docs/prd/prd-005-intake.md`（546 行业务规格 + 验收标准）、`docs/design/design-intake.md`（架构 + 数据契约 + 测试策略 + 决策矩阵）、`docs/ux/prd-005-intake/*.html`（18 份视觉 mock，配 `_shared.css` 色板）。
- 分组策略：先并行落地共享基础设施（G1），然后并行落地 CUJ-1 / CUJ-2 后端 + 前端（G2），再并行落地 CUJ-3 / CUJ-4 / CUJ-5 三个独立终段（G3），最后串行做端到端冒烟测试 + 构建（G4）。13 个任务跨 4 组，相比串行约缩短 3 倍。

## Parallel Group 1（基础设施 / 没有前置依赖）

### Task: T1 — 后端基础设施 (deps + schemas + 配置 + 路由 stub + 空测试文件)

- **Do**:
  1. 编辑 `backend/requirements.txt` 追加三行依赖（保持原有顺序，附在末尾）：
     - `Pillow>=10.0,<12.0`
     - `python-multipart>=0.0.9`
     - `httpx>=0.27`
  2. 新建 `backend/app/schemas_intake.py`，按 `docs/design/design-intake.md §2 数据契约` 完整定义所有 Pydantic 模型（`UploadedImage`、`UploadResponse`、`RecognizeRequest`、`DraftComponent`、`DraftPlate`、`Conflict`、`Draft`、`RecognizeResponse`、`ColorCell`、`Variant`、`FinalDraft`、`MergeStats`、`MergeResponse`）。所有响应 schema 必须以 `ok: bool` 开头，错误分支带 `error_kind`、`error` 字段。
  3. 新建 `backend/app/routers/intake.py`，注册 5 个 stub 端点（每个返回 `{"ok": False, "error": "not implemented"}` 即可），prefix `/api/intake`，tags `["产品录入"]`：
     - `GET  /api/intake/provider-status`
     - `POST /api/intake/upload`
     - `POST /api/intake/recognize`
     - `POST /api/intake/merge`
     - `GET  /api/intake/recent-logs?lines=100`
  4. 在 `backend/app/main.py` import 列表加 `intake`，并在 `app.include_router(...)` 段加 `app.include_router(intake.router)`。
  5. 新建 `backend/tests/test_intake.py`，写一个 `def test_smoke(): assert True` 占位（后续任务往里填）。
  6. 新建 `.env.example`（仓库根目录）含三行：
     ```
     DEEPSEEK_API_KEY=
     DEEPSEEK_BASE_URL=
     DEEPSEEK_VISION_MODEL=
     ```
  7. 编辑 `.gitignore`，在「数据目录」段后追加两行：
     - `data/intake_tmp/`
     - `data/catalog.yaml.bak.*`
- **Files**:
  - `backend/requirements.txt`（追加）
  - `backend/app/schemas_intake.py`（新建）
  - `backend/app/routers/intake.py`（新建）
  - `backend/app/main.py`（仅在路由注册段追加一行 + import 一行）
  - `backend/tests/test_intake.py`（新建，占位）
  - `.env.example`（新建）
  - `.gitignore`（追加）
- **Done when**:
  - `cd backend && python -m pytest tests/ -v` 全部通过（131 + 1 = 132）。
  - 后端可启动（`uvicorn app.main:app`）：`GET /api/intake/provider-status` 返回 200 + `{"ok": false, "error": "not implemented"}`。
  - `python -c "from backend.app.schemas_intake import UploadResponse, RecognizeResponse, MergeResponse"` 无异常。

---

### Task: T2 — 前端基础设施 (路由 + 菜单项 + api.intake stubs + Intake.tsx 框架)

- **Do**:
  1. 编辑 `frontend/src/App.tsx`：
     - import 段加 `import Intake from './pages/Intake';`
     - 在 `/products` 与 `/orders` 之间插入 `<Route path="/intake" element={<Intake />} />`。
  2. 编辑 `frontend/src/components/Layout.tsx`：
     - 在 `@ant-design/icons` import 中追加 `ScanOutlined`。
     - 在 `menuItems` 数组中（位于 `/products` 之后、`/orders` 之前）插入：`{ key: '/intake', icon: <ScanOutlined />, label: '产品录入' }`。
  3. 编辑 `frontend/src/api/client.ts`：
     - 在最后的 `export const api = { ... }` 对象内追加 `intake` 子对象（前面已展开过对象不可再展开 — 直接在 `api` 字面量末尾加键），按 `docs/design/design-intake.md §9.3 api/client.ts 扩展` 中的样例实现，包含 `providerStatus`、`upload`、`recognize`、`merge`、`recentLogs` 5 个方法。`upload` 必须用 `fetch` 直接拼 `multipart/form-data`（不能复用 `request<T>` 因为它强制 `Content-Type: application/json`）。
  4. 新建 `frontend/src/pages/Intake.tsx`，实现：
     - 状态机 type：`type IntakeMode = { kind: "upload" } | { kind: "recognizing"; abortController: AbortController } | { kind: "draft"; draft: Draft; conflicts: Conflict[] } | { kind: "color"; draft: Draft; variants: Variant[] } | { kind: "previewing"; finalDraft: FinalDraft } | { kind: "merging" } | { kind: "success"; stats: MergeStats; backupPath: string; timingMs: Record<string,number> } | { kind: "error"; errorKind: string; error: string };`（先放在本文件，后续 G3 再考虑拆出 types/）
     - 默认进入页面是 `{ kind: "upload" }`，调用 `api.intake.providerStatus()` 检测 `configured`，若 false 标记禁用态。
     - 顶部框架：`<h2>产品录入</h2>` + 右侧步骤指示器（① 上传截图 → ② 识别 → ③ 校对 → ④ 颜色 → ⑤ 合并），用当前 mode 决定哪一步高亮（upload → 1，recognizing → 2，draft → 3，color → 4，previewing/merging/success/error → 5）。下方一行灰色提示「拖入拓竹切片软件的截图…」。
     - 框架下方根据 `mode.kind` 切到对应子组件（暂用 placeholder 文本 `<div>upload mode placeholder</div>` 之类，CUJ 实现在后续任务里替换）。
     - 设置 `document.title` 副作用：跟随 mode 切换在 「产品录入」「产品录入 · 识别中」「产品录入 · 草稿校对」「产品录入 · 填写颜色」「产品录入 · 合并到 catalog」「产品录入 · 完成」「产品录入 · 合并失败」之间切。
     - 颜色 / 间距严格按照 `docs/ux/prd-005-intake/_shared.css` 中的 CSS 变量（`--primary: #1677ff`, `--orange: #fa8c16`, `--green: #52c41a`, `--red: #ff4d4f` 等）。不要硬编码色值。
  5. 新建 `frontend/src/pages/intake/` 子目录（空目录占位即可）— G2/G3 的子组件落在这里：`Upload.tsx`、`Recognizing.tsx`、`Draft.tsx`、`Color.tsx`、`Preview.tsx`、`Success.tsx`、`Error.tsx`。

- **Files**:
  - `frontend/src/App.tsx`（追加 import + 一行 Route）
  - `frontend/src/components/Layout.tsx`（追加 import + 一行 menu item）
  - `frontend/src/api/client.ts`（在 `api` 字面量末尾追加 `intake` 子对象）
  - `frontend/src/pages/Intake.tsx`（新建）
  - `frontend/src/pages/intake/.gitkeep`（新建空文件，建立子目录）
- **Done when**:
  - `cd frontend && npm run build` 通过（无 TypeScript 错误）。
  - 浏览器访问 `/intake` 能看到框架（标题 + 步骤指示器 + 灰色提示 + placeholder）。
  - 左侧菜单「产品录入」位于「产品目录」之后第 3 位（图标为 `ScanOutlined`），点击 URL 切到 `/intake` 并高亮。
- **Coordination**: 仅创建空目录 `intake/`，**不**写 Upload/Draft 等真正的 mode 子组件 — 这些归后续任务，避免冲突。

---

## Parallel Group 2（核心 CUJ-1 / CUJ-2 实现，依赖 G1）

> 全部 5 个 G2 任务编辑各自独立的文件。前后端文件无交集；前端 mode 子组件分别落在 `pages/intake/Upload.tsx`、`pages/intake/Recognizing.tsx`、`pages/intake/Error.tsx`，互不冲突。`pages/Intake.tsx` 由 G2 三个前端任务**共同**追加 `import` 与 `switch (mode.kind)` 分支 — 见「冲突风险」段的协调约定。

### Task: T3 — CUJ-1 后端：upload + provider-status + 启发式分类器

- **Do**:
  1. 新建 `backend/app/services/intake.py`，实现：
     - 常量 `INTAKE_TMP_DIR = Path("data/intake_tmp")`、`TTL_SECONDS = 3600`、`PRODUCE_PANEL_LUMINANCE_THRESHOLD = 80`、`PRODUCE_PANEL_REGION = (0.72, 0.02, 0.98, 0.30)`、`MAX_UPLOAD_BYTES = 10 * 1024 * 1024`。
     - `heuristic_classify(image_bytes: bytes) -> Literal["assembly", "produce"]`：按 `docs/design/design-intake.md §3 启发式分类器` 实现 — Pillow 打开转灰度 → 裁剪右上区域 → 算均值 → 阈值判定。
     - `cleanup_stale_sessions(now: float | None = None) -> int`：扫描 `INTAKE_TMP_DIR` 子目录，删除 `mtime + TTL_SECONDS < now` 的目录，返回清理数。
     - `save_uploaded_image(session_id: str, image_id: str, suffix: str, content: bytes) -> Path`：写到 `data/intake_tmp/<sid>/<iid>.<suffix>`。
  2. 新建 `backend/app/services/intake_llm.py`，实现 `LLMVisionProvider` 抽象、`LLMProviderError`、`DeepSeekVisionProvider` 占位（CUJ-2 任务 T5 会写完整实现 — 本任务先放 `is_configured()`、空 `recognize()` 抛 `NotImplementedError`），`_REGISTERED = [DeepSeekVisionProvider]`、`get_active_provider()`，按 `docs/design/design-intake.md §4 LLM Provider 抽象`。`is_configured()` 读 `os.environ.get("DEEPSEEK_API_KEY")` 非空即返回 True。
  3. 编辑 `backend/app/routers/intake.py`：把 T1 的 stub `GET /api/intake/provider-status` 实现为读 `get_active_provider()`，返回 `{ok: True, provider_name: "DeepSeek", configured: bool}`（即使 not configured 也 ok=True，因为这只是状态查询）。
  4. 编辑 `backend/app/routers/intake.py`：把 stub `POST /api/intake/upload` 实现为：
     - 入参：`files: list[UploadFile] = File(...)`、`session_id: Optional[str] = Form(None)`
     - 每次请求开头 `cleanup_stale_sessions()`
     - 若 `session_id` 缺失，生成 `uuid.uuid4().hex`
     - 对每个文件：校验 mime 类型（`image/png`、`image/jpeg`、`image/webp`）、校验 size ≤ 10MB（超限返回 `{ok: False, error: "..."}`）、生成 `image_id = uuid4.hex`、写盘、读 bytes 调 `heuristic_classify`，构造 `UploadedImage`
     - 返回 `UploadResponse(session_id=..., images=[...])`（外层包 `{ok: True, ...}`，等效于 schema 加 `ok` 字段 — 推荐做法：定义 `class UploadResponseWrapper(BaseModel): ok: bool; session_id: str; images: list[UploadedImage]`，或者直接返回 dict）。
  5. 在 `backend/tests/test_intake.py` 写：
     - `TestHeuristicClassifier`：用 `data/intake/床头柜/assembly/assembly.png`（assembly）+ `data/intake/床头柜/produce/*.png`（produce）真实样本，断言分类正确。再写 2 个合成边界用例（全白 → assembly；全黑 → produce）。
     - `TestUploadEndpoint`：FastAPI `TestClient`，上传一张 PNG（用 PIL 合成或读 fixture），断言响应 `ok=True` + `len(images)==1` + `suggested_class` 是 `"assembly"|"produce"` + `tmp` 文件存在。
     - `TestProviderStatus`：用 `monkeypatch.setenv("DEEPSEEK_API_KEY", "x")` 与 `monkeypatch.delenv(...)` 两条路径，断言 `configured` 值。
     - `TestCleanupStaleSessions`：手工建几个目录、`os.utime` 调早 mtime，调 `cleanup_stale_sessions(now=...)`，断言 stale 被删、fresh 保留。
- **Files**:
  - `backend/app/services/intake.py`（新建）
  - `backend/app/services/intake_llm.py`（新建）
  - `backend/app/routers/intake.py`（编辑：把 2 个 stub 实现成真）
  - `backend/app/schemas_intake.py`（如果需要包 `ok` 字段则微调）
  - `backend/tests/test_intake.py`（追加 4 个测试类）
- **Done when**:
  - `pytest backend/tests/test_intake.py -v` 全部新测试通过。
  - 用 curl / httpie 真实调 `POST /api/intake/upload`（multipart）能上传成功并返回分类。
- **Reference**: 业务规格 `docs/prd/prd-005-intake.md` CUJ-1 Edge Cases & Acceptance Criteria；设计文件 `docs/design/design-intake.md` §1（端点契约）、§3（启发式分类器）、§4（provider 抽象）、§6（临时文件目录）。

---

### Task: T4 — CUJ-1 前端：Intake 页 upload mode（两栏 + dropzone + 6 个 mock 变体）

- **Do**:
  1. 新建 `frontend/src/pages/intake/Upload.tsx`，实现 `UploadMode` 子组件：
     - props：`{ providerConfigured: boolean; productBaseName: string; onProductBaseNameChange: (v: string) => void; onProceedToRecognize: (sessionId: string, assemblyIds: string[], produceIds: string[]) => void }`
     - 内部 state：`assemblyImages: UploadedImage[]`、`produceImages: UploadedImage[]`、`sessionId: string | null`、`uploadingCount: number`、`totalCount: number`。
     - 渲染分支：
       - **no-api-key**：`providerConfigured === false` → 整页禁用，顶部红色 `<Alert type="error" message="未检测到 LLM 提供商 API key" description="请在项目根目录 .env 文件中配置 DEEPSEEK_API_KEY，参见 .env.example。配置后重启后端服务" />` + 主区遮罩。视觉对照 `docs/ux/prd-005-intake/cuj-1-no-api-key.html`。
       - **empty**：`assemblyImages.length === 0 && produceImages.length === 0 && uploadingCount === 0` → 大型 dropzone（虚线边框 `min-height: 360px`），上传插画 + 文案。视觉对照 `cuj-1-empty.html`。
       - **populated**：左右两栏（左淡蓝 `#f5faff` + 蓝点 `#1677ff`，右淡橙 `#fffaf0` + 橙点 `#fa8c16`），上方 mini dropzone。视觉对照 `cuj-1-initial.html` / `cuj-1-overflow.html`。
       - **uploading**：缩略图上 spinner 蒙层 + 顶部 mini dropzone 旁「上传中 X / Y」蓝字。视觉对照 `cuj-1-uploading.html`。
       - **one-empty**：一栏 0 张 + 另一栏 ≥ 1 → 空栏 placeholder + 「开始识别」按钮 disabled。视觉对照 `cuj-1-one-empty.html`。
     - 文件类型校验：拒绝非 `image/png|jpeg|webp`，`message.warning('仅支持图片文件（JPG / PNG / WebP）')`。
     - 拖拽：HTML5 DnD 拖动缩略图跨栏（拖动时另一栏 `border-color` 高亮）。
     - 上传调用 `api.intake.upload(files, sessionId)`；返回后 merge 到对应栏 state；`session_id` 落到本组件 state 且 lift 到父组件。
     - 「开始识别」按钮：`disabled={assemblyImages.length === 0 || produceImages.length === 0}`，点击调 `onProceedToRecognize(sessionId, assemblyIds, produceIds)`。
     - 产品基名输入框（顶部，placeholder「如：床头柜（识别后可自动推断填入，也可现在手填）」）。
  2. 编辑 `frontend/src/pages/Intake.tsx`：在 mode 切换 switch 里把 `mode.kind === "upload"` 分支替换为 `<UploadMode {...} />`；维护父组件的 `productBaseName` state（在 CUJ-2 / CUJ-3 之间持续传递）。
- **Files**:
  - `frontend/src/pages/intake/Upload.tsx`（新建）
  - `frontend/src/pages/Intake.tsx`（修改：替换 upload 分支 + import + 状态 lift）
- **Done when**:
  - `npm run build` 通过。
  - 浏览器视觉手测 6 个 mock 状态：初始空态 / 已分类 / 一栏空 / 上传中 / 无 api key / 多图溢出滚动。
  - 实际拖入 PNG 文件 → 调到后端 `/api/intake/upload` → 返回后缩略图落到正确栏。
- **Reference**: `docs/prd/prd-005-intake.md` CUJ-1 Acceptance Criteria；6 个 mock 文件 `docs/ux/prd-005-intake/cuj-1-*.html`；色板 `_shared.css`。

---

### Task: T5 — CUJ-2 后端：recognize 端点 + DeepSeek provider 完整实现

- **Do**:
  1. 编辑 `backend/app/services/intake_llm.py`，把 `DeepSeekVisionProvider.recognize()` 写完整：
     - 读 `DEEPSEEK_API_KEY` / 可选 `DEEPSEEK_BASE_URL`（默认 `https://api.deepseek.com`）/ 可选 `DEEPSEEK_VISION_MODEL`（默认 `deepseek-vl2-chat`，env 可覆盖）
     - 把图片转成 base64 data url
     - 构造 OpenAI-compatible chat completions request（messages 数组含 system + user with text+image_url[]），`response_format={"type": "json_object"}`、`max_tokens=4096`、`temperature=0.1`
     - 用 `httpx.Client(timeout=120)` 单次发请求
     - prompt 文本按 `docs/design/design-intake.md §5 LLM Prompt 与输出 schema` 硬编码（中文）
     - 解析响应：剥可能的 markdown 包裹 → `json.loads` → 构造 `LLMRawDraft` dataclass
     - 错误映射：401/403 → `LLMProviderError("http_401", ...)`；5xx → `http_5xx`；`httpx.TimeoutException` → `timeout`；JSON 解析失败 → `parse_failed` + raw_preview（200 字符截断）；schema 校验失败 → `schema_invalid`；DeepSeek 返回包含 "image_too_large" → `image_too_large`。
  2. 编辑 `backend/app/routers/intake.py`，把 stub `POST /api/intake/recognize` 实现为：
     - 入参：`RecognizeRequest`（含 session_id + image_ids 列表 + 可选 product_base_name）
     - 通过 `get_active_provider()` 获取 provider；若 None → `{ok: False, error_kind: "no_api_key", error: "..."}`
     - 从 `data/intake_tmp/<session_id>/<image_id>.*` 读所有 bytes
     - 构造 `RecognizeInput`，调 `provider.recognize(...)`，捕获 `LLMProviderError` 映射到响应
     - 把 `LLMRawDraft` 转换成 `Draft`：组件名拼 `<product_base_name>-<name>` 前缀、盘号默认 `<product_base_name>-<component_name>-<quantity_per_plate>`、`source_image_id = produce_image_ids[source_index]`、`plates[].component_name` 也加前缀
     - 在响应里**同时**做撞名检测（调 `services.intake.detect_conflicts(db, draft)`，按设计 §7），把 `conflicts: list[Conflict]` 一并返回 — 避免前端再发一次请求
     - 返回 `{ok: True, draft, conflicts: [...]}`
  3. 新建 `services.intake.detect_conflicts(db: Session, components: list[str], plates: list[str], products: list[str]) -> list[Conflict]`（设计 §7「撞名检测」段），同时用于 recognize 和 merge。
  4. 在 `tests/test_intake.py` 追加：
     - `TestDeepSeekProviderErrorMapping`：用 `httpx_mock` 或自写 stub，断言 401 → `http_401`、5xx → `http_5xx`、timeout → `timeout`、非 JSON → `parse_failed`。
     - `TestRecognizeEndpoint`：mock `DeepSeekVisionProvider.recognize` 返回预设 `LLMRawDraft`，调 `TestClient.post("/api/intake/recognize", json=...)`，断言响应里组件名带前缀（如「床头柜-侧板」）、盘号默认值正确、`source_image_id` 正确反查。
     - `TestRecognizeNoApiKey`：`monkeypatch.delenv("DEEPSEEK_API_KEY")`，断言 `ok=False`、`error_kind="no_api_key"`。
     - `TestDetectConflicts`：内存 SQLite 建几个 Component/PrintConfig/Product，喂草稿断言冲突项与现有名匹配。
- **Files**:
  - `backend/app/services/intake_llm.py`（完整实现 DeepSeek provider）
  - `backend/app/services/intake.py`（追加 detect_conflicts）
  - `backend/app/routers/intake.py`（recognize 端点）
  - `backend/tests/test_intake.py`（追加 4 个测试类）
- **Done when**:
  - `pytest backend/tests/test_intake.py -v` 全绿。
  - 集成测试覆盖：happy path + 4 个 error_kind + 撞名检测。
- **Reference**: 设计 §1（端点）、§4（provider）、§5（prompt + schema）、§7「撞名检测」段。PRD CUJ-2 Acceptance Criteria。

---

### Task: T6 — CUJ-2 前端：识别中进度页 + 错误页

- **Do**:
  1. 新建 `frontend/src/pages/intake/Recognizing.tsx`：
     - props：`{ assemblyCount: number; produceCount: number; productBaseName: string; onCancel: () => void; onSuccess: (draft: Draft, conflicts: Conflict[]) => void; onError: (errorKind: string, error: string, rawPreview?: string) => void; sessionId: string; assemblyImageIds: string[]; produceImageIds: string[] }`
     - `useEffect` 启动：创建 `AbortController`，setTimeout 90 秒后 `controller.abort()`（与服务端 120s 超时对应）；`api.intake.recognize({...}, controller.signal)`；成功 → `onSuccess`；失败 → `onError`。
     - 视觉：中央大卡片（白底圆角 8px、阴影），主标题「正在识别 N 张图片…」、副标题「完成后会自动跳转到草稿预览页」、元信息行（产品基名 / assembly 张数 / produce 张数 / 等宽字体）、三阶段灯水平排列（① 上传图片 ✓ → ② 调用 LLM 识别 pulse → ③ 解析返回数据）、蓝色线性渐变进度条（从 30% 渐进到 95%）+ 「约 30 秒，请稍候…」、中央「取消」secondary 按钮、底部 tip。视觉对照 `docs/ux/prd-005-intake/cuj-2-initial.html`。
     - 「取消」点击：`controller.abort()` + `onCancel()`。
  2. 新建 `frontend/src/pages/intake/Error.tsx`（CUJ-2 错误页与 CUJ-5 错误页有共同结构 — 本任务先做 CUJ-2 错误，CUJ-5 错误在 T10 复用或扩展）：
     - props：`{ errorKind: string; error: string; rawPreview?: string; onRetry: () => void; onBack: () => void }`
     - 视觉：中央卡片，红色 `!` 图标 + 标题「LLM 识别失败」+ 描述「已上传的图片仍保留在上一步，可调整后重试」+ 等宽字体（`font-family: monospace`）的错误详情块（包含 errorKind 映射的中文措辞 + 原始 error 字符串 + 可选 rawPreview）。底部「返回上一步」 secondary + 「重试」 primary。视觉对照 `docs/ux/prd-005-intake/cuj-2-error.html`。
     - errorKind → 中文措辞映射：`http_401 → "HTTP 401 Unauthorized — DeepSeek 拒绝请求，可能是 API key 无效或已用尽额度"`、`timeout → "连接超时 — 90 秒未收到响应，请检查网络"`、`parse_failed → "响应解析失败 — 返回内容不是预期的 JSON 结构"`、`http_5xx → "DeepSeek 服务暂时不可用，请稍后重试"`、`image_too_large → "图片过大 — 单张图超过 LLM 接受的最大尺寸，请缩小后重试"`、`no_api_key → "未检测到 LLM 提供商 API key"`、其它 → 原 error 字符串。
  3. 编辑 `frontend/src/pages/Intake.tsx`：在 mode switch 加 `recognizing` 与 `error` 分支，分别渲染 `<RecognizingMode>` 与 `<ErrorMode>`。把 「取消」回调实现为 `setMode({kind: "upload"})`（保留 assembly/produce/baseName state）；「成功」回调 `setMode({kind: "draft", draft, conflicts})`。
- **Files**:
  - `frontend/src/pages/intake/Recognizing.tsx`（新建）
  - `frontend/src/pages/intake/Error.tsx`（新建）
  - `frontend/src/pages/Intake.tsx`（追加 2 个 mode 分支）
- **Done when**:
  - `npm run build` 通过。
  - 浏览器手测：触发识别 → 进度页可见 → 取消能退回 upload → 错误页能渲染。
- **Reference**: PRD CUJ-2 Acceptance Criteria；mocks `cuj-2-initial.html` / `cuj-2-error.html`。

---

## Parallel Group 3（CUJ-3 / CUJ-4 / CUJ-5 终段实现，依赖 G2）

> 全部任务编辑独立文件（`pages/intake/Draft.tsx`、`pages/intake/Color.tsx`、`pages/intake/Preview.tsx`、`pages/intake/Success.tsx`、`backend/app/services/intake.py` 仅追加新函数）。Intake.tsx 由 3 个前端任务追加 mode 分支 — 见冲突段。

### Task: T7 — CUJ-3 前端：草稿校对页（BOM + 打印盘 + 撞名）

- **Do**:
  1. 新建 `frontend/src/pages/intake/Draft.tsx`，渲染草稿校对页：
     - props：`{ draft: Draft; conflicts: Conflict[]; onBack: () => void; onProceedToColor: (editedDraft: Draft) => void }`
     - 顶部产品基名 input（初值 `draft.product_base_name`，编辑时同步未被用户手改过的组件名 / 盘号前缀 — 维护 `dirty: Map<string, boolean>` 标记每个字段是否手改过）。
     - 撞名 alert：若 `conflicts.length > 0`，顶部红色 `<Alert>` 「检测到 N 处与现有目录重名 — 请改名或确认合并」。撞名行整体 `background: var(--red-soft)`、对应 input 红边、行内右侧红字「目录中已存在同名『XXX』」。改名解除即时清除。
     - 「组件清单 (BOM)」卡片：卡片标题 + 下方一行 assembly 缩略图横排（64×48px，hover `🔍` 放大镜，点击右侧 `<Drawer>` 显示大图）+ 表格只两列「组件名」（text input）+「装配件数」（number input，**蓝色高亮**：`border-color: #bfdfff; background: #fafdff; font-weight: 600; color: #1677ff`）+ 表格下方虚线按钮「+ 增加组件」。
     - 「打印盘清单」卡片：表格 5 列「盘号」（text input）+「所属组件」（AntD `Select`，选项与 BOM 表组件名同步）+「单盘件数」（number input，蓝色高亮）+「耗时」（text input，蓝色高亮，正则 `^(\d+h)?(\d+m)?(\d+s)?$` 校验，不合法红边 + tooltip「格式应为 `2h43m` 或 `17m45s`」）+「原图复核」（眼睛 `👁` icon button，点击右侧滑出 `<Drawer width={480}>` 显示该盘原图 + LLM 识别的件数/耗时 + 件数 input + 「应用到本行」「取消」按钮）。
     - 表格下方虚线按钮「+ 增加打印盘」。
     - 即时校验：耗时格式 / 件数 ≤ 0 / 盘号在草稿内重复 → 对应 input 红边 + tooltip。
     - 底部「← 上一步：调整截图」（secondary）+ 「下一步：填写颜色 →」（primary）。primary 在以下任一条件下 disabled：撞名未解决（任何 input 红边状态尚存）、BOM 为空、关键字段非法。
     - 视觉对照 `cuj-3-initial.html`（无撞名）/ `cuj-3-edit-quantity.html`（drawer 展开）/ `cuj-3-conflict.html`（撞名红化）。
  2. 编辑 `frontend/src/pages/Intake.tsx`：mode switch 追加 `draft` 分支渲染 `<DraftMode>`。
- **Files**:
  - `frontend/src/pages/intake/Draft.tsx`（新建）
  - `frontend/src/pages/Intake.tsx`（追加一个 mode 分支）
- **Done when**:
  - `npm run build` 通过。
  - 浏览器手测：模拟 draft 数据 + 模拟撞名 → 视觉与 3 个 mock 一致 → 即时校验工作。
- **Reference**: PRD CUJ-3 Acceptance Criteria；mocks `cuj-3-*.html`；色板 `_shared.css`。

---

### Task: T8 — CUJ-4 前端：颜色矩阵 + 多配色变体

- **Do**:
  1. 新建 `frontend/src/pages/intake/Color.tsx`，渲染颜色矩阵：
     - props：`{ draft: Draft; onBack: () => void; onProceedToPreview: (finalDraft: FinalDraft) => void }`
     - 状态：`variants: Variant[]`（初始 `[{ variant_name: \`${draft.product_base_name} - 配色 1\`, color_cells: components.map(c => ({component_name: c.name, color: ""})) }]`）。
     - 矩阵列结构（左→右）：「组件名」（来自 BOM，文本）+「件数」（`×${assembly_quantity}` 灰色小字）+ N 列变体 +「+ 新增配色」（rowspan 跨表）。
     - 每列变体头：变体名 input（可改、空 blur 自动回填默认）+「⎘ 复制此列」（克隆该列、所有 cell 预填克隆值，追加到右侧）+「× 删除此变体」（仅 1 列时隐藏）。
     - 每个矩阵单元格 color cell：左 16px 色块 swatch（按 `colorNameToSwatch(color)` 映射，未知色用斜纹 45° 灰白条纹）+ 中间色名 + 右 `▾` 箭头。
     - 点击 cell 弹 popover（宽 320px、cell 下方）三段：
       - ① **本产品已用过的颜色**：横排 chip（变体所有色名 dedupe 后），空时隐藏
       - ② **常用颜色**：固定 11 个 chip — 白 / 黑 / 灰 / 棕 / 粉 / 红 / 黄 / 蓝 / 绿 / 橙 / 紫（色块用对应实际色值，参考 `_shared.css`）
       - ③ **输入新颜色名**：text input + 「添加」secondary 按钮
     - 点选 chip → 应用到当前 cell、关闭 popover；第 3 段「添加」→ 应用 + 加入「已用过」+ 关闭。
     - 底部「可选颜色」汇总条：所有变体所有 cell 色名 dedupe，删除变体时实时重算。
     - 变体名重复时第二个 input 红边 + tooltip「变体名不可重复 — 合并后会撞 catalog 主键」。
     - 「下一步：合并 N 个产品条目 →」按钮文案动态显示变体数；disabled 直到所有变体所有 cell 都填齐。
     - 视觉对照 `cuj-4-initial.html`（1 列空）、`cuj-4-multi-variant.html`（3 列填好）、`cuj-4-add-color.html`（popover 展开）。
  2. 编辑 `frontend/src/pages/Intake.tsx`：mode switch 追加 `color` 分支。「下一步」回调构造 `FinalDraft` 并 `setMode({kind: "previewing", finalDraft})`。
- **Files**:
  - `frontend/src/pages/intake/Color.tsx`（新建）
  - `frontend/src/pages/Intake.tsx`（追加一个 mode 分支）
- **Done when**:
  - `npm run build` 通过。
  - 浏览器手测：1 列变体 → 复制变体 → 改色 → 新增变体 → 删变体 → 汇总条更新。
- **Reference**: PRD CUJ-4 Acceptance Criteria；mocks `cuj-4-*.html`。

---

### Task: T9 — CUJ-5 后端：merge 端点（5 阶段事务 + 回滚）

- **Do**:
  1. 在 `backend/app/services/intake.py` 追加以下函数：
     - `expand_to_yaml_structures(final_draft: FinalDraft) -> tuple[list[dict], list[dict], list[dict]]`：把 FinalDraft 展开为 `(组件列表, 打印盘列表, 产品列表)` 三个 list of dict，按 `docs/design/design-intake.md §8 颜色矩阵 → catalog.yaml 展开映射`。组件的 `可选颜色` = 该组件在所有变体 cell 中出现过的色名 dedupe；产品每个变体一条，BOM 用对应 cell 颜色。
     - `backup_catalog(catalog_path: Path, timestamp: str) -> Path`：`shutil.copy2` 到 `catalog.yaml.bak.<timestamp>` 并返回备份路径。
     - `append_to_catalog(catalog_path: Path, new_components, new_plates, new_products) -> None`：parse → setdefault + extend → safe_dump（`allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096`）。
     - `rollback_from_backup(catalog_path: Path, backup_path: Path) -> None`：`shutil.copy2(backup_path, catalog_path)`，**不删** bak。
     - `do_merge(db: Session, final_draft: FinalDraft, catalog_path: Path) -> MergeResponse`：实现 §7 的 5 阶段流水 — ① 撞名兜底（用 detect_conflicts，有冲突直接返回 `{ok: False, error_kind: "conflict", details}`）→ ② backup（失败 `backup_failed`）→ ③ append + 复读 safe_load 验证（失败 rollback + `yaml_invalid` 或 `write_failed`）→ ④ `load_catalog(SessionLocal())`（失败 rollback + `load_failed`）→ ⑤ 清理 `data/intake_tmp/<session_id>` 目录 → 返回 `{ok: True, stats, backup_path, timing_ms: {"写入": X, "重新加载": Y}}`。计时用 `time.perf_counter()`。
  2. 在 `backend/app/routers/intake.py`：
     - 把 stub `POST /api/intake/merge` 实现为接 `{draft: FinalDraft, session_id: str}`、调 `services.intake.do_merge`、返回响应。
     - 把 stub `GET /api/intake/recent-logs?lines=100` 实现：进程级 `deque(maxlen=500)` 环形缓冲（在 main.py 启动期 monkeypatch sys.stdout 把 `print` 双路写入 buffer — 或简化为 deque + 自定义 `intake_log()` 函数同时 `print` 与 append；MVP 简化方案可接受）。端点返回最近 N 行。
  3. 在 `tests/test_intake.py` 追加：
     - `TestColorMatrixExpansion`：构造 3 变体 × 4 组件，断言生成的 `组件.可选颜色` = union dedupe、`产品` = 3 条、每条 BOM = 4 行 + 对应颜色。
     - `TestAppendToCatalog`：tmpdir 准备一个 `catalog.yaml` fixture（最小合法 YAML），调 `append_to_catalog`，断言文件 round-trip 后仍是合法 YAML 且含新条目。
     - `TestMergeRollback`：tmpdir + in-memory SQLite + mock `load_catalog` 抛 `ValueError`，调 `do_merge`，断言 `catalog.yaml` 内容与备份一致、`bak` 文件存在、`rolled_back=True`。
     - `TestMergeSuccess`：tmpdir + in-memory SQLite + 真实 `load_catalog`，调 `do_merge`（draft 数据无冲突），断言 `ok=True`、DB 有新 Component 行、tmp 目录被清空、bak 文件存在。
     - `TestMergeConflict`：DB 预置一个 `Component(name="床头柜-侧板")`，draft 草稿含同名，断言 `ok=False`、`error_kind="conflict"`、`catalog.yaml` 未被触碰（也无 bak 产生）。
- **Files**:
  - `backend/app/services/intake.py`（追加 5 个函数）
  - `backend/app/routers/intake.py`（merge + recent-logs 端点）
  - `backend/tests/test_intake.py`（追加 5 个测试类）
- **Done when**:
  - `pytest backend/tests/test_intake.py -v` 全绿。
  - 集成测试覆盖：success + conflict + 4 个回滚场景（backup_failed / write_failed / yaml_invalid / load_failed），每个回滚后断言 catalog.yaml 内容 == backup 内容。
- **Reference**: 设计 §1（端点）、§7（merge 关键事务）、§8（YAML 展开映射）、§6 后半段（recent-logs）；PRD CUJ-5 Acceptance Criteria。

---

### Task: T10 — CUJ-5 前端：合并预览页 + 成功页 + 失败页

- **Do**:
  1. 新建 `frontend/src/pages/intake/Preview.tsx`：
     - props：`{ finalDraft: FinalDraft; sessionId: string; onBack: () => void; onMerging: () => void; onSuccess: (stats, backupPath, timingMs) => void; onError: (errorKind: string, error: string) => void }`
     - 「合并摘要」卡片：3 行 — ① 「N 个新组件」+ 名称列表 ② 「M 张新打印盘」+ 总耗时合计（按 `duration_minutes` 求和、格式化为 `XhYm`） ③ 「K 个新产品变体」+ 变体名列表。下方灰底说明条「合并前会自动备份到 `catalog.yaml.bak.<时间戳>`…」。
     - 「YAML 预览」卡片：暗黑代码块（`background: #1e1e1e; color: #d4d4d4; border-radius: 4px; max-height: 520px; overflow-y: auto; font-family: monospace`），用同样的 expand 逻辑生成 YAML 字符串（前端用 `yaml`/`js-yaml` 库 或手写一个简化 YAML serializer — 推荐手写避免引入新依赖，因为只 dump 已知结构）；首行注释 `# --- <产品基名> 系列，由产品录入工具于 YYYY-MM-DD HH:MM:SS 追加 ---`。syntax highlighting（键 `#9cdcfe` 蓝 / 字符串 `#ce9178` 橙 / 数字 `#b5cea8` 绿 / 注释 `#6a9955` 绿斜体）— 用简单正则 + `<span>` 包裹。视觉对照 `cuj-5-initial.html`。
     - 底部「← 上一步：填写颜色」+ 「确认合并并重新加载 →」（带绿勾 svg 图标）。点击 primary → `onMerging()` → 调 `api.intake.merge({draft: finalDraft, session_id: sessionId})` → 成功 / 失败回调。
  2. 新建 `frontend/src/pages/intake/Success.tsx`：
     - props：`{ stats: MergeStats; backupPath: string; timingMs: Record<string,number>; onContinue: () => void; onGotoProducts: () => void }`
     - 视觉：中央大卡片，绿色 ✓ 图标 + 标题「合并成功」+ 描述（追加的组件/盘/变体数）+ 等宽字体显示 backup_path 与「写入 X ms · 重新加载 Y ms」。底部「继续录入下一个产品」（secondary，调 `onContinue` → 父组件重置全部 state 到 upload）+「前往产品目录查看 →」（primary，`navigate('/products')`）。视觉对照 `cuj-5-success.html`。
  3. 扩展 `frontend/src/pages/intake/Error.tsx`（T6 已建）：新增 prop `variant: "recognize" | "merge"`，merge 变体下标题改为「合并失败 — 已自动回滚」，描述按 `errorKind` 变化（`conflict / backup_failed / write_failed / yaml_invalid / load_failed`），4 行错误详情块（错误类型 / 错误信息 / 已回滚至 / 建议）。底部按钮「查看后端日志」（secondary，点击弹 `<Modal>` 含 `<textarea readonly>` 显示 `api.intake.recentLogs()` 返回）+「返回上一步调整」（primary，回 CUJ-4）。视觉对照 `cuj-5-error.html`。
  4. 编辑 `frontend/src/pages/Intake.tsx`：mode switch 追加 `previewing` / `merging` / `success` 三个分支（`error` 分支已有，调用时传 `variant`）。
- **Files**:
  - `frontend/src/pages/intake/Preview.tsx`（新建）
  - `frontend/src/pages/intake/Success.tsx`（新建）
  - `frontend/src/pages/intake/Error.tsx`（扩展，T6 已建）
  - `frontend/src/pages/Intake.tsx`（追加 3 个 mode 分支）
- **Done when**:
  - `npm run build` 通过。
  - 浏览器手测：预览页 → 点确认 → 成功页 → 跳到 /products 看到新产品；或失败页 → 看日志 → 返回。
- **Reference**: PRD CUJ-5 Acceptance Criteria；mocks `cuj-5-*.html`。

---

## Parallel Group 4（端到端 + 回归验收，串行依赖 G3）

### Task: T11 — 端到端集成冒烟测试

- **Do**:
  1. 在 `backend/tests/test_intake.py` 追加 `TestEndToEndIntakeFlow`：
     - tmpdir 准备一个最小合法 `catalog.yaml`（含 `组件: []`、`打印盘: []`、`产品: []`）
     - in-memory SQLite + 在 lifespan 外直接 `Base.metadata.create_all`
     - 用 FastAPI `TestClient` 完成完整流程：
       - `POST /api/intake/upload`（multipart，用 PIL 合成一张「assembly 风格全白图」 + 一张「produce 风格右上深色图」）→ 拿 session_id + image_ids
       - mock `DeepSeekVisionProvider.recognize` 返回预设 `LLMRawDraft`（含 2 个组件 + 3 张盘）
       - `POST /api/intake/recognize` → 拿 draft
       - 用户态：构造 `FinalDraft`（2 变体）
       - `POST /api/intake/merge` → 断言 `ok=True`
     - 断言：DB 新建了 2 个 Component / 3 个 PrintConfig / 2 个 Product 行；`tmp_dir / session_id` 已被删除；备份文件存在；catalog.yaml 内容含新条目。
- **Files**:
  - `backend/tests/test_intake.py`（追加 1 个测试类）
- **Done when**:
  - `pytest backend/tests/test_intake.py::TestEndToEndIntakeFlow -v` 通过。

---

### Task: T12 — 全量后端测试套件回归

- **Do**:
  1. 运行 `cd backend && python -m pytest tests/ -v`
  2. 断言：iter2 基线 131 个测试 + T1~T11 新加测试**全部通过**，无任何失败 / skipped 异常。
  3. 若发现任何回归，立刻报告并修复（**不允许**通过删测试或 skip 测试规避）。
- **Files**:
  - 任何被回归触发需要修复的源文件。
- **Done when**:
  - `pytest` 全绿；总数应在 131 + 大约 15~25 = 145~155 范围（具体数视 G2/G3 测试粒度）。

---

### Task: T13 — 前端 TypeScript 构建回归

- **Do**:
  1. 运行 `cd frontend && npm run build`
  2. 断言：清洁 TypeScript 编译，零 error，零 warning（warning 可保留但需要在 PR 说明里 list）。
  3. 检查 `dist/` 产出存在。
- **Files**:
  - 任何 TS 编译错误需要修复的源文件。
- **Done when**:
  - `npm run build` 退出码 0、`dist/index.html` 存在。

---

## Conflict Risks

- **`frontend/src/pages/Intake.tsx` 被多个任务追加 mode 分支**（T4 / T6 / T7 / T8 / T10）— 这是最大的合并冲突点。**协调约定**：
  - 每个任务只追加自己负责的 `case "..."` 分支，**不修改**其它分支。
  - 每个任务都在文件顶部 import 区按字母序追加自己的子组件 import。
  - merge 顺序按 T4 → T6 → T7 → T8 → T10 进行；若 worktree 撞 case 顺序，rebase 时手工合并 switch 分支即可（每个分支都是独立 case，文本冲突少）。
- **`backend/app/routers/intake.py` 被多个后端任务编辑**（T3 / T5 / T9）— 每个任务实现自己的端点函数，**不动**其它端点。注意保持 import 区顶部一致（按 G2 加 → G3 加的顺序追加 `from .schemas_intake import ...`）。
- **`backend/app/services/intake.py` 被 T3 / T5 / T9 追加新函数** — 各自独立函数，**无共享可变状态**，按追加顺序 merge 即可。
- **`backend/tests/test_intake.py` 由 T3 / T5 / T9 / T11 各自追加 `TestXxx` 类** — 文件追加无冲突。
- **`backend/app/schemas_intake.py` 由 T1 一次性写完** — G2 / G3 任务不应再加 schema，必要时在 PR 评论里向 planner 反馈再统一加。
- **`.env.example` / `.gitignore` 由 T1 一次性写完** — 其它任务不要碰。
- **`docs/ux/prd-005-intake/_shared.css` 是只读 reference** — 前端任务只读取其中的色板 / 间距值，不修改该文件。

## Efficiency Estimate

- 串行执行约 13 个任务，假设每个 1 单位时间 → 13 单位。
- 并行执行：G1 = max(T1, T2) = 1，G2 = max(T3, T4, T5, T6) = 1，G3 = max(T7, T8, T9, T10) = 1，G4 = T11 + T12 + T13 = 3（强制串行）。总 = 6 单位。
- **节约约 7 / 13 ≈ 54% 时钟时间**。G2 / G3 的并行度受文件粒度保证，理论上可同时跑 4 个 agent。

---

## QA-fix tasks (iter3 QA gate produced 2 actionable findings ≥ MEDIUM)

- [x] **QA-fix [HIGH][BUG]**: 把 `Upload.tsx` 内 `sessionId / assemblyImages / produceImages / uploadingCount / totalCount` state 全部提升到父组件 `Intake.tsx`，作为 props 传给 `UploadMode`。修复后从 `recognizing → onCancel` / `error → onBack(recognize)` / `draft → onBack` 三处回退 upload 时，所有已上传图片与 sessionId 保留不丢。验证：在 Playwright 中上传 N 张图、触发识别错误、点「返回上一步」断言图片张数与 sessionId 仍存在。— 影响文件 `frontend/src/pages/Intake.tsx:192/226/355` 与 `frontend/src/pages/intake/Upload.tsx`。— source: qa-report.md 2026-06-14 16:40:20 (UTC+8) — **CLOSED iter3 retry 1 by commit `1eee605`，验证 Scenarios A/B 通过**

- [x] **QA-fix [MEDIUM][BUG]**: 修 `Intake.tsx::stepIndex` 的 `error` 分支按 `variant` 区分：`recognize → return 1`、`merge → return 4`。验证：识别失败时步骤指示器在 ② 步高亮；合并失败时在 ⑤ 步高亮。— 影响文件 `frontend/src/pages/Intake.tsx:96-112`。— source: qa-report.md 2026-06-14 16:40:20 (UTC+8) — **CLOSED iter3 retry 1 by commit `1eee605`，验证 Scenario C 通过**

## QA-fix tasks (iter3 Retry 1 QA gate 新增)

- [x] **QA-fix [MEDIUM][BUG]**: recognize 页「取消」按钮被 `.catch` 分支误翻译为 timeout 错误页，应直接退回 upload。修复方案：在 `handleCancel` 设 `cancelledByUserRef.current = true`，`.catch` 里 `if (cancelledByUserRef.current) return;` 提前 return（约 3 行）。状态保留是正确的（HIGH 修复有效，图片不丢），但 UX 路径错（多一跳假错误页让人困惑）— 违反 PRD CUJ-2 AC #4「点击后主区退回 CUJ-1，所有图与产品基名完整保留」。— 影响文件 `frontend/src/pages/intake/Recognizing.tsx:71-93`。— source: qa-report.md 2026-06-14 20:08:23 (UTC+8) — **CLOSED iter3 retry 2 by commit `558849d`，cancel 后 DOM 无任何「错误」/「连接超时」文案，title 直接回「产品录入」，3 张图与产品基名保留**

LOW-severity 残留（不强制本轮修复，但建议）：

- AntD deprecation 警告：`Alert.message → title`、`Drawer.width → size`、`Statistic.valueStyle → styles.content`、`Spin.tip → description`（iter3 retry 1 新增 Spin.tip） — 全 grep 替换。
- CUJ-1 empty-state 副文案与 mock 微差（实现 "支持 JPG / PNG / WebP，单张 ≤ 10MB" / mock "支持一次拖入多张...系统会自动归类"）— PRD AC 未约束，可不修。
- 后续补 Playwright E2E 覆盖 QA 报告中 9 类 manual-NOT_RUN 场景（识别中态、Drawer、撞名、各种校验等）+ iter3 retry 1 验证过的 3 个 fix 场景（Scenario A/B/C），避免下次回归还要手测。

Caveats（非 bug，PM review 知情）：

- MergeStats Pydantic schema 声明中文键 vs `do_merge` 返回英文键不一致 — FastAPI `/api/intake/merge` 端点**未设置 `response_model`**，所以 Pydantic 不校验 response；前端 Success.tsx 读英文键正常显示。端到端可用但 schema-impl drift，建议未来对齐（schema 改英文，或 do_merge 改中文键，并显式设 `response_model=MergeResponse`）。
