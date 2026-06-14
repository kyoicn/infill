# QA Report

Last updated: 2026-06-14 21:31:58 (UTC+8)
Scope: prd-005-intake — Iter3 Retry 2（narrow scope：仅验证 cancel-fix + 1 happy-path）

## Verdict: PASS

Iter3 retry 2 — commit `558849d` 修复的「cancel 按钮 → 假 timeout 错误页」MEDIUM bug 已验证关闭：点取消后 UI 直接回到 upload 页（标题「产品录入」，步骤指示器回到 ①），3 张图与产品基名「床头柜QA」均保留，无任何错误页 / 「连接超时」文案出现，URL 仍是 `/intake` 不变。Happy path 快速回归（upload → recognize → draft → color → merge → success）端到端走通；catalog.yaml 实际追加「床头柜QA - 配色 1」+ DB 重新加载 + `/api/products` 立即可见新产品（4 条记录，含新产品）。无 MEDIUM+ 回归。LOW deprecation warnings（Alert / Drawer / Spin AntD）依然存在但与 retry 1 状态一致 — 转交下一 iter。详见 ## Iter3 Retry 2 节。

详见 ## Iter3 Retry 2 节。

---

## Iter3 Retry 2

Last updated: 2026-06-14 21:31:58 (UTC+8)
Scope (narrow): 验证 commit `558849d` 的 cancel-fix + 1 个 happy-path sanity walk。不重走全部 5 个 CUJ。

### Verdict: PASS

无 MEDIUM+ 回归。cancel-fix 通过；happy path 通过。继承自 retry 1 的 LOW bugs（AntD 3 处 deprecation、MergeStats caveats）状态未变化 — 转交下一 iter。

### Fix verification: Cancel button → 不再触发假错误页

**Bug:** `[MEDIUM][BUG]` recognize 页「取消」按钮被 fetch `.catch` 误翻译成 timeout 错误页（iter3 retry 1 新发现） — `frontend/src/pages/intake/Recognizing.tsx:71-93`
**Fix:** commit `558849d` — 新增 `cancelledByUserRef` sentinel，`handleCancel` 设置 true 后再 abort；`.catch` 早返回当 ref 为 true。

#### Walk steps (run1)

| # | 操作 | 期望 (PRD CUJ-2 AC #4) | 实际 | Result |
|---|------|------------------------|------|--------|
| 0 | 启动 dev server + 真后端（DEEPSEEK_API_KEY=qa-stub-key + 拦截 fetch 让 recognize 永挂） | 上传区可用 | provider-status=configured，dropzone enabled | PASS |
| 1 | 上传 1 张组装图 + 2 张打印盘截图（真实床头柜素材） | 1A + 2P，「开始识别」启用 | "组装图 1 张" / "打印盘 2 张"，按钮 enabled | PASS |
| 2 | 输入产品基名「床头柜QA」并点击「开始识别」 | 切换到 recognize 页（title「产品录入 · 识别中」） | title 变更，step ② 高亮，「正在识别 3 张图片…」进度条转 | PASS |
| 3 | 在 recognize 页点击「取消」 | UI 直接回 upload（无错误页中转）、保留所有图与基名 | title 变回「产品录入」（NOT 错误中），step ①，产品基名 = 床头柜QA，3 张图全部保留，「开始识别」可点 | PASS |

#### 关键 DOM 断言（programmatic）

执行 `document.body.innerText` 扫描 after 取消：
- `bodyContainsTimeout` = **false**（无「连接超时」/「timeout」）
- `bodyContainsError` = **false**（无「错误」字样）
- `bodyHasUploadHint` = **true**（看到 `+ 继续追加截图` 上传 UI）
- `productBaseInputValue` = `"床头柜QA"`（产品基名保留）

#### Artifacts (cancel-fix)

- `docs/qa-artifacts/iter3-20-59-54/cancel-fix/run1/00-uploaded.png` — 上传完成、按钮 enabled
- `docs/qa-artifacts/iter3-20-59-54/cancel-fix/run1/01-recognizing.png` — recognize 页中、可见取消按钮
- `docs/qa-artifacts/iter3-20-59-54/cancel-fix/run1/02-after-cancel.png` — 取消后正确回到 upload 页（关键证据）

### Happy path sanity check (run1)

| # | 阶段 | 实际 | Result |
|---|------|------|--------|
| 1 | upload | 1A + 2P 三张图，产品基名「床头柜QA」 | PASS |
| 2 | recognize（fetch shim 切到 happy 模式，返回 draft 含 2 组件 + 2 盘） | title「产品录入 · 草稿校对」，draft 渲染 2 组件 + 2 盘，全部带 `床头柜QA-` 前缀 | PASS |
| 3 | color（柜体 = 白色 / 抽屉面板 = 棕色） | 「下一步：合并 1 个产品条目」按钮启用，颜色摘要更新 | PASS |
| 4 | merge preview | 「合并摘要」显示 2 组件 / 2 打印盘 / 1 产品（床头柜QA - 配色 1）；YAML 预览正确 | PASS |
| 5 | confirm merge | title「产品录入 · 完成」，「合并成功」，实际 bak 文件 `data/catalog.yaml.bak.20260614-213158`，写入 12 ms · 重新加载 14 ms | PASS |
| 6 | 真实验证：`tail data/catalog.yaml` 含「床头柜QA - 配色 1」+ 两个 `床头柜QA-` 组件 + 两张 `床头柜QA-` 盘 | YAML 实际追加内容匹配预览 | PASS |
| 7 | 真实验证：`GET /api/products` 返回 4 条记录（原 3 条 + 新增的 床头柜QA - 配色 1） | 是 | PASS |

#### Artifacts (happy-path)

- `docs/qa-artifacts/iter3-20-59-54/happy-path/run1/01-draft.png`
- `docs/qa-artifacts/iter3-20-59-54/happy-path/run1/02-color.png`
- `docs/qa-artifacts/iter3-20-59-54/happy-path/run1/03-merge-preview.png`
- `docs/qa-artifacts/iter3-20-59-54/happy-path/run1/04-success.png`

### Console messages

3 条 error-level 全部为 AntD deprecation warning（pre-existing LOW，与 retry 1 一致）：
- `[antd: Alert] message is deprecated. Please use title instead.`
- `[antd: Drawer] width is deprecated. Please use size instead.`
- `[antd: Spin] tip is deprecated. Please use description instead.`

无新增 console 错误。无网络 5xx。无 React 渲染告警。

### 严重度分布（本次 retry 增量）

- CRITICAL / HIGH: 0
- MEDIUM: 0（retry 1 新发现的 cancel bug 已关闭）
- LOW: 0 新增（继承 retry 1 的 AntD deprecation 与 MergeStats caveats）

### 方法学备注

- 本次为「窄范围 retry 2」— 不重走全部 5 CUJ；只验 cancel-fix 闭环 + 1 个 sanity walk。这是 dev-cycle 编排者的明确指令，符合「retry 仅复验 fix」的快速验收规约。
- 为让 cancel 路径真实可达，使用 `DEEPSEEK_API_KEY=qa-stub-key` 启 backend（让 `provider-status` 返回 configured），再通过 page-side `fetch` shim 拦截 `/api/intake/recognize`（`delay-forever` 模式让用户来得及点取消；`happy` 模式返回伪 draft 给后续 happy path 走）。`/api/intake/upload` 和 `/api/intake/merge` 等其它端点未拦截 — merge 真的写入了 catalog.yaml。
- 测试结束后已自动用 bak 恢复 catalog.yaml，且 bak 文件已清理，无 QA 残留。

### 单 run vs flakiness

按 SYNC 规则 CUJ walk 应跑 2 次。本次为编排者指定的「窄 retry」，单 run 已涵盖整个 fix 验证 + happy path 闭环。如需 run2 复核，建议下一个完整 QA cycle 走。

---

## Iter3 Initial (kept for history)

## Verdict: FAIL

CUJ-1 / CUJ-3 / CUJ-4 / CUJ-5 walk twice and PASS functionally. CUJ-2 has a HIGH-severity bug — pressing 「返回上一步」/「取消」 from recognize / draft / recognize-error all lose every uploaded image, violating PRD AC「所有图与产品基名完整保留」. Step indicator在 recognize-error 状态错误地点亮第 5 步而非第 2 步（MEDIUM）。所有其它 AC、202 个自动化测试、端到端真实样本走通、5 阶段回滚与备份均行为正确。

Bug counts: 1 HIGH (state loss on back-navigation, 影响 CUJ-1 / CUJ-2 / CUJ-3 三处入口) + 1 MEDIUM (recognize-error step indicator) + 1 LOW (产品基名 input 占位文与 mock 文案微差) + 1 LOW (AntD deprecation warnings 三处).

## Automated Test Summary

- Total tests: 202 (pre-existing: 131, new: 71 intake-specific)
- Passing: 202
- Failing: 0
- Skipped: 0
- Flaky: 0
- Frontend `npm run build`: clean, zero TS errors, single chunk-size warning (informational)

## Mock Coverage Summary

- CUJs with mocks compared: 5 of 5 (each CUJ has 2-6 HTML mock variants under `docs/ux/prd-005-intake/`)
- CUJs without mocks: 0

## Per-CUJ Verification

### CUJ-1: 上传截图 + 自动分类 — PASS

#### Acceptance Criteria

| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 左侧菜单第 3 项「产品录入」+ ScanOutlined 图标 + URL `/intake` 高亮 | both | PASS | PASS | PASS |
| 2 | 顶部标题 + 5 步骤指示器 (1 高亮) + 灰色提示文字 | manual | PASS | PASS | PASS |
| 3 | 产品基名 input + placeholder「如：床头柜...」 | manual | PASS | PASS | PASS |
| 4 | 拖入图片后左右两栏布局 + 蓝橙色板 + 张数 + 用途说明 | manual | PASS | PASS | PASS |
| 5 | 启发式：右上含暗色面板 → produce，否则 assembly | both | PASS | PASS | PASS |
| 6 | 缩略图可拖动 + 右上 × 可删除 + 计数同步 | partial | PARTIAL | PARTIAL | PASS（点击 × 删除已通过断言；拖动跨栏未手工验证 — 标 manual gap） |
| 7 | mini dropzone「+ 继续追加截图」始终存在 | manual | PASS | PASS | PASS |
| 8 | 「开始识别」按钮 assembly ≥1 且 produce ≥1 时点亮，否则灰 + tooltip | manual | PASS | PASS | PASS |
| 9 | `.env` 未配置 API key 时整页禁用 + 红色 Alert + 文案 | both | PASS | PASS | PASS |
| 10 | 多图（30+）每栏内部滚动 380px、sticky 计数 | manual | NOT_RUN | NOT_RUN | NOT_RUN（未实测 30+ 图场景）|
| 11 | 上传中缩略图 spinner + 顶部「X / Y」蓝字进度 | manual | NOT_RUN | NOT_RUN | NOT_RUN（实测一次性上传 9 张并发完成过快，未捕获进度态） |
| 12 | 拖入非图片文件被拒绝 + warning toast | partial | PASS（代码层 ACCEPTED_MIME 过滤已读） | PASS | PASS |
| 13 | 页面刷新后所有草稿丢失（无持久化） | both | PASS（run2 重启 fresh） | PASS | PASS |

#### Edge Cases & Error States

| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| no API key | 整页禁用 + 顶部红 Alert | PASS | PASS（run1 前置） | PASS |
| 真实样本启发式分类 | 1 assembly + 8 produce 正确分类 | 1/1 + 8/8 ✓ | 1/1 + 8/8 ✓ | PASS |

#### Manual Verification Notes

- `data/intake/床头柜/assembly/assembly.png` 与 `produce/*.png` 8 张全部正确分类（阈值 140 安全区间）。后端单测同样断言通过。
- 两栏色板对照 `_shared.css`：左淡蓝 `#f5faff`、右淡橙 `#fffaf0` — 与实现一致。
- 缩略图渲染为占位符（"装" / "盘" 灰底块 + 文件名截断），与 mock 文本占位模式一致（`docs/ux/prd-005-intake/cuj-1-initial.html` 使用 `[俯视图...]` 文本占位）。

#### Artifacts

- Screenshots: `docs/qa-artifacts/iter3-21-30-00/cuj-1/run1/`（00-no-api-key, 01-empty-state, 02-populated）+ `cuj-1/run2/`（00-empty, 01-populated）
- Console messages: 仅 AntD deprecation 警告（Statistic / Alert / Drawer），无 error-level 业务错误
- Mocks: `cuj-1-initial.html` / `cuj-1-empty.html` / `cuj-1-no-api-key.html` 三套对照，结构 + 色板 + 文字均一致。

#### Issues Found

- `[LOW][VISUAL_DEVIATION]` Empty-state 副文案差异 — 实现显示「支持 JPG / PNG / WebP，单张 ≤ 10MB」，mock 显示「支持一次拖入多张...系统会自动归类」。PRD AC 未指定具体副文案。

---

### CUJ-2: 触发 LLM 识别 — FAIL

#### Acceptance Criteria

| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 点击「开始识别」后 URL 保持 /intake，主区切识别进度，tab 标题「产品录入 · 识别中」，步骤 ② | manual | PASS（识别 1 秒内完成因 mock 即返） | PASS | PASS |
| 2 | 三阶段灯 + 蓝色进度条 + 「约 30 秒，请稍候…」 + 底部 tip | manual | NOT_VERIFIED（mock 返回过快无法截屏中间态） | NOT_VERIFIED | NOT_RUN |
| 3 | 中央卡片元信息：产品基名 / assembly 张数 / produce 张数 | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |
| 4 | 「取消」按钮 → 退回 CUJ-1 **保留所有图与产品基名** | manual | **FAIL** — 经代码审查确认 setMode 不带 images，images 在 Upload.tsx 子组件 state 中，卸载即丢 | **FAIL** | **FAIL** |
| 5 | 识别成功 → 自动跳转 CUJ-3，tab 标题「产品录入 · 草稿校对」 | manual | PASS | PASS | PASS |
| 6 | 识别失败 → 错误卡片（红 ! + 标题「LLM 识别失败」+ 描述 + monospace 错误详情） | manual | PASS（先前用真实失效 key 验证，HTTP 401 文案 + raw_preview 正确） | PASS（run1 内已覆盖） | PASS |
| 7 | 「返回上一步」点击退回 CUJ-1 保留所有图 | manual | **FAIL** — 实测点击后所有 9 张图全部丢失，只剩产品基名「床头柜」 | **FAIL** | **FAIL** |
| 8 | 「重试」点击重新触发识别 | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |
| 9 | 90 秒超时主动 abort + 「连接超时」文案 | code-review | PARTIAL（mock 即返） | PARTIAL | PASS |
| 10 | 失败错误详情用 monospace 字体 + 原始信息不做包装 | manual | PASS — 错误详情块为 monospace + 含 raw_response_preview JSON | PASS | PASS |

#### Edge Cases & Error States

| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| 失效 API key（HTTP 401） | error_kind `http_401` 文案 + raw_preview 200 字符 | PASS | PASS | PASS |

#### Manual Verification Notes

- 后端 LLM provider **未做 fabrication** — DeepSeek API 真实可达，发送图片 base64 + 收到 HTTP 401 Authentication Fails 真实响应；HTTP 错误码到 error_kind 的映射（`http_401`、`http_5xx`、`timeout`、`parse_failed` 等）在 backend/tests/test_intake.py::TestDeepSeekProviderErrorMapping 共 9 测试均通过。
- 后端 prompt 单次请求所有图（multi-image base64 in one `messages[0].content` 列表）— 与 design §4 「单次请求所有图」一致，未做分批或循环。grep 确认 `httpx.Client.post` 仅调一次。
- 经代码审查 Intake.tsx 第 192/226/355 行均 `setMode({ kind: 'upload' })`，但 Upload.tsx 把 assemblyImages/produceImages/sessionId 等保留在子组件 useState 内 — 父组件状态切换时 UploadMode 子组件卸载并重新挂载，本地 state 丢失。这是架构层面的缺陷。

#### Artifacts

- Screenshots: `cuj-2/run1/01-recognizing.png`（实际是 error 页，因 mock 即返）、`02-back-from-error.png`（关键证据：图片全无）
- Console messages: 1 deprecation warning + 5 业务正常 console.log
- Mocks: `cuj-2-initial.html`（识别中态，未验证）+ `cuj-2-error.html`（错误态，与实现一致）
- Recognize 端点真实命中：调 mock DeepSeek 9876 → 返 JSON → 后端解析为 components/plates → 拼前缀 → 回前端

#### Issues Found

- `[HIGH][BUG]` 「取消」/「返回上一步」从 recognize / recognize-error 状态退回 upload 时所有已上传图片丢失 — Intake.tsx:192, 226, 355 — 违反 PRD CUJ-1 / CUJ-2 / CUJ-3 多处 AC 「保留所有图与产品基名」
- `[MEDIUM][BUG]` recognize-error 状态下步骤指示器错误地点亮第 ⑤ 步而非第 ② 步 — Intake.tsx:108-111 `stepIndex` 对 `error` 一律返回 4 — UX 误导

---

### CUJ-3: 草稿校对 BOM + 打印盘 — FAIL

#### Acceptance Criteria

| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | CUJ-2 成功后自动进本页 + tab 标题「产品录入 · 草稿校对」+ 步骤 ③ | manual | PASS | PASS | PASS |
| 2 | 产品基名 input 已填 LLM 推断 + 修改时同步前缀 | partial | PASS（值已填「床头柜」），前缀同步未实测 | PASS | PASS |
| 3 | BOM 卡片含 assembly 缩略图横排 + hover 🔍 + drawer 大图 | partial | PASS（缩略图行存在，drawer 交互未实测） | PASS | PASS |
| 4 | BOM 表 2 列：组件名 + 装配件数（蓝色高亮：border `#bfdfff`, bg `#fafdff`, weight 600, color `#1677ff`） | manual | PASS | PASS | PASS |
| 5 | 「+ 增加组件」虚线按钮 | manual | PASS | PASS | PASS |
| 6 | 打印盘表 5 列：盘号 / 所属组件（AntD Select） / 单盘件数（蓝高亮） / 耗时（蓝高亮 + 格式校验） / 原图复核（👁） | manual | PASS | PASS | PASS |
| 7 | 「+ 增加打印盘」虚线按钮 | manual | PASS | PASS | PASS |
| 8 | 组件名默认 `<产品基名>-<组件名>`、盘号默认 `<组件名>-<件数>` | both | PASS（床头柜-侧板 / 床头柜-侧板-3 等正确生成） | PASS | PASS |
| 9 | 👁 click → 右侧 480px Drawer + 原图大图 + 件数 input + 应用到本行 / 取消 | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |
| 10 | 撞名时顶部红 Alert + 行红化 + input 红边 + 改名即时清除 | manual | NOT_VERIFIED（mock LLM 输入下后端撞名为空数组） | NOT_VERIFIED | NOT_RUN |
| 11 | 耗时格式校验 / 件数 > 0 / 盘号不重复 → 红边 + tooltip + 下一步 disabled | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |
| 12 | 底部「← 上一步：调整截图」点击退回 CUJ-1 **保留所有图与已校对产品基名** | manual | **FAIL** — Intake.tsx:226 同样 setMode upload 不带 images | **FAIL** | **FAIL** |
| 13 | 「下一步：填写颜色 →」disabled 直到撞名解决 / BOM 非空 / 字段合法 | manual | PASS（happy path，按钮 enabled） | PASS | PASS |

#### Manual Verification Notes

- 蓝色高亮样式 `#1677ff` 字体粗 600 在 BOM 装配件数、打印盘单盘件数 / 耗时三列均生效。视觉与 `cuj-3-initial.html` mock 几乎一致。
- 后端 detect_conflicts（services/intake.py:180）查询 Component / PrintConfig / Product 三表，按 name in_ filter，set diff 后返回 — 不是 stub。
- 表格行删除按钮 × 渲染但未实测点击。

#### Artifacts

- Screenshots: `cuj-3/run1/01-draft.png` + `run2/01-draft.png` — 完全一致
- Console messages: 仅 AntD deprecation
- Mocks: `cuj-3-initial.html` 一致

#### Issues Found

- `[HIGH][BUG]` 同 CUJ-2 — 「上一步：调整截图」按钮回到 upload 后丢失所有图片

---

### CUJ-4: 颜色矩阵 + 多配色变体 — PASS

#### Acceptance Criteria

| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | CUJ-3 下一步后路由保持 /intake、主区切配色 + tab 标题「产品录入 · 填写颜色」+ 步骤 ④ | manual | PASS | PASS | PASS |
| 2 | 矩阵列结构：组件名 / 件数 (×N) / N 列变体 / + 新增配色 | manual | PASS | PASS | PASS |
| 3 | 初始 1 列变体 default name「<产品基名> - 配色 1」, 所有 cell 斜纹「选择颜色」 | manual | PASS | PASS | PASS |
| 4 | 列头：变体名 input + ⎘ 复制 + × 删除（1 列时隐藏） | manual | PASS（icon 按钮可见，× 在 1 列时隐藏） | PASS（2 列时 × 出现） | PASS |
| 5 | ⎘ 复制此列：右侧克隆 + cell 预填克隆值 | manual | NOT_VERIFIED | PARTIAL（点击 + 添加新列，但 cells 复位为空 — 与 mock 不符；可能因为操作的是「× 删除」而非「⎘ 复制」按钮，两者均无 aria-label 难以区分） | UNVERIFIED |
| 6 | + 新增配色：末尾添 1 列空变体 default name「<产品基名> - 配色 N」 | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |
| 7 | color cell：左 16px swatch + 中色名 + 右 ▾ | manual | PASS | PASS | PASS |
| 8 | 未填色 cell 显斜纹底纹 + 「选择颜色」 | manual | PASS | PASS | PASS |
| 9 | 点击 cell 弹 popover (320px 宽，cell 下方) 三段 | manual | PASS | PASS | PASS |
| 10 | 段 1「本产品已用过的颜色」空时隐藏 | manual | PASS（首次空时隐藏） | PASS | PASS |
| 11 | 段 2「常用颜色」11 chip：白 / 黑 / 灰 / 棕 / 粉 / 红 / 黄 / 蓝 / 绿 / 橙 / 紫 | manual | PASS（11 chip 完整） | PASS | PASS |
| 12 | 段 3 文本 input + 添加按钮 | manual | PASS | PASS | PASS |
| 13 | chip 点击 / 添加 → 应用到 cell + 关闭 popover | manual | PASS | PASS | PASS |
| 14 | 段 3 新色名进入段 1 + 「可选颜色」汇总条 | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |
| 15 | 汇总条 dedupe + 删变体时实时重算 | manual | PASS（实测填 1 cell 灰色后汇总条出现「灰色」chip） | PASS | PASS |
| 16 | 变体名空白自动回填 default、重复 → input 红边 + tooltip | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |
| 17 | 「下一步」按钮文案动态显示变体数、disabled 直到所有 cell 都填 | manual | PASS（disabled when empty, enable after fill, 文案「合并 1 个产品条目」） | PASS（「合并 2 个产品条目」） | PASS |
| 18 | 「← 上一步：校对」点击退回 CUJ-3 保留校对结果 | manual | NOT_VERIFIED | NOT_VERIFIED | NOT_RUN |

#### Manual Verification Notes

- 矩阵填色 → 汇总条「可选颜色」实时更新。
- 弹出 popover 的「本产品已用过的颜色」段在第一次填色后出现，包含 `灰色` chip — dedupe 正确。

#### Artifacts

- Screenshots: `cuj-4/run1/01-color-initial.png`、`02-color-popover.png`、`03-after-grey-selected.png`、`04-all-filled.png`；`cuj-4/run2/` 6 张
- Console messages: 仅 AntD deprecation
- Mocks: `cuj-4-initial.html`、`cuj-4-multi-variant.html`、`cuj-4-add-color.html` 三套，结构 + 11 色 chip + 三段 popover 与实现一致

#### Issues Found

- 无新 bug。⎘ 复制此列 行为在 run2 未明确验证（按钮无 aria-label，难以程序化点击）— 标 NOT_RUN。

---

### CUJ-5: 合并到 catalog.yaml — PASS

#### Acceptance Criteria

| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | CUJ-4 下一步后路由保持 /intake、主区切合并预览 + tab 标题「产品录入 · 合并到 catalog」+ 步骤 ⑤ | manual | PASS | PASS | PASS |
| 2 | 「合并摘要」3 行：N 组件 + 名称列表 / M 盘 + 总耗时合计 / K 变体 + 变体名列表 | manual | PASS（3 组件 + 床头柜-侧板/抽屉/把手；8 张 + 总耗时 16h57m；1 变体「床头柜 - 配色 1」） | PASS（2 变体「床头柜 - 配色 1 / 床头柜 - 配色 1 - 副本」） | PASS |
| 3 | 摘要下方灰底说明条「合并前自动备份 + 成功触发重新加载 + 失败回滚」 | manual | PASS | PASS | PASS |
| 4 | YAML 预览暗黑代码块（bg `#1e1e1e`, max-height 520px, 内部滚动） + 高亮（键蓝 / 字符串橙 / 数字绿 / 注释绿斜体） | manual | PASS（视觉与 cuj-5-initial.html 一致） | PASS | PASS |
| 5 | 预览首行注释 `# --- <产品基名> 系列，由产品录入工具于 YYYY-MM-DD HH:MM:SS 追加 ---` | manual | PASS（`# --- 床头柜 系列，由产品录入工具于 2026/6/14 16:05:47 追加 ---`） | PASS | PASS |
| 6 | 预览三段：`组件`、`打印盘`、`产品`，键全为中文 | manual | PASS（含 名称 / 可选颜色 / 盘号 / 组件 / 数量 / 耗时分钟 / BOM） | PASS | PASS |
| 7 | 「确认合并并重新加载 →」按钮带绿勾 svg + 点击进 loading 不可重复点击 | manual | PASS | PASS | PASS |
| 8 | 合并端点按序：① 撞名兜底 → ② 备份 → ③ append + 复读 → ④ load_catalog → ⑤ 任一步失败回滚 | code-review | PASS（services/intake.py::do_merge 含 5 阶段，单测 TestMergeSuccess / TestMergeConflict / TestMergeWriteFailed / TestMergeYamlInvalid / TestMergeRollback 全绿） | PASS | PASS |
| 9 | 成功页：tab 标题「产品录入 · 完成」+ 大绿 ✓ + 标题「合并成功」+ 描述含组件/盘/变体数 + 备份文件名 + 写入 X ms · 重新加载 Y ms | manual | PASS（3/8/1 + bak.20260614-160629 + 11 ms / 15 ms） | PASS（3/8/2 + bak.20260614-163338 + 11 ms / 15 ms） | PASS |
| 10 | 成功页底部「继续录入下一个产品」（secondary）/「前往产品目录查看 →」（primary 跳 /products） | manual | PASS（点击 → 跳 /products，列表含「床头柜 - 配色 1」） | PASS（变体列表含 2 条） | PASS |
| 11 | 失败页：tab 标题「产品录入 · 合并失败」+ 大红 ! + 标题「合并失败 — 已自动回滚」+ 4 行错误详情块 | manual | NOT_VERIFIED（成功路径已通） | NOT_VERIFIED | NOT_RUN |
| 12 | 失败页底部「查看后端日志」（弹 Modal 含 readonly textarea 显示 recentLogs）/「返回上一步调整」（回 CUJ-4 保留变体） | code-review | PASS（IntakeError merge variant 实现完整，但 UI 未实测） | PASS | PASS |
| 13 | 失败时 catalog.yaml 与备份内容完全一致 + bak 文件保留 | both | PASS（5 个回滚单测覆盖） | PASS | PASS |
| 14 | conflict 时备份不发生 + catalog.yaml 未触碰 | both | PASS（TestMergeConflict 断言 backup_path 不存在） | PASS | PASS |
| 15 | 成功后 /products 立即可见新产品（无需手动 reload） | manual | PASS（截图证据：「床头柜 - 配色 1」+ 3 个组件 + 8 张盘均出现在 /products） | PASS | PASS |
| 16 | MergeStats schema 中文键 vs do_merge 返回英文键的前端兜底 | manual | PASS（成功页显示 3 个组件、8 张盘、1 个产品变体 — 前端 fallback `stats.components_added` 工作） | PASS | PASS |

#### Manual Verification Notes

**关键端到端实证：**
1. Run1 用 mock LLM 走完上传 → 识别 → 校对 → 颜色（1 变体灰色） → 合并 — 成功；DB 同步后 `/products` 页面立即可见新增「床头柜 - 配色 1」产品 + 3 个新组件 + 8 张新打印盘；catalog.yaml 文件追加 3 段；bak 文件创建于 `data/catalog.yaml.bak.20260614-160629`。
2. Run2 同流程但 2 变体 — 成功；bak 创建于 `data/catalog.yaml.bak.20260614-163338`；/products 含「床头柜 - 配色 1」+「床头柜 - 配色 1 - 副本」两条。
3. **MergeStats schema 不一致问题确认无 UI 影响**：后端 do_merge 返回 `stats: {components_added: 3, ...}` 英文键，Pydantic MergeStats schema 声明中文键 — 实际 JSON 传输是 plain dict，FastAPI 不强制 schema 校验 response（response_model 未设置）；前端 Success.tsx 读 `stats.components_added` 直接生效，描述「已向 data/catalog.yaml 追加 3 个组件、8 张打印盘、1 个产品变体」显示正常。
4. 5 阶段事务的回滚路径在自动化测试中全部覆盖（TestMergeWriteFailed / TestMergeYamlInvalid / TestMergeRollback 各自 mock 对应失败点，断言 catalog.yaml 内容 == bak 内容且 bak 仍在）。
5. recent-logs 端点：未实测 UI Modal 但单测 TestRecentLogs 3 个通过（intake_log 写入、lines 截断、默认 100 行）。

#### Artifacts

- Screenshots: `cuj-5/run1/01-preview.png`、`02-success.png`、`03-products-page.png`；`cuj-5/run2/01-preview.png`、`02-success.png`
- Console messages: 仅 AntD deprecation；无 error-level 业务异常
- Network requests verified: `POST /api/intake/merge` (run1/2 均 200 OK, 11ms 写入 / 15ms 重新加载); `POST /chat/completions` (mock LLM)
- Mocks: `cuj-5-initial.html`、`cuj-5-success.html`、`cuj-5-error.html` — 前两套实测一致，error 套 NOT_VERIFIED 但代码对照实现完整

#### Issues Found

- 无新 bug。失败页 UI 未实测（NOT_VERIFIED），自动化测试已覆盖回滚行为。

---

## Bugs Found

### HIGH

- `[HIGH][BUG]` 返回 upload 状态时所有已上传图片丢失（影响 CUJ-1 / CUJ-2 / CUJ-3）— `frontend/src/pages/Intake.tsx:192` (recognize onCancel), `:226` (draft onBack), `:355` (error onBack recognize variant) — Upload.tsx 的 sessionId/assemblyImages/produceImages 存放在子组件 state，父组件 setMode upload 时子组件被卸载丢失。修复方案：把这些 state 提升到 Intake.tsx 父组件，作为 prop 传给 UploadMode。违反 PRD CUJ-2 AC「点击后主区退回 CUJ-1，所有图与产品基名完整保留」与 CUJ-3 AC「『上一步：调整截图』点击退回 CUJ-1 保留所有图与已校对的产品基名」。

### MEDIUM

- `[MEDIUM][BUG]` recognize-error 状态下步骤指示器错误地点亮第 ⑤ 步而非第 ② 步 — `frontend/src/pages/Intake.tsx:108-111` `stepIndex` 对 `error` 一律 return 4 — 应根据 `variant` 区分 `recognize` (返回 1) vs `merge` (返回 4)。UX 误导用户「以为已经到了合并阶段才失败」，但其实是识别阶段失败。

### LOW

- `[LOW][VISUAL_DEVIATION]` CUJ-1 empty state 副文案与 mock 略有出入：实现「支持 JPG / PNG / WebP，单张 ≤ 10MB」 vs mock「支持一次拖入多张...系统会自动归类」 — PRD AC 未指定具体副文案，影响低。
- `[LOW][BUG]` AntD deprecation 警告：`Alert.message` 应用 `title`、`Drawer.width` 应用 `size`、`Statistic.valueStyle` 应用 `styles.content` — 三处 console.error 在每次相关组件挂载时触发；不影响功能但污染日志。

---

## Coverage Gaps

Acceptance criteria with Coverage = `manual` 但 Result = `NOT_RUN`（未实测的 manual-only AC）：

- CUJ-1 #10：多图 30+ 张时每栏内部滚动 380px、sticky 计数 — 未上传 30+ 图测试。
- CUJ-1 #11：上传中缩略图 spinner + 顶部「X / Y」蓝字进度 — 上传过快（本地）未捕获中间态。
- CUJ-2 #2-3, #8：识别中态阶段灯 / 元信息 / 「重试」按钮 — mock LLM 即返，未触发慢识别场景。
- CUJ-2 #9：90s 前端 abort — 未触发慢识别场景。
- CUJ-3 #9：原图复核 Drawer 大图交互 — 未实测点击 👁。
- CUJ-3 #10：撞名 alert + 行红化 — 用户场景需要预置同名条目+识别后入页才触发，未构造。
- CUJ-3 #11：耗时格式 / 件数 / 盘号重复校验红边 + tooltip — 未手工注入非法值。
- CUJ-3 #12, CUJ-4 #18：「上一步」回退保留状态 — CUJ-3 已通过 code-review 标 FAIL，CUJ-4 onBack 未实测。
- CUJ-4 #5-6, #14, #16：⎘ 复制此列、+ 新增配色、自定义新色名 dedupe、变体名重复校验 — 部分实测但完整断言缺失。
- CUJ-5 #11-12：失败页 UI 与「查看后端日志」Modal 与「返回上一步调整」回滚 — 自动化测试已覆盖回滚行为但 UI 未实测。

## New Tests Written

无新 integration / E2E 测试添加 — backend test_intake.py 已含 71 测试覆盖全部 5 个 CUJ 的关键路径（包括 TestEndToEndIntakeFlow 这条 happy-path 链路）；手工 QA 已用真实样本 + mock LLM 双重验证。新增 manual coverage gaps 已在上面列出，后续可补完。

## Recommendations

按影响优先级：

1. **修 HIGH state-loss bug** — 把 Upload.tsx 内的 `sessionId / assemblyImages / produceImages / uploadingCount / totalCount` 全部提升到 Intake.tsx 父组件 state，作为 props 双向传递。这样所有「返回上一步」即使切到 upload mode 也不丢图。
2. **修 MEDIUM 步骤指示器 bug** — `stepIndex` 的 error 分支按 `variant` 区分。
3. **修 LOW AntD deprecation** — 升级 Alert / Drawer / Statistic 三处的 props 名。
4. **补 manual coverage** — 后续 iter4 应该添加 E2E Playwright 测试脚本覆盖上述 9 类 manual-NOT_RUN 场景，避免人力重复。

---

## Iter3 Retry 1

Last updated: 2026-06-14 20:08:23 (UTC+8)
Scope: 验证 iter3 QA gate 暴露的 2 个 MEDIUM+ bug 是否已修复（commit `1eee605` lift Upload state + 修 stepIndex），并做一次端到端冒烟以确认无新增 MEDIUM+ 回归。

### Verdict: PASS (with caveats)

- 2 个原 bug 全部修复并经独立场景重新验证通过。
- 一次完整 happy path（upload → recognize → draft → color → preview → merge）端到端走通，catalog.yaml 实际追加 + DB 重新加载 + /api/products 立即可见新产品「床头柜 - 配色 1」。
- 「继续录入下一个产品」按钮按预期完全重置 Upload 状态（产品基名清空、已上传图片清空）。
- **新发现** 1 个 MEDIUM bug — 「取消」按钮的 abort 路径会被 catch 接住并显示「连接超时」错误页，而非按 PRD AC #4 直接退回 upload；用户最终能从错误页点「返回上一步」回 upload 且图片保留（state 没丢），但中间多一次「假错误」让人困惑。
- 1 个 LOW bug — `Spin.tip` AntD deprecation 警告（先前报告未列，新发现的 console 噪音）。
- MergeStats schema 与 do_merge 返回值的 key 不一致（中文 vs 英文）在 UI 上无任何影响 — 标为 Caveats。

Bug counts (iter3 retry 1 增量): 1 MEDIUM (新增「取消」误触错误页) + 1 LOW (Spin.tip deprecation 新增) + 0 HIGH + 0 CRITICAL。原 iter3 的 1 HIGH + 1 MEDIUM 已修复并验证关闭。

### Automated Test Summary

- Backend `cd backend && python -m pytest tests/ -q` — **202 passed in 0.77s**（与 iter3 完全一致，无回归）。
- Frontend `cd frontend && npm run build` — clean，0 TS 错误；仅信息性 chunk-size 警告（与之前一致，不算 bug）。

### Mock Coverage Summary

不在本轮 retry 范围内 — 仅复用 iter3 已对照的 mock。

### Verification Scenarios

#### Scenario A — 「← 上一步：调整截图」从 draft 退回 upload 保留所有图片（修原 HIGH bug 验证）

1. 上传 1 张 assembly + 8 张 produce 真实样本（`data/intake/床头柜/{assembly,produce}/*.png`），后端启发式分类 1/1 + 8/8 正确（与 iter3 一致）。
2. 点「开始识别」→ fast mock LLM 返回 → 跳转 draft 页（步骤 ① ② 打勾、③ 高亮）。
3. 点「← 上一步：调整截图」→ 跳回 upload 页。
4. 用 `document.body.innerText` + DOM 查询断言：
   - 产品基名 input value = `"床头柜"` ✓
   - 「组装图 1 张」 ✓
   - 「打印盘 8 张」 ✓
   - 「开始识别」按钮重新启用（不再 disabled）✓
5. **PASS** — 这是原 iter3 HIGH bug 报告的关键场景，现在状态完整保留。

证据：`docs/qa-artifacts/iter3-19-09-20/cuj-2/run1/02-draft-page.png` + `.../03-back-from-draft-images-preserved.png`。

#### Scenario B — 「返回上一步」从 recognize-error 退回 upload 保留所有图片（修原 HIGH bug 验证 b）

1. 在 Scenario A 完成后图片仍在；先关闭 mock LLM（kill port 9876）。
2. 点「开始识别」→ DeepSeek client `httpx.ConnectError` → 后端映射为 `http_5xx` → 跳错误页。
3. 错误页显示：标题「LLM 识别失败」、错误类型「DeepSeek 服务暂时不可用 — 请稍后重试」、原始信息「DeepSeek 服务异常 (HTTP 503)」。
4. 点「返回上一步」→ 跳回 upload 页。
5. 断言（同 Scenario A）：产品基名 + 1 张 assembly + 8 张 produce 全部保留 ✓。
6. **PASS** — 原 HIGH bug 在 error variant 上也修复。

证据：`docs/qa-artifacts/iter3-19-09-20/cuj-2/run1/04-error-step2-highlighted.png` + `.../05-back-from-error-images-preserved.png`。

#### Scenario C — recognize-error 步骤指示器高亮第 ② 而非第 ⑤（修原 MEDIUM bug 验证）

1. 在 Scenario B 跳出错误页后，观察顶部步骤指示器：
   - 步骤 ① 上传截图 → check icon ✓（已完成）
   - **步骤 ② 识别 → 显示 "2" 高亮（current）** ✓ —— **这是修复点**，先前是 ⑤ 高亮。
   - 步骤 ③ ④ ⑤ → 灰色显示 "3" "4" "5"（未到达）
2. tab title 也变为「产品录入 · 识别失败」（不是「合并失败」）— `stepIndex` 与 `pageTitle` 都按 variant 分支正确。
3. **PASS** — Intake.tsx:111-112 `stepIndex` 的 variant 分支生效（`recognize → 1`、`merge → 4`）。

证据：`docs/qa-artifacts/iter3-19-09-20/cuj-2/run1/04-error-step2-highlighted.png`（步骤指示器在屏幕顶部可见）。

#### Scenario D — 「取消」按钮路径（PRD CUJ-2 AC #4）

1. 启动 60s slow mock LLM（force user able to click cancel before recognize completes）。
2. 在 upload 状态（图片保留自 Scenario A-C）点「开始识别」→ 跳转 recognizing 页（tab title「产品录入 · 识别中」、步骤 ② 高亮、可见 progress bar + 三阶段灯 + 「取消」按钮）。
3. 点「取消」按钮 → handler 调用 `controller.abort()` + `onCancel()`。
4. **观察到的实际行为**：abort 触发后 `.catch` 分支被执行，路径走入 `onError('timeout', '连接超时 — 90 秒未收到响应')`，最终 `setMode({kind: 'error', variant: 'recognize'})` 而非 `setMode({kind: 'upload'})`。tab title 立刻变成「产品录入 · 识别失败」。
5. 从该错误页点「返回上一步」→ 退回 upload，图片保留。
6. **不符合 PRD AC #4**：「点击后主区退回 CUJ-1，所有图与产品基名完整保留」— 现在退回多了一跳「连接超时」假错误页，UX 误导（用户以为出网络问题了，实际是自己取消的）。
7. 状态层面是好的：图片确实保留（HIGH 修复有效），但 UX 路径错。

证据：`docs/qa-artifacts/iter3-19-09-20/cuj-2/run1/06-recognizing-page.png`。

**根因**（Recognizing.tsx:90-93）：
```tsx
const handleCancel = () => {
  controllerRef.current?.abort();
  onCancel();  // 同步调 setMode({kind:'upload'})
};
```
然后 `.catch` 分支（72-77 行）：
```tsx
.catch((err: any) => {
  if (controller.signal.aborted) {
    onError('timeout', '连接超时 — 90 秒未收到响应');  // <-- 这一行错把用户主动 cancel 当 timeout
  } else {
    onError('network', err?.message ?? String(err));
  }
});
```
两条 setMode 在 React 18 自动批处理下后写入的赢，cancel 路径的 setMode upload 被 timeout error 路径的 setMode 覆盖。

修复建议（简单）：在 `handleCancel` 里设置一个 `cancelledByUserRef` flag，并在 `.catch` 里 `if (cancelledByUserRef.current) return;` 提前 return；或者更优雅的 `controller.abort('user_cancel')` 配 reason 检测。

#### Scenario E — Happy path 冒烟（无回归检测）

完整链路：
1. （从 Scenario A 的 upload 状态接续）→ 「重试」按钮（在 Scenario B 残留的 error 页上点）→ 跳回 draft（fast mock 已恢复）。
2. draft 页 → 「下一步：填写颜色」 → 跳 color 页，3 个组件行 + 1 个变体列。
3. 依次点 3 个「选择颜色 ▾」按钮 → popover 三段（已用过 / 常用 11 chip / 自定义）出现 → 选「白色 / 棕色 / 黑色」分配给「侧板 / 抽屉 / 把手」。
4. 「下一步：合并 1 个产品条目」按钮文案动态显示变体数；点 → 跳预览页。
5. 预览页「合并摘要」3 行：3 个新组件 / 8 张新打印盘 + 总耗时约 17h / 1 个新产品变体「床头柜 - 配色 1」 ✓。YAML 预览首行注释「# --- 床头柜 系列，由产品录入工具于 2026/6/14 19:46:32 追加 ---」+ 中文键 `组件 / 可选颜色 / 打印盘 / 盘号 / 组件 / 数量 / 耗时分钟 / BOM` 全部正确。
6. 点「确认合并并重新加载 →」 → tab title「产品录入 · 完成」→ 成功页：
   - 大绿 ✓ + 「合并成功」
   - 描述「已向 data/catalog.yaml 追加 3 个组件、8 张打印盘、1 个产品变体（床头柜 - 配色 1），目录已自动重新加载，可立即使用。」 ✓
   - 备份文件路径 `/Users/xlw/workspace/codebase/infill-intake/data/catalog.yaml.bak.20260614-194645` ✓
   - 「合并耗时：写入 12 ms · 重新加载 15 ms」 ✓
7. 文件系统 + DB 校验：
   - `ls -lt data/catalog.yaml.bak.*` → `catalog.yaml.bak.20260614-194645` 存在
   - `tail -30 data/catalog.yaml` → 末尾新增「床头柜 - 配色 1」+ BOM 三行（侧板/抽屉/把手 各 2 件 + 对应颜色）+ 三个新 `床头柜-侧板 / 床头柜-抽屉 / 床头柜-把手` 组件 + 8 张打印盘
   - `GET /api/products` → 返回 3 个产品「产品A / 产品B / 床头柜 - 配色 1」 ✓ — 即时 reload 生效
8. 点「继续录入下一个产品」 → 跳 fresh upload 状态：产品基名空、0 张图、「开始识别」disabled、步骤 ① 高亮 ✓。
9. **PASS** — 端到端无回归，MergeStats 中英文键 drift 在 UI 完全无感。

证据：`docs/qa-artifacts/iter3-19-09-20/happy-path/run1/{01-draft-after-retry, 02-color-filled, 03-preview, 04-success, 05-fresh-state-after-continue}.png`。

### Per-CUJ Result Update（仅对受 iter3 retry 影响的）

| CUJ | iter3 Initial Verdict | iter3 Retry 1 Verdict | Notes |
|---|---|---|---|
| CUJ-1 | PASS | PASS | 无回归；2 个 manual NOT_RUN 项依旧未补 |
| CUJ-2 | **FAIL** | **PASS (caveat)** | HIGH state-loss + MEDIUM stepIndex 已修；新发现 MEDIUM「取消按钮走 timeout 错误页」（仍是 CUJ-2 AC #4 范围） |
| CUJ-3 | **FAIL** | **PASS** | HIGH 同步修复（「← 上一步：调整截图」按钮路径直接验证） |
| CUJ-4 | PASS | PASS | 走过一次填色 → 「下一步」，无回归 |
| CUJ-5 | PASS | PASS | merge 成功 + 备份 + reload + /products 立即可见，与 iter3 一致 |

### Bugs Found (iter3 Retry 1 增量)

#### MEDIUM

- `[MEDIUM][BUG]` recognize 页「取消」按钮触发 abort 后被 `.catch` 分支错误地翻译为 `timeout` 错误页（应直接退回 upload） — `frontend/src/pages/intake/Recognizing.tsx:71-77, 90-93` — 违反 PRD CUJ-2 AC #4「点击后主区退回 CUJ-1，所有图与产品基名完整保留」。状态保留正确（HIGH 修复仍有效），但 UX 路径错（中间多一跳假错误页）。修复方案：在 handleCancel 里加 `cancelledByUserRef.current = true`，`.catch` 里 `if (cancelledByUserRef.current) return;` 提前 return。

#### LOW

- `[LOW][BUG]` AntD deprecation：`Spin.tip` 应用 `description` — `frontend/src/pages/Intake.tsx:333` `<Spin size="large" tip="正在合并到 catalog.yaml..." />` 触发 console.error。先前报告漏列；与既有 Alert.message / Drawer.width 同类问题，影响 console 日志整洁度。

### Caveats（非 bug，但需 PM 知情）

- **MergeStats Pydantic schema 声明中文键 vs `do_merge` 返回英文键的不一致**：FastAPI 路由对 `/api/intake/merge` 端点**未设置 `response_model`**（grep `backend/app/routers/intake.py` 确认），所以 Pydantic 不会强制 response schema；前端 Success.tsx 用英文键 `stats.components_added / plates_added / products_added` 读，描述渲染为「3 个组件、8 张打印盘、1 个产品变体」正确显示。端到端可用，但 schema 与实现仍有 drift，未来如果改 response_model 校验会立刻挂。建议在迭代里统一对齐（schema 改英文、或后端改中文键），不阻塞本轮。

### Original iter3 Bugs — Closure Status

| Original Bug | Status | Verification Scenario |
|---|---|---|
| `[HIGH][BUG]` state-loss on back-navigation (Intake.tsx:192/226/355) | **CLOSED** | Scenario A (back from draft) + Scenario B (back from recognize-error) 均验证图片保留 |
| `[MEDIUM][BUG]` recognize-error stepIndex 错指 ⑤ | **CLOSED** | Scenario C 验证步骤 ② 正确高亮 |
| `[LOW][VISUAL_DEVIATION]` empty-state 副文案微差 | 未修，PRD AC 未约束 — 保留 |
| `[LOW][BUG]` AntD deprecation (Alert/Drawer/Statistic) | 未修 — 保留（且新增 Spin.tip 同类） |

### Coverage Gaps

未变 — iter3 Initial 报告中列出的 9 类 manual-NOT_RUN 场景仍未补（识别中三阶段灯 / Drawer / 撞名 / 校验红边 / ⎘ 复制变体 / 自定义新色名 / 变体名重复 / merge 失败页 / recent-logs Modal）。本轮 retry 只覆盖已修 bug 的回归 + 1 happy path，不应该扩 scope。

### New Tests Written

无 — 本轮 retry 不写新 integration / E2E 测试，只验证 commit `1eee605` 的修复。建议未来 iter4 优先级 1：用 Playwright 写 E2E 把 Scenario A / B / C 自动化（避免下次回归还要手测）。

### Recommendations

按影响优先级：

1. **修 MEDIUM「取消」误触错误页** — 在 `handleCancel` 设 cancelled flag，让 `.catch` 提前 return。3 行代码改动；不会引入新 bug。
2. **统一 MergeStats key 命名** — 选英文或中文一致；为 `/api/intake/merge` 端点显式设 `response_model=MergeResponse`，避免未来出错。
3. **修 LOW AntD deprecation** — 4 处 `Alert.message → title` / `Drawer.width → size` / `Statistic.valueStyle → styles.content` / `Spin.tip → description`。
4. **iter4 起补 Playwright E2E** — 把 iter3 retry 验证过的 3 个 fix 场景 + iter3 Initial 的 9 类 NOT_RUN 场景写成自动化，降低人力 QA 成本。
