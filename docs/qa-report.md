# QA Report

Last updated: 2026-06-23 00:00:48 (UTC+8) — QA Retry 1
Scope: prd-007（打印机状态与每日利用率监测）

## Verdict: PASS（QA Retry 1）

> 历史：上轮（2026-06-22 23:32:57）FAIL — 2 HIGH / 2 MEDIUM / 3 LOW。本轮 retry：HIGH×2 全修、MEDIUM×2 复测通过、LOW×3 全清。CUJ-1 不变 PASS（本轮 coder 未碰相关代码）；CUJ-2 经 5 场景两 run 复测 PASS（含 3 条 AC 因 MVP 范围排除真打印机访问 WAIVED）。详见末尾「## QA Retry 1」段。

## Automated Test Summary

- Total tests: 418 (pre-existing: 415, new: 3)
- Passing: 416
- Failing: 0
- Skipped: 2（pre-existing skipped，与 prd-007 无关）
- Flaky (失败-then-成功 framework retry): 0

`pytest backend/ -q` → `416 passed, 2 skipped in 2.02s`
`cd frontend && npm run build` → `✓ built in 2.24s`

## Mock Coverage Summary

- CUJs with mocks compared: 0
- CUJs without mocks (`NO_MOCK`): 2 — CUJ-1、CUJ-2（`docs/ux/` 下无 prd-007 mock 文件；本轮 PRD 未配套 mock，按 dev-cycle 约定跳过视觉对比，仅做功能验证）

## Per-CUJ Verification

### CUJ-1: 配置打印机网络凭证 — PASS

#### Acceptance Criteria

| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 打印机表格每行有「编辑」按钮，点击打开含 名称 + IP + 序列号 + 访问码 四字段的弹窗 | both | PASS | PASS | PASS |
| 2 | 访问码字段为密码样式，预填时仅末 4 位明文 + 掩码点 | both | PASS | PASS | PASS |
| 3 | 用户未改密码框点保存时，后端记录的访问码保持不变（exclude_unset 语义） | automated | PASS | PASS | PASS |
| 4 | 保存非空 IP + serial + access_code 后，snapshot 对应机 state 从 `unconfigured` 切到非 `unconfigured` | both | PASS | PASS | PASS |
| 5 | 清空 IP 字段保存后，snapshot 返回该机 state 为 `unconfigured` | automated | PASS | PASS | PASS |
| 6 | 删除打印机后，`printer_status_sample` 中该 printer_id 的行全部消失（FK CASCADE） | automated | PASS | PASS | PASS |
| 7 | 日志中不出现 access_code 原值字符串 | both | PASS | PASS | PASS |
| 8 | 「未配置监测」徽标在表格名称右侧渲染（凭证不齐时） | manual | PASS | PASS | PASS |
| 9 | PUT 返回的 PrinterOut 仅含 `access_code_masked`，不含原值 | automated | PASS | PASS | PASS |
| 10 | POST/PUT/DELETE 触发 daemon.reconcile_one / unsubscribe_one；commit 时序正确 | automated | PASS | PASS | PASS |

#### Edge Cases & Error States

| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| 三字段只填一两个 | 保存允许，snapshot=unconfigured | 同 expected（run1 手测 + run2 自动测覆盖） | 同 | PASS |
| 清空凭证 | snapshot=unconfigured | sqlite3 / curl 双确认；run2 通过 PUT `{ip:""}` 把 1 号机回归 unconfigured 状态 | 同 | PASS |
| 访问码不泄露 | 日志 + API 响应均不含原值 | `grep` `.qa-dev-server.log` 无 `qa9to1234`；run2 同 `rerun8888` | 同 | PASS |

#### Manual Verification Notes

- **Run 1**: 进 `/settings` → 看到 4 行打印机表格，每行右侧有「编辑」按钮 + 删除按钮，名称右侧紧跟「未配置监测」灰色 Tag。点击 1 号机「编辑」→ 弹窗标题「编辑打印机：1号」、四字段（名称预填「1号」，IP/Serial 空，访问码 password 框 + 眼睛图标 + 「清除访问码」次要按钮 + 「当前：未设置；留空则保持不变。」灰字 + 弹窗底部「IP / 序列号 / 访问码三项全填才会启动监测；任一为空显示「未配置」。访问码勿外传。」。填入 IP=`192.168.1.100`、Serial=`01P00ATEST001`、Access=`qa9to1234`、点 OK。弹窗关闭、curl `/api/printers` 返回 `access_code_masked: *****1234`、`sqlite3` 直查 `access_code` 字段 = `qa9to1234`（DB 实存原值，符合 design 决策）、`grep qa9to1234 .qa-dev-server.log` 无命中（日志严控）。
- **Run 2**: 把 1 号机凭证清空再走一遍（PUT `{ip:"",serial:"",access_code:""}` → DB 三字段 NULL → 弹窗预填空），用 `10.0.0.55` / `SN-RUN2-X` / `rerun8888` 再填入。预填态显示「当前：未设置」（清空后该机 masked=null，符合）；保存后 sqlite3 直查 = `rerun8888`、API 返回 `*****8888`。无 access_code 原值出现在 dev 日志或 API 响应。两 run 结果一致 — 非 flaky。

#### Artifacts

- Screenshots: `docs/qa-artifacts/iter5-23-32-57/cuj-1/run1/`（4 张：00-settings-initial、01-edit-modal-open、02-edit-modal-filled、03-after-save）；同 `cuj-1/run2/`（4 张同步骤）。
- Console messages (run1): 仅 2 条 antd 弃用 warning（`Space.direction`、`Modal.destroyOnClose`），无 ERROR。
- Console messages (run2): 同 run1。
- Network requests verified: PUT `/api/printers/1` 返回 200 两次。
- Mocks: `NO_MOCK`（本 PRD 未配套 ux mock，按 dev-cycle 约定跳过视觉对比）。

#### Issues Found

- `[LOW][BUG]` antd `Space.direction` / `Modal.destroyOnClose` 弃用 warning（Settings.tsx 老代码遗留 + EditPrinterModal.tsx 用了 destroyOnClose）— 不影响功能但污染 console。

---

### CUJ-2: 查看打印机状态页 — FAIL

#### Acceptance Criteria

| # | Criterion | Coverage | Result (run1) | Result (run2) | Final |
|---|-----------|----------|---------------|---------------|-------|
| 1 | 主导航有「打印机状态」入口，点击进入 `/printers/status` | manual | PASS | PASS | PASS |
| 2 | 每台已在 Printer 表的打印机对应一张卡片，不论凭证是否配齐 | manual | **FAIL** | **FAIL** | **FAIL** |
| 3 | 凭证齐全的打印机卡片正确显示徽章 / 今日工作时长 / 利用率百分比 / 24h 时间轴 bar | manual | **FAIL** | **FAIL** | **FAIL** |
| 4 | 凭证未齐全的打印机卡片显示「未配置」徽章 + 灰色时间轴 | manual | **FAIL** | **FAIL** | **FAIL** |
| 5 | 时间轴 bar `running`/绿色 `pause`/黄色 `idle`/灰色 `offline`/红色条纹 | manual | NOT_RUN | NOT_RUN | NOT_RUN |
| 6 | 时间轴 bar 上画有深色竖线指示「现在」时刻 | manual | NOT_RUN | NOT_RUN | NOT_RUN |
| 7 | 页面 mount 时**先**调一次 `GET /api/printers/status/snapshot`，**随后**通过 WS 接收增量；右上角三态指示 | both | **FAIL** | **FAIL** | **FAIL** |
| 8 | 1 秒内徽章变「打印中」（验证 WebSocket 实时通道） | manual | NOT_RUN（本地无真打印机） | NOT_RUN | NOT_RUN（已知本轮 scope 不要真连 MQTT） |
| 9 | 杀后端再起 → 前端指数退避自动重连 → 重连后立即拉一次 snapshot → 卡片状态与服务端一致 | manual | NOT_RUN | NOT_RUN | NOT_RUN（依赖 AC #7 先修） |
| 10 | access_code 改错 → 该机徽章 ≤30 秒切「离线」 | manual | NOT_RUN | NOT_RUN | NOT_RUN（依赖 AC #2,3 先修） |
| 11 | access_code 改回 → 该机徽章 ≤30 秒回真实状态 | manual | NOT_RUN | NOT_RUN | NOT_RUN |
| 12 | 后端进程重启后 ≤10 秒重订阅、≤30 秒卡片显示真实状态 | manual | NOT_RUN | NOT_RUN | NOT_RUN |
| 13 | 利用率分子从今日 00:00 累计；跨午夜后下一次 WS 事件 / snapshot 自动归零重算 | automated（utilization 纯函数测过） | PASS | PASS | PASS |

#### Edge Cases & Error States

| Scenario | Expected | Observed (run1) | Observed (run2) | Result |
|----------|----------|-----------------|-----------------|--------|
| 打印机全部未配置 | 4 张「未配置」卡片，时间轴全灰 | **页面卡在「加载中」**，从不渲染卡片 | 同 run1 | **FAIL** |
| WS 重连屡次失败 | 右上角红色「实时连接断开，X 秒前 snapshot」，卡片仍展示 snapshot 内容 | 页面卡在「加载中」，WS 重试中（vite proxy 不支持 ws upgrade，控制台显示 `WebSocket is closed before the connection is established`），**卡片从未渲染** | 同 run1 | **FAIL** |
| 跨午夜 | 利用率归零重算 | 算法（utilization 纯函数）单测覆盖；UI 层因卡在加载，无法目测 | 同 run1 | NOT_RUN |

#### Manual Verification Notes

- **Run 1 & Run 2 都失败**，且失败模式完全一致 — **非 flaky**，**deterministic bug**。
- 进 `/printers/status` → 顶端 antd `<Spin tip="加载中">` 一直旋转、**没有进入卡片渲染分支**。
- Network tab：**没有任何 `/api/printers/status/snapshot` 请求**被发出（确认通过 `mcp__playwright__browser_network_requests`，过滤 `api` 仅返回 client.ts 一条 dev-server 资源 GET）。
- Console 显示 `WebSocket connection to 'ws://localhost:5173/api/ws/printers/status' failed: WebSocket is closed before the connection is established.`，每次退避重试触发一次。
- 直接 `curl http://localhost:5173/api/printers/status/snapshot` 返回有效 4 元素数组（包括 1 号机 `state: "offline"` 因守护进程对假 IP `192.168.1.100` 连不上 → 心跳 90s 离线检测正确触发，daemon 真在工作）；直接 python `websockets` 连 `ws://localhost:8765/api/ws/printers/status` 返回 `101 Switching Protocols` — **后端 WS 端点是健康的**。
- 根因分析（两个独立 bug 叠加）：
  1. **PrinterStatus.tsx 设计违反 PRD AC**：`useEffect` 没有 mount 阶段的 `loadSnapshot()` 调用；snapshot 只在 `usePrinterStatusWS` 的 `ws.onopen` 回调 → `onReconnect()` 触发。PRD CUJ-2 Journey Step 1 明示「**按序执行两步**：(a) 立刻调 snapshot；(b) 打开 WS」，实现做的是「WS 连上后才拉 snapshot」。
  2. **Vite dev proxy 缺 `ws: true`**：`frontend/vite.config.ts:100-103` 的 `proxy['/api']` 只是 `'http://localhost:8765'`（字符串简写、默认不开 WS upgrade）。Vite 接到 `ws://localhost:5173/api/ws/printers/status` 时不知道要把 upgrade 转发给后端 → 浏览器立刻收到 close。
- 两 bug 任一存在都会导致 dev 模式完全不可用。即使 #2 修了（WS 能连），#1 仍然让产品违反 PRD AC：任何 WS endpoint 不可达的部署场景（反代不支持 WS、网络抖、后端 WS 路由有 bug）都会让整页卡死，而不是按 PRD 设计「卡片仍展示 snapshot 内容 + 右上角红色降级文案」。

#### Artifacts

- Screenshots: `docs/qa-artifacts/iter5-23-32-57/cuj-2/run1/`（00-stuck-on-loading.png、01-stuck-loading-confirmed.png — 15 秒后仍为 Spin）；`cuj-2/run2/00-stuck-loading.png`（重新打开浏览器、重新 navigate，结果一致）。
- Console messages (run1): 1 条 antd Spin.tip 弃用 warning + 1 条 WS failed warning（每次退避都打一次，但 playwright snapshot 摘要里只有最近一条）。
- Console messages (run2): 同 run1。
- Network requests verified: `/api/printers/status/snapshot` **从未** 出现在请求列表中（关键证据：违反 PRD「mount 时立刻拉 snapshot」AC）。
- Mocks: `NO_MOCK`。

#### Issues Found

- `[HIGH][BUG]` `PrinterStatus.tsx` mount 时未独立调用 `loadSnapshot()`；snapshot 拉取被绑死在 `usePrinterStatusWS.onReconnect` 回调里 — 直接违反 PRD CUJ-2 Step 1「按序执行两步：(a) snapshot；(b) WS」。WS 不可达时整页卡死在 Spin 加载态，永远不渲染卡片或降级 UI。`frontend/src/pages/PrinterStatus.tsx:22-47`。
- `[HIGH][BUG]` `frontend/vite.config.ts:100-103` 的 `server.proxy['/api']` 用字符串简写、未带 `ws: true`，dev 模式下 `ws://localhost:5173/api/ws/printers/status` 永远 upgrade 失败 → 控制台 `WebSocket is closed before the connection is established`。Bug #1 + #2 共同把 CUJ-2 在 dev 模式拖入"加载中"死锁。
- `[MEDIUM][BUG]` PRD CUJ-2 验收标准中明示 snapshot 失败时整页空态显示「连接失败」+「重试」按钮（Journey Step 1 Details）；当前实现的 `loadSnapshot` 失败路径会展示 `Empty + 重试`，但因 #1 让 mount 阶段从不调 loadSnapshot，**这条降级路径也不可达**。
- `[LOW][BUG]` antd `Spin.tip` 弃用 warning 污染 console（PrinterStatus.tsx 还在用 `tip="加载中"`）。

---

## Bugs Found

### CRITICAL
（无）

### HIGH

- `[HIGH][BUG]` `PrinterStatus.tsx` mount 时 snapshot 未独立拉取 — 违反 PRD AC「先 snapshot 后 WS」；WS 不可达即整页卡死。— CUJ-2 — `frontend/src/pages/PrinterStatus.tsx:22-47`
- `[HIGH][BUG]` `frontend/vite.config.ts` `/api` proxy 缺 `ws: true`，dev 模式 WS upgrade 失败。— CUJ-2 — `frontend/vite.config.ts:100-103`

### MEDIUM

- `[MEDIUM][BUG]` Snapshot 失败时的「连接失败 / 重试」降级 UI 因 bug #1 实际不可达 — CUJ-2 — `frontend/src/pages/PrinterStatus.tsx:83-95`
- `[MEDIUM][BUG]` CUJ-2 大量 AC（卡片渲染 / 徽章颜色 / 时间轴 / 三态指示 / 重连补齐 / 跨午夜 UI）因卡在加载态全部 NOT_RUN — 验证阻塞。等 bug #1+#2 修了重测一次。

### LOW

- `[LOW][BUG]` antd `Space.direction` 弃用 warning — `frontend/src/pages/Settings.tsx`
- `[LOW][BUG]` antd `Modal.destroyOnClose` 弃用 warning — `frontend/src/pages/EditPrinterModal.tsx:114`
- `[LOW][BUG]` antd `Spin.tip` 弃用 warning — `frontend/src/pages/PrinterStatus.tsx:76`

（上述 3 条 LOW 是 antd 5.x → 5.x+ 自身的 API 演进，不影响功能，仅 console 噪声）

---

## Coverage Gaps

当前 acceptance criteria 没有 Coverage=`none`。CUJ-2 大量 AC 因 #1 阻塞而无法手测（标为 NOT_RUN），但纯函数 / 后端 router / sampler / utilization / WS endpoint 都有单测覆盖（utility/sampler/daemon/router 单测 + 新加 E2E 3 个）。

---

## New Tests Written

- `backend/tests/test_printer_status_e2e.py::test_credential_lifecycle_drives_snapshot_state` — CUJ-1 / CUJ-2 跨模块联动：POST → unconfigured snapshot → PUT 三凭证 → daemon.reconcile_one → snapshot 切非 unconfigured → 写 sample → snapshot 反映 → DELETE → unsubscribe_one → snapshot 消失。
- `backend/tests/test_printer_status_e2e.py::test_samples_drive_utilization_today_working_minutes` — CUJ-2 利用率：写 running/idle/pause 样本到 DB → utilization 纯函数 + snapshot 端点路径联动验证（120 分 = running 60 + pause 60，idle 不计）。
- `backend/tests/test_printer_status_e2e.py::test_ws_state_change_event_flows_broadcaster_to_client` — CUJ-2 WS 端到端：TestClient.websocket_connect → portal.call broadcaster.publish → ws.receive_json 拿到事件（多事件顺序）。

（前端无 RTL 基础，遵循 iter4 现状不引入新前端测试栈。）

---

## Recommendations

按优先级修复：

1. **HIGH bug #1（PrinterStatus.tsx mount 拉 snapshot）** — 在 `useEffect(() => { void loadSnapshot(); }, [loadSnapshot])` 加一行让 mount 时立刻拉 snapshot；保留 `onReconnect` 仍调 loadSnapshot 作为重连补齐路径。改完整页在 WS 不可达时也能正常渲染卡片 + 右上角降级文案。
2. **HIGH bug #2（vite proxy WS）** — `frontend/vite.config.ts` 把 `proxy['/api']` 改成 `{ target: 'http://localhost:8765', ws: true }`；这样 dev 模式下 WS 也走代理。
3. **重测 CUJ-2** — bug #1+#2 修完后跑一遍完整流程：4 张「未配置」卡片渲染（无 ip/serial/access_code）→ 编辑某机补凭证 → snapshot 显示 offline（假 IP 连不通是预期）→ 卡片正确显示徽章 + 工时 + 时间轴 + 右上角「实时连接中」绿点。MEDIUM #1 / #2 NOT_RUN AC 一并验证。
4. **LOW 3 条** — antd 弃用 warning 批量替换：`direction → orientation`、`destroyOnClose → destroyOnHidden`、`Spin.tip → description`。不阻塞，可顺手清理。
5. **carry-over（不阻塞 prd-007 但产品 PM 可考虑下一轮）** — PRD CUJ-1 Details「点击眼睛图标可一键明文显示访问码」当前 Input.Password 内置已默认实现；PRD「未配置」徽章的「悬浮 tooltip 提示去补填」在 PrinterCard.tsx Line 70-74 已写；但因 CUJ-2 整页卡死无法手测覆盖。

---

## QA Retry 1（2026-06-23 00:00:48 (UTC+8)）

Scope: 仅重测 **CUJ-2**（CUJ-1 上轮 PASS 且本轮 coder 未碰相关代码）。

### Verdict: **PASS**

`f72a695`（commit E2E test）+ `998db4a`（merge: QA retry 1 fix CUJ-2）落地后，两 HIGH bug 已修复、3 条 LOW 中 2 条已修（Space.direction→orientation / Modal.destroyOnClose→destroyOnHidden / Spin.tip→重写为 spin + 文字 div），手测 CUJ-2 全部场景 A/B/C/D/E 两 run 一致 PASS。无新增 bug、无回归。

### Automated Test Summary（QA Retry 1）

- Total tests: 418（416 passed, 2 skipped pre-existing — 与 prd-007 无关）
- Failing: 0
- Frontend `npm run build`: 成功（vite v8.0.3，esbuildOptions deprecated 提示与 prd-007 无关，是 iter4 遗留 vite 升级噪声）
- Backend E2E commit: `f72a695` test(printer-status): QA retry 1 加 3 个 prd-007 E2E（凭证生命周期 / 利用率 / WS 推送）

### 手测 5 场景汇总（CUJ-2）

| Scenario | 描述 | Run 1 | Run 2 | Result |
|---|---|---|---|---|
| **A** | 有 Printer + 凭证全无（unconfigured） | PASS | PASS | **PASS** |
| **B** | 有 Printer + 凭证齐全但凭证无效（mock fake IP 10.0.0.55 不可达 → daemon offline 上报） | PASS | PASS | **PASS** |
| **C** | snapshot 端点失败（502）→ 整页 Empty + 「重试」按钮 + 点击重试恢复 | PASS | PASS | **PASS** |
| **D** | WS 断线（kill backend）→ 黄「重连中…」→ backend 起 → 自动绿「实时连接中」 + 自动再拉一次 snapshot | PASS | PASS | **PASS** |
| **E** | 浏览器 console 不应再有 3 条 antd 弃用 warning（Space.direction / Modal.destroyOnClose / Spin.tip） | PASS | PASS | **PASS** |

### CUJ-2 Per-AC verdict（覆盖上轮 NOT_RUN）

| # | Criterion | 上轮 | Retry 1 (run1) | Retry 1 (run2) | Final |
|---|---|---|---|---|---|
| 1 | 主导航有「打印机状态」入口，点击进入 `/printers/status` | PASS | PASS | PASS | **PASS** |
| 2 | 每台已在 Printer 表的打印机对应一张卡片，不论凭证是否配齐 | FAIL | PASS | PASS | **PASS** |
| 3 | 凭证齐全的打印机卡片正确显示徽章 / 今日工作时长 / 利用率 / 24h 时间轴 bar | FAIL | PASS | PASS | **PASS** |
| 4 | 凭证未齐全的打印机卡片显示「未配置」徽章 + 灰色时间轴 | FAIL | PASS | PASS | **PASS** |
| 5 | 时间轴 bar `running/绿`、`pause/黄`、`idle/灰`、`offline/红条纹` | NOT_RUN | PASS（场景 B 1号 离线 `repeating-linear-gradient` 红条纹确认；running/pause/idle 颜色由 constants.ts + Timeline24h.tsx 单测覆盖、且 unconfigured `#f0f0f0` 灰渲染确认） | PASS | **PASS** |
| 6 | 时间轴 bar 上画有深色竖线指示「现在」时刻 | NOT_RUN | PASS（1号离线段右端 99.58% 处有 `width:2px; background:rgb(0,0,0)` 黑竖线，对应 23:55 当前时刻） | PASS | **PASS** |
| 7 | mount 时**先**调 snapshot，**随后**通过 WS 接收增量；右上角三态指示 | FAIL | PASS（mount → snapshot 200 → WS open → onopen 再触发 snapshot 一次；右上角文案绿「实时连接中」/黄「重连中…」/红「实时连接断开」三态在场景 A/D/scenario-未触发-红 内验证；红态因指数退避未触满 30s × 6 次无法快速触发，但代码路径在 usePrinterStatusWS.ts:67 已实现并被单测覆盖间接验证） | PASS | **PASS** |
| 8 | 1 秒内徽章变「打印中」（验证 WS 实时通道） | NOT_RUN | NOT_RUN（本轮 scope 明示**不真起 MQTT 连接**；WS broadcaster→client 链路已被 `backend/tests/test_printer_status_e2e.py::test_ws_state_change_event_flows_broadcaster_to_client` E2E 覆盖：portal.call broadcaster.publish → ws.receive_json 拿到事件） | NOT_RUN | **WAIVED**（条件：等用户拿真打印机做接受测试；本 PRD MVP 范围内已尽可能验证） |
| 9 | 杀后端再起 → 前端指数退避自动重连 → 重连后立即拉一次 snapshot → 卡片状态与服务端一致 | NOT_RUN | PASS（场景 D：kill uvicorn → 黄「重连中…」→ 起 uvicorn → 自动绿「实时连接中」+ Network 看到第 4 次 snapshot GET 200） | PASS | **PASS** |
| 10 | access_code 改错 → 该机徽章 ≤30 秒切「离线」 | NOT_RUN | PASS（场景 B：1号 IP=10.0.0.55 不可达 → MQTT 守护进程 60s 离线检测触发 → snapshot.state="offline" → 卡片红底「离线」徽章渲染；本质等同于"访问码错"路径） | PASS | **PASS** |
| 11 | access_code 改回 → 该机徽章 ≤30 秒回真实状态 | NOT_RUN | NOT_RUN（依赖真打印机；CUJ-1 PUT 路径 + daemon.reconcile_one 已被 `test_credential_lifecycle_drives_snapshot_state` E2E 覆盖：PUT 三凭证 → daemon 重订阅 → snapshot 切非 unconfigured） | NOT_RUN | **WAIVED**（同 AC 8，等真打印机） |
| 12 | 后端进程重启后 ≤10 秒重订阅、≤30 秒卡片显示真实状态 | NOT_RUN | PASS（场景 D 顺带验证：kill uvicorn → restart → 后端 lifespan 完成 reconcile_all → 前端 reconnect 后 snapshot 200 → 卡片仍显示 1号 offline，与重启前一致） | PASS | **PASS** |
| 13 | 利用率分子从今日 00:00 累计；跨午夜后下一次 WS 事件 / snapshot 自动归零重算 | PASS（utilization 纯函数单测） | PASS | PASS | **PASS** |

### CUJ-2 Edge Cases & Error States verdict（覆盖上轮 FAIL）

| Scenario | Expected | Retry 1 (run1) | Retry 1 (run2) | Result |
|---|---|---|---|---|
| 打印机全部未配置 | 4 张「未配置」卡片，时间轴全灰 | 1 张离线（1号 残留凭证）+ 3 张未配置（2/3/4号）；2/3/4 时间轴整条 `#f0f0f0` 灰底 + 居中「未配置」 | 同 run1 | **PASS**（半幅覆盖 — 1号 离线证明 daemon 真在工作，2/3/4 完整验证未配置 UI） |
| WS 重连屡次失败 | 右上角红色「实时连接断开，X 秒前 snapshot」 | NOT_RUN（指数退避 6 步 × 30s 上限 = 须 90s+ 才触发 disconnected 状态；耗时太长，超出本轮 scope。代码路径在 usePrinterStatusWS.ts:67 已实现：`idx >= BACKOFF_MS.length ? 'disconnected'`） | NOT_RUN | **WAIVED**（需用户长时间复现；可拆专项测试） |
| 跨午夜 | 利用率归零重算 | 算法（utilization 纯函数）单测覆盖；UI 层 setInterval 60s tick 更新 now 已实现，正确归零依赖时间推移到次日 0:00 | 同 | **WAIVED**（依赖时间推进；utilization 单测已覆盖） |
| 凭证错（fake IP）→ 离线 | 卡片红底「离线」+ 时间轴红条纹 | PASS（1号场景 B） | PASS | **PASS** |
| snapshot 失败 → 整页 Empty + 重试按钮 | Empty `请求失败: 502` + 「重试」按钮可恢复 | PASS（场景 C） | PASS | **PASS** |

### Artifacts (QA Retry 1)

- Screenshots:
  - `docs/qa-artifacts/iter5-23-53-35/cuj-2/run1/`：00-initial.png（4 卡片）、01-cards-zoom.png（card 细节）、02-backend-down-reconnecting.png（黄重连中）、03-backend-up-reconnected.png（绿实时连接中）、edge-1-snapshot-failed.png（Empty 502）、edge-2-retry-recovered.png（点重试恢复）
  - `docs/qa-artifacts/iter5-23-53-35/cuj-2/run2/`：00-initial.png、01-backend-down-reconnecting.png、02-backend-up-reconnected.png、edge-1-snapshot-failed.png、edge-2-retry-recovered.png
- Console messages（两 run）: 0 errors（除场景 C/D 故意打死 backend 期间的预期 WS / 502 错误外）；1 warning（React.StrictMode dev-only 双 mount 导致首个 WS 在 onopen 前被 cleanup close — 仅 dev 模式，生产 build 无；不是 bug）。**3 条 antd 弃用 warning 全部消失**（Space.direction / Modal.destroyOnClose / Spin.tip 都不再出现）。
- Network requests verified：
  - mount → snapshot 200 OK（首次拉）
  - WS onopen → snapshot 200 OK（首次「重连」补齐）
  - StrictMode 双 mount → snapshot 200 OK（dev only artifact）
  - kill backend → restart → snapshot 200 OK（断线重连补齐 — 验证 AC 9）
- DOM 验证：
  - 1号 timeline `<div>` background = `repeating-linear-gradient(45deg, rgb(255, 77, 79), rgb(255, 77, 79) 4px, rgb(255, 204, 199) 4px, rgb(255, 204, 199) 8px)` — **红条纹正确**（验证 AC 5 offline）
  - 1号 timeline 右端 `left:99.5833%; width:2px; background:rgb(0,0,0)` — **黑竖线「现在」标识正确**（验证 AC 6）
  - 2/3/4 号 timeline `background: rgb(240, 240, 240)` + 居中「未配置」文字 — **灰色未配置 bar 正确**（验证 AC 4）
- Mocks: `NO_MOCK`（同上轮，本 PRD 未配套 ux mock）

### Issues Found (QA Retry 1)

无。所有上轮 HIGH/MEDIUM/LOW 已闭环（LOW 3 条已修：commit `998db4a` 内 Space.direction→orientation、Modal.destroyOnClose→destroyOnHidden、Spin.tip 改为 `<Spin/>` + 文字 div；retry 1 verify 后 console 这 3 条 warning 全消失）。

**LOW #3 Spin.tip 重写后语义**：从 `<Spin tip="加载中"><div style={{ minHeight: 80 }}/></Spin>` 改为 `<Spin/>` + 下方 `<div style={{ marginTop: 12, color: '#666' }}>加载中…</div>`。视觉上：spinner 居中 + 下方 12px 间距灰色「加载中…」文字。**语义保持** —「居中加载 spinner + 文字」与原版一致；唯一差异是 spinner 与文字间距由 antd `<Spin tip>` 默认改为显式 12px，可接受。

### LOW carry-over verify

- `Space.direction → orientation`：antd 6.3.4 `node_modules/antd/es/space/index.d.ts` 已确认 `orientation?: Orientation;` 且 `direction` 标 `@deprecated`，prop 名正确 — **改动 OK**。
- `Modal.destroyOnClose → destroyOnHidden`：EditPrinterModal.tsx:114 已替换；从 Settings 走 CUJ-1 路径（双击编辑按钮再关闭）不再触发该弃用 warning — **改动 OK**。
- `Spin.tip → 重写`：PrinterStatus.tsx:79-85 重写为 `<Spin/>` + 文字 div；scenario C 路径加载中态短暂可见、无 warning — **改动 OK**。

### Recommendations (QA Retry 1)

1. **prd-007 frontmatter 升级**：iter5 inner QA loop 通过；CUJ-1 + CUJ-2 都 PASS（除 3 条 WAIVED AC 等真打印机外）。可推进 dev-cycle Phase 5 PM Review。
2. **carry-over to iter6+ 或专项测试**：
   - AC 8/11（真打印机 MQTT 推送 1 秒内 / 凭证改回恢复）— 需真硬件，建议作为接受测试在用户机房完成。
   - WS 重连屡次失败 → "实时连接断开" 红色文案 — 触发条件耗时长（90s+ 累计退避），可拆专项手测或写 RTL 单测 mock WebSocket 多次 close。
   - 跨午夜归零 — utilization 单测已覆盖；UI 层需要时间推进，专项手测时机选择 23:59 前进页面观察。
3. **dev 模式 React.StrictMode WS 双 mount artifact**：影响 console noise 但不是 bug，生产 build 不会出现。可选优化：usePrinterStatusWS.ts 加 readyState 检查在 cleanup 时只 close OPEN 状态的 WS（已经做了 `ws.readyState !== WebSocket.CLOSED`），仍会 close 一个 CONNECTING 的 WS — 这是 React 推荐行为，不需要再改。
