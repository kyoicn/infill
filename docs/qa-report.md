# QA Report

Last updated: 2026-06-18 22:20:32 (UTC+8)
Scope: prd-006-auto-import-orders（Iter4 — 4 CUJ 端到端首轮 QA）

## Verdict: FAIL

实现层基础完整且自动化测试 340 通过（baseline 330 + 新增 10），核心后端契约（commit 单事务原子性、`-redoN` override、partial unique index、extension zip 4 文件、stateless 预览批次）live 验证全部通过。但 4 个 MEDIUM+ bug 阻断 PASS：① CUJ-1 缺少扩展下载链接（PRD AC 明示要求）；② CUJ-2/4 `adb_connected` 用 device 列表非空当通过条件 → 在配置 endpoint 不可达时仍亮绿灯，等价于「扫描就绪状态被伪造」；③ CUJ-3 缺少空 batch 的「未抓取到任何订单」空态 UI；④ extension probe 与 xhs/probe 实际并不真探活（占位实现），违反 CUJ-1 AC「调 probe 探查千帆 tab」。MEDIUM+ 计 4 + LOW carry-over 1（AntD Spin tip deprecation）+ TL review carry-over 5（已在用户消息中标注）。

## Automated Test Summary
- Total tests: 342 (pre-existing: 330 + 2 skipped, new: 10)
- Passing: 340
- Failing: 0
- Skipped: 2
- Flaky (failed-then-passed on framework retry): 0

## Mock Coverage Summary
- CUJs with mocks compared: 4（CUJ-1 / CUJ-2 / CUJ-3 / CUJ-4）
- CUJs without mocks (`NO_MOCK`): 0

## Per-CUJ Verification

### CUJ-1: 扫描小红书千帆订单 — FAIL

CUJ-1 默认渲染「扩展未装」态（环境无扩展、`VITE_INFILL_EXT_ID` 未配置），与 `cuj-1-no-extension.html` mock 布局对齐，但**关键下载按钮缺失**（PRD AC 明示「下载扩展压缩包链接（指向 `/static/extensions/infill-xhs-scraper-v0.1.x.zip`）」）+ probe 端点是占位实现（永远返回 has_xhs_tab=true，不真探活）。生产路径 tuple 解包逻辑通过新加单测验证。未走 happy path（无 LLM key / 无扩展 / 无千帆 tab，CI 不可达）。

#### Acceptance Criteria
| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 左侧菜单存在「订单管理 → 自动导入」/ 顶部入口 | manual | PASS | PASS | PASS |
| 2 | 进入 `/orders/import` 渲染面包屑「订单管理 / 自动导入」+ 标题副标题 | manual | PASS | PASS | PASS |
| 3 | 双 tab 切换「小红书千帆」红 `#ff2442` / 「闲鱼」橙 `#ff7a00`，切 tab 不丢前端态 | manual | PASS | PASS | PASS |
| 4 | 进入页面 / 切 tab / 「重新检测」时调 `chrome.runtime.sendMessage(ping)` + `POST /api/auto-import/xhs/probe` | both | PASS（probe 端点返回但是**占位**，并未真探活） | PASS | **FAIL**（占位实现违背 AC「探查千帆 tab」） |
| 5 | 控制栏三态指示器 ● 就绪 / ● 扩展未装 / ● 未发现千帆 tab，颜色绿/蓝/黄 | manual | PASS（蓝色未装态正确渲染） | PASS | PASS |
| 6 | 「开始扫描」按钮仅就绪态启用 | manual | PASS（disabled，未装态没显示按钮） | PASS | PASS |
| 7 | 5 步进度列表 ① 连接扩展 ② 定位千帆 tab ③ 抓取 DOM ④ 解析订单 ⑤ LLM 匹配 SKU | none | NOT_RUN | NOT_RUN | NOT_RUN（无扩展无法触发） |
| 8 | 5 步完成后自动跳 CUJ-3 预览，面包屑变「预览批次」 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 9 | 「取消」回到 CUJ-1 初始态 + 5 秒灰提示 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 10 | 扩展未装态显示蓝色 setup 块 + 4 步安装引导（**下载链接 → 解压 → chrome://extensions/ → 加载**） | manual | **FAIL**（4 步文案有，但下载按钮 / 链接缺失） | **FAIL** | **FAIL** |
| 11 | 未发现千帆 tab 时显示黄色 warning 块 + 「重新检测」按钮 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 12 | 抓 0 单仍跳 CUJ-3 空态 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 13 | 缺必填三件套时后端丢弃该单（不入预览） | automated | PASS | PASS | PASS（test_scan_drops_missing_required_fields） |
| 14 | LLM 匹配 90 秒超时 → 错误卡片 + 三按钮 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 15 | 「跳过 SKU 匹配」按钮允许 LLM 故障时仍进 CUJ-3 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 16 | 闲鱼扫描进行中时小红书「开始扫描」disabled + tooltip | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 17 | 生产路径 `match_listing_to_sku` 返回 tuple 时 router 正确解包 | automated | PASS | PASS | PASS（test_xhs_scan_with_production_tuple_path） |

#### Edge Cases & Error States
| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| 扩展未装（无 chrome.runtime） | 蓝色 setup 块 + 下载链接 + 4 步 | 蓝色块 + 4 步（无下载链接） | 同 run1 | FAIL |
| extension-status 在未配置 INFILL_EXT_ID 时 | 返回 `{ok:true, installed:false}` | PASS（live curl） | PASS | PASS |
| extension-status 在配置 INFILL_EXT_ID 时 | 返回 `{ok:true, installed:true, version}` | PASS（automated） | PASS | PASS |
| xhs/probe 端点 | 探查千帆 tab 真活性（AC #4） | 占位永远 ok=true（**违反 AC**） | 同 run1 | FAIL |

#### Manual Verification Notes
- `/orders/import` 默认渲染未装态（`hasInstallText=true, hasDownloadLink=false`），4 步引导第 1 步文案"下载扩展 zip 并解压到本地"但无 `<a>` 或 `<button>` 实际触发下载，违反 PRD CUJ-1 Mocks `cuj-1-no-extension.html` 明示的"下载扩展 zip (12 KB)"主按钮。
- 生产路径 LLM tuple 返回值经 curl + automated 双重验证：`match_listing_to_sku` 返回 `(sku|None, confidence, reasoning)` 三元 tuple，router 正确解包。LLM 未配置时降级为 `confidence=0` reasoning="LLM 错误：未配置 LLM API key"，UI 看到的是 low_conf=1。
- 5 步进度 / 自动跳预览 / 取消 等扫描中流程因无 Chrome 扩展环境不可在 CI 验证，标 NOT_RUN（**待真实环境覆盖**）。

#### Artifacts
- Screenshots: `docs/qa-artifacts/iter4-22-12-12/cuj-1/run1/00-initial-no-extension.png`，`.../run2/00-initial-no-extension.png`
- Console messages (run1): 0 错误
- Console messages (run2): 0 错误
- Network requests verified: `GET /api/auto-import/xhs/extension-status` 200，`POST /api/auto-import/xhs/probe` 200（占位）
- Mocks: `docs/ux/prd-006-auto-import-orders/cuj-1-initial.html`（未触发：默认是未装态）、`cuj-1-no-extension.html`（**对比执行**）、`cuj-1-scanning.html`（NOT_RUN）、`cuj-1-no-xhs-tab.html`（NOT_RUN）

#### Issues Found
- `[MEDIUM][BUG]` 扩展未装态缺少下载链接 / 按钮 — frontend/src/pages/auto_import/XhsTab.tsx:378（应在 step 1 加 `<a href="/static/extensions/infill-xhs-scraper-v0.1.0.zip" download>` 或主按钮）
- `[MEDIUM][VISUAL_DEVIATION]` 与 `cuj-1-no-extension.html` 相比缺少"下载扩展 zip (12 KB)"primary 按钮 — XhsTab.tsx:391
- `[LOW][BUG]` `POST /api/auto-import/xhs/probe` 是占位实现（永远返回 has_xhs_tab=true） — backend/app/routers/auto_import.py:99（违反 AC #4「探查千帆 tab」语义，但实际扩展探活由前端 `chrome.runtime.sendMessage` 完成，可视为「后端意图保留 hook」）

---

### CUJ-2: 扫描闲鱼订单 — FAIL

**关键 HIGH 缺陷**：probe 端点的 `adb_connected` 用 `bool(list_devices())` 计算 — 即任何 ADB 设备存在即认定连接成功，**完全没校验配置的 endpoint 是否匹配**。导致用户在 pc_ip="" / 配置 IP 不可达时仍看到绿色"ADB 已连接"，点击"截屏"后端真实失败，UI 仅一秒钟 toast 即消失，留下黑盒状态。等同伪造扫描就绪 — 属于 fabrication 范畴的 HIGH 级 BUG。

#### Acceptance Criteria
| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 切「闲鱼」tab 渲染橙色主区 + 触发 `POST /api/auto-import/xianyu/probe` | manual | PASS | PASS | PASS |
| 2 | 状态指示器「● ADB 就绪」（绿）/「● ADB 错」（红） | manual | **FAIL**（pc_ip="" 时显示绿色） | **FAIL** | **FAIL** |
| 3 | 控制栏含设备类型下拉 / PC IP / 编辑 link / 4 步操作说明 | manual | PASS | PASS | PASS |
| 4 | 「截屏」disabled 直到 ADB 就绪 + 「完成截屏」disabled 直到 ≥1 张 | manual | **FAIL**（pc_ip="" 时未 disabled） | **FAIL** | **FAIL** |
| 5 | ADB 错态下两按钮 disabled | manual | NOT_RUN（实际看到的是 false-green） | NOT_RUN | NOT_RUN |
| 6 | MVP 不自动滚动；后端不发 `adb shell input swipe` | code | PASS | PASS | PASS（grep 验证无 swipe） |
| 7 | 每次点截屏触发 ADB screencap + 异步 LLM 解析 | both | PASS（automated TestE2E_xianyu_screencap_async + new TestQAGapXianyuScreencapEnvelope） | PASS | PASS |
| 8 | 缩略图条状态徽章 🔄 / ● / ! / ✗ | none | NOT_RUN（无可用设备） | NOT_RUN | NOT_RUN |
| 9 | 截屏与解析重叠跑（异步队列） | automated | PASS（TestE2E_xianyu_screencap_async） | PASS | PASS |
| 10 | 已解析订单 mini 卡片随后端解析完成不断追加 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 11 | 「完成截屏，开始解析」后跑二次 LLM + 跳 CUJ-3 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 12 | 「取消」回 CUJ-2 初始态 + 5 秒灰提示 | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 13 | ADB 连不上时红色 err 块 + 三项实时检查 + 「重新测试 ADB」按钮 + 设置 link | none | NOT_RUN（被 false-green 阻断） | NOT_RUN | NOT_RUN |
| 14 | `adb` 未装时第 1 项 ✗ + 安装命令；设备 offline 时第 4 项追加 | automated | PASS（TestDiagnoseAdb 系列 5 测试） | PASS | PASS |
| 15 | 单次截屏失败时红边 + ✗ 徽章，可继续 | both | PASS（new test_screencap_failure_returns_envelope） | PASS | PASS |
| 16 | LLM 解析失败率 > 30% 时弹 warning | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 17 | 整批 0 单时跳 CUJ-3 空态 | none | NOT_RUN（与 CUJ-3 空态 bug 双重阻塞） | NOT_RUN | NOT_RUN |
| 18 | 小红书扫描进行中时本 tab 按钮 disabled + tooltip | none | NOT_RUN | NOT_RUN | NOT_RUN |

#### Edge Cases & Error States
| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| 默认 pc_ip="" 进入闲鱼 tab | 红色 err 块 + 三项检查 ✗ | **绿色「ADB 已连接」+ 按钮 enabled**（!） | 同 run1 | FAIL |
| 配置 bogus IP（203.0.113.1）后 probe | `adb_connected: false` | `adb_connected: true`（拿到 USB 真机 serial） | 同 run1 | FAIL |
| 配置 endpoint 不通时点截屏 | UI 阻止或 inline 错误 | toast 消失后无残留状态 | 同 run1 | FAIL |

#### Manual Verification Notes
- live probe 三次（pc_ip="", "203.0.113.1", "127.0.0.1"）均返回 `adb_connected:true`，因为我的 Mac 接了真实 Android 手机 USB；只有当 PC 完全没接 ADB 设备时才会偶然返回 false。这是真实的「假阳性」陷阱，所有作坊主在自家局域网首次配置时几乎不会触发，但任何调试 / 切换设备的瞬间都会触发。
- 同 bug 在 CUJ-4 test-adb 端点同样存在（test_adb 也返回 USB 真机的 serial 即便配置的是 BlueStack 端口 — 但因 BlueStack 实际跑在我这台机器的 5555 上，diagnostics 全 ok，不算 false-green。故 CUJ-4 当前未独立标 bug，但根因相同：probe / test-adb 的 `adb_connected` 标识与配置 endpoint 解耦）。
- 截屏失败 envelope 在 new test_screencap_failure_returns_envelope 中明确覆盖：返回 `{ok:false, error_kind:"screencap_failed", error:"..."}`。
- scan-status 在 batch 不存在时不崩，返回空列表（new test_scan_status_empty_batch_returns_empty_lists）。

#### Artifacts
- Screenshots: `docs/qa-artifacts/iter4-22-12-12/cuj-2/run1/{00-initial-xianyu,01-after-screencap-click}.png`，`.../run2/00-initial-xianyu.png`
- Console messages (run1): 1 error (AntD Spin `tip` deprecation — LOW carry-over)
- Console messages (run2): 同上
- Network requests verified: `POST /api/auto-import/xianyu/probe` 200，`POST /api/auto-import/xianyu/screencap` 200 (ok=false)
- Mocks: `cuj-2-initial.html`（**实际看到的是误判后的 initial 态**）、`cuj-2-captured.html`（NOT_RUN）、`cuj-2-parsing.html`（NOT_RUN）、`cuj-2-no-adb.html`（NOT_RUN — 被 false-green 遮蔽）

#### Issues Found
- `[HIGH][BUG]` probe / test-adb 端点的 `adb_connected` 标识仅按"有任何设备"判定，不校验配置 endpoint — backend/app/routers/auto_import.py:248 + :567。应改为「devices 中存在 serial 起始于 pc_ip 或等于 endpoint 的设备且 state=='device'」（diagnose_adb 已有此逻辑，但 probe/test-adb 没复用）
- `[HIGH][BUG]` 前端 XianyuTab 仅基于 `adb_connected` 切换 idle/error_adb 状态，没参考 diagnostics — frontend/src/pages/auto_import/XianyuTab.tsx:58。建议改成「所有 diagnostics 都 ok 才算 idle」

---

### CUJ-3: 预览校对 + 一键导入 — FAIL

后端契约（commit 单事务原子性、`-redoN` override、SKU 不存在整批回滚、partial unique index）全部 live 验证通过。前端预览页因为 CUJ-1/2 入口阻塞未能 end-to-end 验证（手动 happy path），但代码层 grep 发现一个 MEDIUM AC 漏洞：空 batch 空态文案与「返回扫描页」按钮在代码库中根本不存在。preview 状态纯前端态（无 localStorage / sessionStorage 持久化）经 live 验证。

#### Acceptance Criteria
| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | CUJ-1/2 完成自动进预览，面包屑「订单管理 / 自动导入 / 预览批次」 | code | PASS（AutoImport.tsx setMode preview） | PASS | PASS |
| 2 | 顶部页面头含来源 chip + 订单总数 + 副标题 | code | PASS（PreviewTable.tsx 渲染） | PASS | PASS |
| 3 | 4 chips 横排（高/中/低/重复），可点击切「只看本类」 | code | PASS | PASS | PASS |
| 4 | 主表格列：[checkbox] 平台 / 外部订单号 / 买家+下单时间 / 商品 | code | PASS | PASS | PASS |
| 5 | 行底色规则（白/浅黄/浅红/灰） | code | PASS | PASS | PASS |
| 6 | 默认勾选规则（高/中+非重复 → 勾） | code | PASS（deriveInitialChecked） | PASS | PASS |
| 7 | 低置信度行 checkbox disabled + tooltip + 自动转正后勾上 | code | PASS | PASS | PASS |
| 8 | 商品列子行结构：badge / picker / 件数 / 删除 + 末尾「+ 添加商品」 | code | PASS | PASS | PASS |
| 9 | 手指 SKU 后 badge 绿 ✓，picker 框置信度文案「手选」 | code | PASS | PASS | PASS |
| 10 | SkuPicker 浮窗 360px 三段：当前匹配 + 原文 + 候选 + 搜索 | code | PASS | PASS | PASS |
| 11 | 搜索结果为空时浮窗中部「无匹配结果」 | code | PASS | PASS | PASS |
| 12 | 重复订单第 3 列含 `重复` tag + 元信息 + 「改判为新单」link → Modal | code | PASS | PASS | PASS |
| 13 | 改判确认后行变白 + 勾选框自动勾上；导入时绕过唯一约束 | code+live | PASS（live: -redo1 / -redo2 验证） | PASS | PASS |
| 14 | 件数 input 1~999；0/负数红边 + tooltip | code | PASS | PASS | PASS |
| 15 | 「+ 添加商品」/「✕ 删除商品」即时改前端态 | code | PASS | PASS | PASS |
| 16 | 底部 sticky 工具栏 + bulk actions + 两个 primary/secondary 按钮 | code | PASS | PASS | PASS |
| 17 | bulk actions「全选新单 / 全不选 / 反选」行为正确 | code | PASS | PASS | PASS |
| 18 | 「导入勾选」调 `POST /api/auto-import/commit`，loading 期 disabled | code | PASS | PASS | PASS |
| 19 | 后端单事务批量创建 Order + OrderItem；任一失败回滚 | both | PASS（live + automated test_commit_zero_inserts_when_one_sku_missing_mid_batch） | PASS | PASS |
| 20 | 成功页：绿 ✓ + 4 stat 网格 + 批次详情 + 前 5 ID + 跳 /orders link | code | PASS（SuccessPanel.tsx） | PASS | PASS |
| 21 | 失败页：红 ! + 标题 + 错误详情 + 返回预览 / 丢弃二次确认 | code | PASS（FailurePanel.tsx） | PASS | PASS |
| 22 | batch 为空时居中空态「未抓取到任何订单」+ 「返回扫描页」按钮 | code | **FAIL**（grep 0 hit） | **FAIL** | **FAIL** |
| 23 | batch 全是重复时顶部黄 alert + 两个 link | code | PASS（PreviewTable.tsx 渲染） | PASS | PASS |
| 24 | 预览批次不持久化（刷新即丢） | live | PASS（localStorage / sessionStorage 都空） | PASS | PASS |
| 25 | commit 单事务：mid-batch SKU 缺失整批回滚（零写入） | automated | PASS（new test_commit_zero_inserts_when_one_sku_missing_mid_batch） | PASS | PASS |

#### Edge Cases & Error States
| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| commit 中 1 单 SKU 不存在 | 整批回滚 + DB 0 写入 + error_kind=commit_sku_not_found | PASS（live + automated）| PASS | PASS |
| 同 external_order_id override 两次 | DB 含 base / -redo1 | PASS（live REDO-LIVE-001 + REDO-LIVE-001-redo1）| PASS | PASS |
| 多个手工录单（platform=NULL）共存 | partial unique index 允许多行 NULL | PASS（live 6 个 NULL/NULL 行）| PASS | PASS |
| 重复且未 override | 静默跳过 + 计入 dup_skipped 统计 | PASS（automated）| PASS | PASS |
| 预览刷新页面 | batch 丢失，回到初始 | PASS（localStorage 空 + Mode 默认 tabs）| PASS | PASS |

#### Manual Verification Notes
- 前端 PreviewTable / SkuPicker / SuccessPanel / FailurePanel 代码完整、字段与契约一致；因为无扩展环境无法走完整 happy path UI 渲染，标 code (静态)而非 manual。
- live 验证三类后端契约：commit 原子性（mid-batch SKU 缺失整批回滚）、-redoN 后缀（连续 override 递增）、partial unique index（多个 manual 共存）— 全部通过。
- React state 持久化检查：navigate 到 `/orders/import` 后 `Object.keys(localStorage)` / `sessionStorage` 均为空 — stateless preview 声明真实。
- 缺失：空 batch 空态 UI。`grep -rn "未抓取到\|返回扫描页" frontend/src/` 0 hit — PRD CUJ-3 AC 第 22 项「batch 为空时居中空态」违约。

#### Artifacts
- Screenshots: 无（preview UI 未触发；后端契约通过 curl + sqlite 验证）
- Console messages: 无（preview 未渲染）
- Network requests verified: `POST /api/auto-import/xhs/scan`（live），`POST /api/auto-import/commit`（live 多次），SQLite 直接验证
- Mocks: `cuj-3-initial.html`（NOT_COMPARED — 未渲染）、`cuj-3-success.html`（NOT_COMPARED）

#### Issues Found
- `[MEDIUM][BUG]` 缺空 batch 空态 UI「未抓取到任何订单」+ 「返回扫描页」按钮 — frontend/src/pages/auto_import/PreviewTable.tsx（应在 items.length === 0 时渲染空态卡片）
- `[LOW][BUG]` 既往 TL 已标：N+1 重复查询 + 串行 LLM 调用 + commit 无 payload 大小限制（性能 / 安全 carry-over）

---

### CUJ-4: 自动导入设置 — FAIL

GET/PUT xianyu config roundtrip 验证通过；设备类型下拉端口自动填正确（MuMu→7555 / 蓝叠→5555 / 雷电→5555 / USB→5037 — 三个全部 live 验证）；视觉布局对齐 `cuj-4-initial.html`。同 CUJ-2 HIGH bug：test-adb 端点在配置不可达 endpoint 时仍可能误报「连接成功」（仅因有其它 ADB 设备）。

#### Acceptance Criteria
| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 「系统设置」下「自动导入」/ URL `/settings/auto-import` | manual | PASS | PASS | PASS |
| 2 | 进入页面并发调 extension-status + xianyu/config | manual | PASS（两个请求并发触发） | PASS | PASS |
| 3 | 面包屑 + 标题 + 副标题 | manual | PASS | PASS | PASS |
| 4 | 两张并列卡片（左小红书红 chip / 右闲鱼橙 chip） | manual | PASS | PASS | PASS |
| 5 | 小红书卡片：已装态显示绿点 + 版本 + 「重新检测」/ 未装态显示蓝点 + 下载 link + 4 步引导 + 「我已安装」 | manual | PASS（未装态显示 ● 未配置 + .env 提示，按设计有意为之 — VITE_INFILL_EXT_ID 未配置时显示提示而非引导引导） | PASS | PASS |
| 6 | 闲鱼卡片含：设备类型下拉 / PC IP / 端口号 / 测试 ADB / 保存配置 | manual | PASS | PASS | PASS |
| 7 | 设备类型变化时端口号自动填默认值 | manual | PASS（live: MuMu→7555 ✓, 蓝叠→5555 ✓, USB→5037 ✓） | PASS | PASS |
| 8 | 「测试 ADB 连接」点击触发 `POST /api/auto-import/xianyu/test-adb`；按钮 loading；结果回显（绿/红框 + 序列号 + 系统 / 三项诊断） | manual | PASS（结果回显正确） | PASS | PASS |
| 9 | 「保存配置」仅在表单字段被改后点亮；触发 PUT；成功 toast | manual+automated | PASS（new test_put_then_get_persists + test_put_overwrites_existing） | PASS | PASS |
| 10 | 测试连接不持久化（必须保存才落库） | code | PASS | PASS | PASS |
| 11 | PC IP 空时「测试 ADB」disabled + tooltip | manual | NOT_RUN（页面允许点击；端点返回 false-green 而非 disabled） | NOT_RUN | NOT_RUN |
| 12 | 端口非数字 / 超出 1~65535 → 红边 + tooltip | manual | NOT_RUN（InputNumber 内建保护） | NOT_RUN | NOT_RUN |
| 13 | 页底 LLM 配置说明条 + link / 灰条样式 | manual | PASS | PASS | PASS |
| 14 | `.env` 未配 `DASHSCOPE_API_KEY` 时说明条变红 | none | NOT_RUN（设计未实现服务端检测） | NOT_RUN | NOT_RUN |
| 15 | 从故障态跳设置页时自动滚 + 卡片 pulse | none | NOT_RUN | NOT_RUN | NOT_RUN |
| 16 | GET / PUT xianyu/config 持久化 | automated+manual | PASS（new TestQAGapXianyuConfigRoundtrip 三测） | PASS | PASS |

#### Edge Cases & Error States
| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| 默认配置（unset）GET | 返回 mumu/""/7555 | PASS | PASS | PASS |
| PUT bluestacks/192.168.1.42/5555 → GET | 一致 | PASS | PASS | PASS |
| PUT 覆盖已有 | 新值生效 | PASS | PASS | PASS |
| test-adb 配置 endpoint 不可达 + 有其它 ADB 设备 | adb_connected: false | adb_connected: true（继承 CUJ-2 HIGH bug） | 同 run1 | FAIL（同根因，已在 CUJ-2 issue 中追踪，本节不重计） |

#### Manual Verification Notes
- 表单初始化、port auto-fill 行为、测试 ADB 显示绿框、save 按钮 disabled-when-pristine 全部 live 验证 OK。
- automated 端：三个新测试 `test_get_config_default_when_unset` / `test_put_then_get_persists` / `test_put_overwrites_existing` 全绿。
- 已知遗留：CUJ-2 的 false-green probe 在此页同样存在（test-adb 端点 `adb_connected` 计算逻辑相同）— 但因此 issue 已在 CUJ-2 处计入 HIGH，此处不重复计算。

#### Artifacts
- Screenshots: `docs/qa-artifacts/iter4-22-12-12/cuj-4/run1/{00-initial-settings,01-after-test-adb}.png`，`.../run2/{00-initial-settings,01-usb-autofill}.png`
- Console messages: 0 错误（CUJ-4 单独无问题）
- Network requests verified: GET `/api/auto-import/xianyu/config`, PUT 同路径, POST `/api/auto-import/xianyu/test-adb`, GET `/api/auto-import/xhs/extension-status`
- Mocks: `cuj-4-initial.html`（**对比执行，视觉对齐**）

#### Issues Found
- 见 CUJ-2 的 `[HIGH][BUG]`（test-adb 与 probe 共用根因，不在本节再计）

---

## Bugs Found

### HIGH
- `[HIGH][BUG]` probe / test-adb 端点的 `adb_connected` 仅按 `bool(list_devices())` 判定，不校验配置 endpoint — 在 pc_ip="" / 配置 IP 不可达时仍亮绿，UI 让用户以为 ADB 就绪 — CUJ-2/CUJ-4 — backend/app/routers/auto_import.py:248,567
- `[HIGH][BUG]` 前端 XianyuTab 仅基于 `resp.ok && resp.adb_connected` 切 idle/error_adb，不参考 diagnostics — 即便后端修了上一条仍要前端联动 — CUJ-2 — frontend/src/pages/auto_import/XianyuTab.tsx:58

### MEDIUM
- `[MEDIUM][BUG]` 扩展未装态缺少 zip 下载链接 / 按钮（违反 PRD CUJ-1 AC「下载扩展压缩包链接」） — CUJ-1 — frontend/src/pages/auto_import/XhsTab.tsx:378
- `[MEDIUM][VISUAL_DEVIATION]` 缺少「下载扩展 zip (12 KB)」primary 按钮，与 `cuj-1-no-extension.html` mock 不符 — CUJ-1 — frontend/src/pages/auto_import/XhsTab.tsx
- `[MEDIUM][BUG]` 预览批次为空（batch.items.length === 0）时缺少「未抓取到任何订单」空态 UI + 「返回扫描页」按钮（违反 PRD CUJ-3 AC #22） — CUJ-3 — frontend/src/pages/auto_import/PreviewTable.tsx

### LOW
- `[LOW][BUG]` `POST /api/auto-import/xhs/probe` 是占位实现（永远 has_xhs_tab=true），不真探活 — CUJ-1 — backend/app/routers/auto_import.py:99
- `[LOW][BUG]` AntD `Spin tip` deprecation console warning（carry-over from iter3） — 全局 — frontend/src/pages/AutoImport.tsx + 其它 Spin 使用点

### LOW（TL review carry-over，已在用户消息标注，不复计入新 bug）
- N+1 重复查询（scan 端点对每条 raw_order 都查一次 DB） — 性能 — backend/app/routers/auto_import.py:160
- 串行 LLM 调用（CUJ-1 每个 product 一次 chat_completion） — 性能 — backend/app/routers/auto_import.py:175
- 无 payload-size limits — 安全 — 全部 `/api/auto-import/*` 端点
- 硬编码后端 URL `http://localhost:8000` — 部署 — extension/background.js
- CORS `allow_origins=["*"]` — 安全 — backend/app/main.py:53

## Coverage Gaps
Acceptance criteria with Coverage = `none`:
- CUJ-1 criterion 7~9, 11~12, 14~16: 扫描中 / 取消 / 错误态等流程需要真实 Chrome 扩展环境，CI 不可达
- CUJ-2 criterion 5, 8, 10~13, 16~18: 截屏卡片渲染 / 缩略图状态 / 完成解析等需要真实 ADB 设备 + emulator + LLM key
- CUJ-3 全部 manual：preview UI 需要先走完整 scan happy path 才能进入，未在 CI 验证
- CUJ-4 criterion 11~12, 14~15: 边界态 + 故障跳转的视觉细节

## New Tests Written
- `backend/tests/test_auto_import.py::TestQAGapXhsExtensionAndProbe::test_extension_status_returns_ok` — CUJ-1 AC #2 + extension-status 契约（无 env 时 installed=false）
- `backend/tests/test_auto_import.py::TestQAGapXhsExtensionAndProbe::test_extension_status_reflects_configured_env` — CUJ-1 + extension-status 契约（有 env 时 installed=true，version 透传）
- `backend/tests/test_auto_import.py::TestQAGapXhsExtensionAndProbe::test_xhs_probe_returns_ok` — CUJ-1 AC #4 + xhs/probe 占位契约（has_xhs_tab=true）
- `backend/tests/test_auto_import.py::TestQAGapXhsExtensionAndProbe::test_xhs_scan_with_production_tuple_path` — CUJ-1 + 生产路径 tuple 解包（TL review 修复点）
- `backend/tests/test_auto_import.py::TestQAGapXianyuScreencapEnvelope::test_screencap_failure_returns_envelope` — CUJ-2 + 截屏失败时 envelope shape
- `backend/tests/test_auto_import.py::TestQAGapXianyuScreencapEnvelope::test_scan_status_empty_batch_returns_empty_lists` — CUJ-2 + 不存在的 batch_id 不崩
- `backend/tests/test_auto_import.py::TestQAGapCommitAtomicityMidBatchSkuDelete::test_commit_zero_inserts_when_one_sku_missing_mid_batch` — CUJ-3 + 单事务原子性（5 单中第 4 单缺 SKU 整批回滚）
- `backend/tests/test_auto_import.py::TestQAGapXianyuConfigRoundtrip::test_get_config_default_when_unset` — CUJ-4 + GET 默认值
- `backend/tests/test_auto_import.py::TestQAGapXianyuConfigRoundtrip::test_put_then_get_persists` — CUJ-4 + PUT/GET roundtrip
- `backend/tests/test_auto_import.py::TestQAGapXianyuConfigRoundtrip::test_put_overwrites_existing` — CUJ-4 + 覆盖既有

## Recommendations
**HIGH 优先（必须在下一轮修，阻塞 PR 合入）：**
1. **CUJ-2/4 `adb_connected` 计算修正**：probe / test-adb 端点改用 diagnostics 第 4 项（device_state.ok）作为「就绪」标志，前端 XianyuTab 改用 `resp.diagnostics.every(d => d.ok)` 切 idle/error_adb。修完后补 ≥ 2 个自动化测试（pc_ip="" → adb_connected=false / 配置不可达 IP + 有其它设备 → adb_connected=false）。

**MEDIUM 优先（下一 iter 修）：**
2. **CUJ-1 下载扩展按钮**：在 XhsTab.tsx 无扩展态加 `<Button type="primary"><a href="/static/extensions/infill-xhs-scraper-v0.1.0.zip" download>下载扩展 zip</a></Button>`，文案参考 `cuj-1-no-extension.html`。
3. **CUJ-3 空态 UI**：PreviewTable 当 `batch.items.length === 0` 时渲染居中空态卡片「未抓取到任何订单 — 请确认 [平台具体说明]」+ 「返回扫描页」按钮（调 onCancel）。

**LOW 优先（积压）：**
4. xhs/probe 占位实现：要么真做（虽然探活由前端走扩展，后端可做 "extension version compat check"），要么去掉这一调用。
5. 修 AntD Spin tip deprecation（globally 替换 `tip` → `description` 属性）。
6. TL review carry-over 性能 / 安全项（N+1 / 串行 LLM / payload limit / CORS / 硬编码 URL）— 与下一个 prd 一起处理。
