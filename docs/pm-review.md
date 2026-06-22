# PM Review

Last updated: 2026-06-23 00:30:00 (UTC+8)
Iteration: 5（prd-007 打印机状态与每日利用率监测 — CUJ-1/2 端到端实现）
Scope: prd-007-printer-status 2 个 CUJ（CUJ-1 配置打印机网络凭证 / CUJ-2 查看打印机状态页）。prd-006 仍 active 待 PM 重新评审；prd-005 在 iter3 已 completed；prd-000~004 不在本轮范围。

---

## Iter5 — prd-007

### Overall Assessment

iter5 把"作坊主肉眼跑去看 LCD 才知道哪几台还在打"压到了"打开页面 4 张卡片一眼扫完"的工程骨架 — CUJ-1 编辑弹窗（名称+IP+Serial+访问码 + access_code 三态：unchanged/set/cleared）+ CUJ-2 状态页（4 卡片 + 状态徽章 + 利用率 + 24h 时间轴 + WS 三态指示器）端到端落地；后端契约（凭证 partial update + reconcile_one / unsubscribe_one + utilization 纯函数 + Broadcaster fanout + WS endpoint）扎实。QA Retry 1 把首轮 2 HIGH（mount 不拉 snapshot / vite proxy 缺 ws:true）+ 2 MEDIUM + 3 LOW（antd deprecation）全部闭环，11/13 AC PASS。

但走完整 product walk（启动 backend + frontend、亲手点编辑弹窗、亲眼看时间轴渲染、亲手杀 backend 看失败态）后我有**两个 CUJ 都判 Caveats** — 主流程跑得通，作坊主能完成"补凭证 + 查状态"，但有若干 PRD 写了 / 实现取了简化方向 / 用户首次体验会困惑的体感毛刺。

**Per-verdict 计数**：Satisfied = 0；Caveats = 2（CUJ-1、CUJ-2）；Not done = 0。

价值校验：作坊主拿到"打开页一眼看哪几台在打 / 今日跑了多少"的产品承诺，在以下条件下成立 ——
1. 4 台机都在同一局域网、IP 可达、access_code 正确（PRD 关键约束 #4：MQTT 握手失败统一显示离线，用户得自己排查）
2. 守护进程没崩、后端没重启（PRD 关键约束 #1：进程崩了丢订阅状态、重启窗口期假象离线）
3. 用户认知到"24h bar = 自然日 0:00→现在"而不是"最近 24 小时滚动窗口"（PRD 设计原意，但用户首次心智可能错位）
4. 用户知道访问码在打印机 LCD 哪里找（PRD CUJ-1 Step 1 写了"placeholder「LCD → 设置 → 网络 → 访问码取得」"，但实现的 placeholder 是"留空保持不变，输入新值覆盖"覆盖掉了来源提示）

满足这 4 条时主流程顺畅。不满足任一条时 fallback 路径**没那么 actionable**：离线徽章没有"为什么 / 怎么办"指引；时间轴没有 legend；snapshot 失败时弹"请求失败: 502"对作坊主不友好。这不是阻断发布的 bug，是 v1 起步形态对体感的妥协。**整体可接受，但建议下一 iter 用半个 sprint 收 UX 抛光 + 真硬件接受测试再升 completed。**

### Walk 覆盖

- CUJ-1：8/9 Journey Step + Edge case「清除凭证 / 编辑名称未改 / 三字段只填一两个」3 项 + AC 1/2/3 walk（gen real browser；clear 路径走通；access_code 三态实测；自然日时间轴渲染观察）
- CUJ-2：6/6 Journey Step + Edge case「打印机全部未配置 / snapshot 失败 / 离线段渲染 / 三态指示器 / Empty 重试」5 项 + AC 1/2/3/4/5/7 walk（real browser + curl 直查 snapshot / DB sample / daemon 心跳行为）
- 真打印机相关 AC 8/11/edge「WS 重连屡次失败」**未 walk**（无真硬件，且超出 dev-cycle 本轮 scope，沿用 QA WAIVED 处理）

### Per-CUJ Verdict

#### CUJ-1（prd-007）: 配置打印机网络凭证 — Caveats

**QA verdict** (from qa-report.md): PASS（retry 1）
**PM verdict**: Caveats

**Assessment**:

按用户"作坊主拿到打印机 → 在 LCD 上找 IP / Serial / Access Code → 进系统补凭证 → 编辑弹窗 → 保存 → 状态页看徽章切活"路径 walk —

1. **入口**：「系统设置 → 打印机管理」表格每行右侧有「编辑」按钮 + 删除按钮；名称右侧若三字段缺一则跟一个「未配置监测」灰色 Tag。AC #1 / #8 PASS。**这是 PRD CUJ-1 + iter4 PM Review prd-004 差异 #6（改名无 UI 入口）的同时解决** — 一个入口承担两个职责（改名 + 补凭证），心智合理。

2. **编辑弹窗 — 整体结构对**：4 字段（名称 / IP / Serial / 访问码）纵向排列，访问码是 password 框（带 antd 内置眼睛图标），底部一段灰色 hint「IP / 序列号 / 访问码三项全填才会启动监测；任一为空显示「未配置」。访问码勿外传。」结构与 PRD 描述一致。

3. **「Cancel」「OK」按钮是英文 — 真实损失**：弹窗底部 antd 默认 footer 按钮渲染为 `Cancel` / `OK` 而非中文。这是 antd 6.x 没在 `ConfigProvider` 注入 `zh_CN` locale 的副作用 — 整页中文里突然两个英文按钮，**作坊主首次进来会停顿一秒**「我点哪个 / 保存是哪个」。这是 v1 体感的真实瑕疵。**PRD AC 没明示按钮文案，但作为 UX 标准这条该补**。属于 LOW UX bug 范畴，但发生在产品最关键的"保存凭证"路径上，建议下一 iter 必修。

4. **「访问码勿外传」OK 但缺关键引导**：PRD CUJ-1 Step 1 明确写**「访问码 Access Code」字段的 placeholder「LCD → 设置 → 网络 → 访问码取得」**。实际实现 placeholder 是 `留空保持不变，输入新值覆盖` — 这覆盖了 "编辑/不编辑" 的语义提示，但**完全丢失了"去哪里找访问码"的引导**。同样，IP / Serial 字段的 placeholder 是格式示例（`192.168.1.123` / `01P00A123456789`），也没告诉首次用户"这两个在打印机 LCD 哪一页找"。**实际影响**：作坊主第一次配 4 台机时，得自己去 google / 翻 Bambu 文档才知道访问码在 "LCD → 设置 → 网络"。**这是 PRD 写了 / 实现没做的明确差异点**，属于 spec-impl drift，且后果直接（多花 5~10 分钟找信息）。

5. **「清除访问码」link 太显眼且无二次确认 — 真实误点损失风险**：实现把「清除访问码」做成 antd `Button type="link"`，**贴在 label 行右侧**（CLEAR_BTN_BOX 实测在 label 同一行 y=410），蓝色文字、cursor pointer。**首次进编辑弹窗的作坊主可能本能去试点它看会怎样** → 进入 cleared 模式（access_code 字段下方变红字 "将清除访问码"）→ 用户没特别留意 / 顺手点 OK → DB access_code 被 NULL 化 → 守护进程立刻 unsubscribe 该机。**虽然有 in-place warning text 显示，但**：
   - 没有 antd Popconfirm 二次确认
   - 没有 "如果你只是想关掉这台机的监测，请改去删整行" 的引导
   - 操作不可撤销（清掉后用户得回去 LCD 重抄访问码）
   
   **建议下一 iter 把「清除访问码」改为 Popconfirm 包一层「确定清除？需要重新到打印机 LCD 抄写 8 位访问码才能恢复监测」二次确认**。code 改动 5 行。
   
   PRD CUJ-1 Step 1 Details 没明示二次确认要求 — 这是 spec gap，**PM 角度建议补**。

6. **三态 access_code（unchanged/set/cleared）的设计是好的**：用户没动密码框 → 不发送 access_code → 后端保留旧值；输了新值 → 发送新值；点了清除按钮 → 发送空串。代码 `EditPrinterModal.tsx:60-82` 把这三态用 `accessCodeMode` 显式建模，**比"靠输入框是否 dirty 推断"更稳**。`exclude_unset` 在后端配合得严丝合缝。AC #2 / #3 / #9 PASS。

7. **「当前：****8888；留空则保持不变」hint**：清晰直观（已实测 `CURRENT_UNSET=1`、密码框预填空状态下显示 "当前：未设置"），让用户知道"我现在看到的是掩码 / 未设置"。这条做得好。

8. **批量新增弹窗保持只填名称**：PRD CUJ-1 Step 3 明确保留这条快路径（凭证每台都不一样 → 不混入批量弹窗）。Settings.tsx 的批量新增 modal 没改，OK。

9. **删除打印机 + FK CASCADE**：AC #6 自动测覆盖 + 路由 unsubscribe_one 有 try/except 容错（`c469c8f` TL fix）。

10. **凭证错时统一显示「离线」(PRD ack)**：PRD Edge case 已 ack "不区分网络不通 / 凭证错"。但**离线徽章本身在状态页上没有 actionable 提示**（"凭证可能错 / 网络可能不通 / 点这里重试" 等），用户得自己回 Settings 编辑。这条**算 CUJ-2 的 caveat 不算 CUJ-1**，但与 CUJ-1 是配套关系，体感上是一体的。

**Caveats / gaps**:
- 「Cancel」「OK」按钮文案英文（antd locale 未注入 zh_CN）— v1 真实体感瑕疵
- 访问码字段 placeholder「LCD → 设置 → 网络 → 访问码取得」**未实现** — 与 PRD CUJ-1 Step 1 直接 drift；首次用户找凭证多走 5~10 分钟
- IP / Serial 字段 placeholder 仅给格式示例，缺"去 LCD 哪一页找"引导
- 「清除访问码」link 太显眼 + 无二次确认 — 误点直接清掉凭证（有 in-place warning text 但弱保护）

**Spec gap**:
- PRD CUJ-1 Step 1 Details 没明示「清除访问码」是否需要二次确认 — 建议下一 iter 修 PRD 加这条 + 加 Popconfirm
- IP / Serial 字段的"去 LCD 哪一页找"提示要不要加（PRD 只提到访问码 placeholder，对 IP / Serial 未明示） — 建议下一 iter 把三字段引导都加上

---

#### CUJ-2（prd-007）: 查看打印机状态页 — Caveats

**QA verdict** (from qa-report.md): PASS（retry 1）
**PM verdict**: Caveats

**Assessment**:

按用户"作坊主想知道当下哪几台还在打 / 哪几台空 / 今天产能"路径 walk —

1. **入口 + 4 卡片网格**：主导航「打印机状态」入口 click 进 `/printers/status`，渲染 4 张卡片网格，标题「打印机状态」+ 右上角 ✓「实时连接中」绿点。响应式 4/2/1 列断点（lg/sm/xs）实测在 1440 视口下 4 列。AC #1 / #2 PASS。**导航顺序：仪表盘 → 产品目录 → 产品录入 → 订单管理 → 库存管理 → 排班中心 → 打印机状态 → 系统设置** — PRD 写「在 Dashboard 与系统设置之间」字面上达成（中间夹了多个旧菜单），但**意图上是「Dashboard 旁边 + 系统设置旁边」**，实际位置是"系统设置左侧"。这条没违 spec，但**首次用户可能找一会儿**（菜单一长串中间）— 建议下一 iter 考虑把"打印机状态"提到主导航靠前位置（如 Dashboard 之后），或者在 Dashboard 加一个状态卡片直跳。

2. **状态卡片版式 — 紧凑且信息密度对**：标题（左：1号 加粗 / 右：状态徽章）、主体（今日已工作 X 分 / 24 小时、利用率 Z.Z%）、底部 24h timeline + 0/6/12/18/24 刻度。视觉上**第一眼能扫完 4 张** — 心智负担低。AC #3 / #5 / #6 PASS。

3. **「打印中」徽章呼吸点动画**：`PrinterCard.tsx:46-58` 实测代码层实现了 6px 小圆点 + `printerStatusPulse` 1.4s 呼吸动画，**但本次实测没有真机 running 状态可观察** — 沿用 QA WAIVED 处理。

4. **24h 时间轴 — 这是最大的体感问题**：实测 1 号机（凭证齐全但 IP 不通）渲染为「时间轴左端 1.6%（00:00~00:23）一条小红条纹 + 右侧 98.4% 全空白灰底 + 黑色"现在"竖线在 1.6% 处」。**作坊主第一眼会困惑**：「我家 1 号离线了一晚上啊，怎么 bar 只有最左边一小段红？」
   
   根因是 PRD 设计 「24h bar = 自然日 0:00~24:00（黑竖线指示当前时刻）」，**但用户的心智模型是「24h bar = 最近 24 小时滚动窗口（黑竖线在右端）」**。当用户在凌晨 00:23 看时间轴时（实测当下时间），这两个口径产生最大差异：自然日口径下整 bar 左端 1.6% 有内容、右端 98.4% 是"未来"灰；滚动口径下 bar 整条 24h 都填满了历史。
   
   **PRD 设计有意按自然日切**（"利用率分母固定自然日 24h，按服务器本地时区切日"）。这是合理的 — 利用率分母固定 1440 让早晨 0~6 点不会出现 utilization%=NaN 或者前一天数据污染。**但 UX 没消化这个模型** — 没有任何视觉信号告诉用户「右半部分灰底 = 今天还没到 / 不是数据缺失」。
   
   **建议下一 iter**：
     - 在时间轴下方刻度旁加一个小字 hint "今日 0:00 起" / "Today's timeline starts at midnight"
     - 或者在"现在"黑竖线**右侧** 把灰底改为淡条纹（`repeating-linear-gradient 浅灰`）表示"未到"，与"已过去但 idle" 的实灰区分
     - 或者在状态页顶部加一行小字「按自然日统计；利用率分母 = 24h（00:00~24:00）」
   
   PRD AC #6 只要求"画一条深色竖线指示『现在』"，**没规定竖线右侧 UI** — 这是 spec gap。

5. **「未配置」卡片 — tooltip 文案错位**：实测 2/3/4 号「未配置监测」灰色 dashed Tag 显示 OK。`PrinterCard.tsx:70-74` 的 Tooltip 文案是 **「点右上角『设置』补填 IP / 序列号 / 访问码」**。**但状态页右上角是 WS 三态指示器，不是设置入口** —— 这是真实文案错位。用户照 tooltip 提示去找右上角设置 → 找不到 → 困惑。**建议下一 iter 改为「去『系统设置 → 打印机管理』补填 IP / 序列号 / 访问码」并加 link 直跳 `/settings`**。修改 1 行。

6. **离线徽章无 actionable 提示**：1 号红底「离线」徽章渲染，但**没有任何"为什么 / 怎么办"指引**。PRD Edge case 已 ack "不区分网络不通 / 凭证错"，但完全没出 escape hatch — 作坊主看到「离线」要自己推断「可能是凭证错 / 可能是机断电 / 可能是 WiFi 离了 / 可能是后端 daemon 挂了」，然后自己回 Settings 编辑 OR 物理走过去看 LCD。**这对单人作坊主是个真实的产品问题** — 监控仪表盘没告诉他下一步该做什么。
   
   **建议下一 iter**：离线徽章 hover → tooltip「最后一次成功连接：xx 分钟前。可能原因：(a) 打印机断电/离网，(b) IP 变了，(c) 访问码错。[去 Settings 检查凭证 →]」link。代码改动 ~20 行（PrinterCard 加 tooltip）。

7. **时间轴无 legend** — 实测 `LEGEND_PRESENT=0`：状态页没有"绿=打印中 / 黄=暂停 / 灰=空闲 / 红条纹=离线"的颜色图例。**首次用户看不懂颜色**。tooltip 要 hover 才出来，且只对单段有效。**PRD AC 没明示要 legend，但 UX 标准这条该有**。
   
   **建议下一 iter**：状态页顶部 / 右下角加一行小字图例 `🟢 打印中  🟡 暂停  ⚫ 空闲  🔴 离线`（用与时间轴一致的色块）。代码改动 ~10 行。

8. **WS 三态指示器**：实测「实时连接中」绿点正常显示，杀 backend 后 `重连中…` 黄色文案预期工作（QA 场景 D 已验过）；「实时连接断开」红文案我没等到 90s+ 触发（与 QA 一致 WAIVED），代码路径在 `usePrinterStatusWS.ts:67` 已实现。AC #7 PASS（功能层）。

9. **mount 时先 snapshot 再 WS**：实测 Network requests 看到 `/api/printers/status/snapshot` 在 mount 时被请求 3 次（useEffect 调一次 + WS onopen 调一次 + dev StrictMode 双 mount artifact 一次）。**功能层 OK**（QA Retry 1 已验 PRD AC #7「先 snapshot 后 WS」契约）。但 3 次请求对单用户、4 台机、SQLite 是无压力的，**未来若 100+ 台机或多用户**这条 N+1 + 3x 重复会成问题。TL P2 carry-over 已记。

10. **空态「暂无打印机」+ 链接到 `/settings`**：代码层 `PrinterStatus.tsx:108` 写了 `<a href="/settings">` — **会触发整页 reload**（不是 react-router 的 navigate），用户跳过去会丢前端态，回退时状态页要重连 WS。**建议下一 iter 改用 react-router `<Link>`**。微差，本轮 QA 没触发（4 台机都有）。

11. **snapshot 失败「请求失败: 502」**：实测杀 backend 后状态页 Empty + 「重试」按钮渲染 OK，但**错误描述是 "请求失败: 502"** — 这是 raw HTTP error 透出来的，**作坊主不知道 502 是什么意思**。PRD CUJ-2 Step 1 Details 写「snapshot 失败 → 整页空态显示『连接失败』+「重试」按钮」 — **PRD 设计是固定文案「连接失败」**，实现把 raw error message 直接渲染。
   
   **建议下一 iter**：把 `description` 改成「无法连接后端，请检查服务是否启动」固定文案；技术错误码（502）放 console.log 给排查用。代码改动 1 行。

12. **跨午夜 UI 重置**：算法纯函数测覆盖 + UI 层 `setInterval(setNow, 60_000)` 实现了"每分钟刷新 now" — PRD Edge case "服务器跨午夜时 利用率归零重算" 在功能层 OK，但**用户在跨午夜瞬间看到的是「时间轴突然左端清空 + 现在竖线回到最左」**，无任何过渡 / 提示 / 动画。本轮无法直接观察（要等真午夜），QA 同样 WAIVED。**建议下一 iter 加一句"已切到 06-24 / 利用率重置"toast 或在 0:00~0:05 时段在卡片上挂个小角标"今日刚开始"**。

13. **2/3/4 号未配置卡的渲染**：实测时间轴整条 `#f0f0f0` 灰底 + dashed border + 居中文字「未配置」— 清晰直观，比"红条纹离线"更好懂。AC #4 PASS。

14. **「未配置」与「离线」的视觉对比**：未配置 = 灰底 + dashed + 「未配置」文字；离线 = 红条纹 + 红徽章。**视觉区分明显**，PRD 关键约束 #7「凭证「未配置」与「凭证错（离线）」是两种不同徽章」达成。

15. **真硬件 AC 8/11/edge 「WS 重连屡次失败 90s+」**：沿用 QA WAIVED 处理，作为产品评审 caveat 而**不是** Satisfied — 因为没有真打印机 / 没有等满 90s 触发降级文案，**所以无法在产品层确认"红色降级文案落在右上角的位置 / 文案 / 时间戳显示是否对作坊主可读"**。这三条进下一 iter 的真硬件接受测试。

**Caveats / gaps**:
- 24h 时间轴在 0~6 点早晨视觉上"大段灰底空白"，用户心智模型（滚动 24h）与 PRD 模型（自然日 24h）错位，**无任何视觉信号区分"未到时间"vs"已过去但 idle"** — 体感上易困惑
- 时间轴没有颜色 legend — 首次用户看不懂颜色
- 「未配置监测」tooltip 文案"点右上角『设置』" — 文案错位（状态页右上角是 WS 指示器不是设置）
- 离线徽章无 actionable 指引 — 单人作坊主看到"离线"不知下一步做什么
- snapshot 失败时错误描述 "请求失败: 502" — raw HTTP error 透出来，不是 user-friendly 固定文案
- 跨午夜 UI 无过渡 / 提示
- 空态「暂无打印机」link 用 `<a href>` 触发整页 reload — 应该用 react-router `<Link>`
- AC 8 / AC 11 / Edge case「WS 重连屡次失败」3 项**WAIVED — 等真打印机做接受测试**（不能直接判 Satisfied）

**Spec gap**:
- PRD CUJ-2 Step 1 Details 没规定 snapshot 失败时具体错误文案 → 实现透 raw error；建议下一 iter 修 PRD 明示固定文案 "连接失败" 或类似
- PRD AC #6 只要求"画一条深色竖线指示『现在』"，没规定竖线右侧 UI；用户心智错位是 spec 没考虑到的部分
- PRD 没要求时间轴 legend；UX 标准建议补
- 「未配置监测」tooltip 文案在 PRD 里没明示要给 link；建议补
- 离线徽章是否要 actionable hint，PRD ack 了"不区分网络不通/凭证错"，但没明示是否要给 retry / 检查凭证 link

**关于明确不做的项（不算 caveat）**：

PRD 23-28 行明确 MVP 不做：告警 / 推送通知（打印中断 / 长时间空闲）/ 7-30 天滚动趋势 / 远程控制 / 硬件遥测（温度 / 层数 / 进度 / 摄像头）/ 任务排班关联。这些是 user 在 PRD 设计阶段明确的取舍 — **不算 caveat**，但 product 角度要 ack:

- **没有报警** — 用户晚上睡觉打印机离线了，第二天才看到状态页才发现。这是 MVP scope 的明确取舍（与 PRD-003 收菜闹钟没耦合）；**未来若把 WS 推送复用到浏览器 Notification API 或邮件，能从"被动查看"升级到"主动提醒"**。下一 iter+1 可议。

**关于利用率分母（24h vs 操作窗口）**：

PRD 23 行明示"利用率分母固定 24h（1440 分钟）"，用户原话「想监测每一台每天的使用率（工作时间/24h）」 — MVP 用 24h 是对的（用户认知一致）。**但作坊主实际只在操作窗口内可工作**（PRD-004 配的每日时间窗口，比如 08:00~23:00 = 15h 而非 24h），所以 24h 分母会让"实际跑满"的机看起来只有 60%~70% 利用率（15/24 = 62.5%）。

**这不是 caveat — 是产品取向问题**。MVP 阶段维持 24h 是对的（用户原话最优先），**但建议下一 iter+1 在卡片上加一个 toggle「24h 分母 / 操作窗口分母」让用户切换**。两种口径对应两种问题：
- 24h 分母 = "这台机相对设备折旧 / 电费占用而言被用了多少"
- 操作窗口分母 = "这台机在我能干预的时间段被用了多少 / 排班够不够吃满"

第二种口径与 PRD-003 排班算法的"产能预算"更直接挂钩。

---

### Recommended Next-Iteration Priorities

ordered by user-value × cost：

1. **CUJ-1 凭证编辑弹窗 v2 抛光**（高价值 / 低成本，1-2 小时）
   - 注入 antd `ConfigProvider locale={zh_CN}` → "Cancel" / "OK" 自动变中文「取消」/「确定」
   - 访问码字段 placeholder 改为 `从打印机 LCD → 设置 → 网络 → 访问码 抄取（8 位数字）`
   - IP / Serial 字段 placeholder 后追加 hint（IP：`在 LCD 网络页可见` / Serial：`在 LCD 关于页或机身贴纸`）
   - 「清除访问码」改为 antd `Popconfirm` 二次确认，文案「确定清除？需重新到打印机 LCD 抄写才能恢复监测」
   
   这四项是 CUJ-1 体感最直接的 4 个高价值低成本改动。修完 CUJ-1 可考虑升 Satisfied。

2. **CUJ-2 状态页 v2 体感抛光**（高价值 / 中成本，半天）
   - 时间轴 legend：状态页右上角 / 卡片右下角 加 inline 图例「🟢 打印中  🟡 暂停  ⚫ 空闲  🔴 离线」
   - 时间轴"现在"竖线右侧改为淡条纹（`repeating-linear-gradient(45deg, #f9f9f9, #f9f9f9 4px, #fff 4px, #fff 8px)`）表示"未到"，区分"已过去但 idle"灰底
   - 离线徽章 hover tooltip 加 actionable 提示：「最后成功连接 X 分钟前。可能原因：断电/凭证错/网络。去 [系统设置](/settings) 检查 →」
   - 「未配置监测」tooltip 文案改「去『系统设置 → 打印机管理』补填」+ link 直跳
   - snapshot 失败错误描述改固定文案「连接失败，请检查后端服务」
   - 空态 link 改为 react-router `<Link to="/settings">`
   
   修完这些 + 真硬件接受测试通过，CUJ-2 可升 Satisfied。

3. **真打印机硬件接受测试**（高价值 / 真机依赖）
   - AC 8: 真打印机 MQTT push → 后端 → WS 推送 → 前端徽章变「打印中」全程时延 < 1 秒
   - AC 11: 改 access_code 为错 → ≤30s 切离线；改回正确 → ≤30s 切回真实状态
   - Edge case「WS 重连屡次失败」：等 90s+ 累计退避，观察右上角是否显示「实时连接断开，X 秒前 snapshot」+ 卡片是否仍展示 snapshot 内容
   - 跨午夜实地观察利用率重置（建议 23:55 进页面 + 等到 00:05 观察）
   
   **建议作为用户首次拿到真硬件时的「上手测试 checklist」一次走完**。这三条不是工程层能消化的、必须用户参与。

4. **CUJ-2 + PRD-003 收菜闹钟耦合**（中价值 / 中成本，1 天）
   - 当某机从 `running` → `idle` 且本批是排班最后一批 → 复用现有收菜闹钟逻辑发桌面 Notification「1 号机收菜了」
   - 把"被动看仪表盘"升级到"主动推送"
   - 这是真正解决 "用户晚上睡觉离线了第二天才发现" 痛点的方向
   
   依赖 PRD-003 CUJ-5（收菜闹钟）已 merged，但需要把 PrintTask 状态机与 PrinterStatusSample 对齐 — 可能要小开 PRD-008 设计。

5. **TL Phase 3.6 carry-over 收尾**（不阻塞 / 加固）
   - P2: snapshot 端点 N+1 查询（4 台机不痛，但与 prd-006 同类 carry-over 一起做）
   - P2: lifespan race window（startup 后第一条 MQTT 前 ≤3s 窗口）— 真硬件接受测试后看实际影响再定优先级
   - LOW: 守护进程无 watchdog 自动重启（PRD #1 已 ack）

6. **利用率分母 toggle**（低价值 / 低成本，迭代 +1）
   - 在卡片上加一个 segmented control「24h / 操作窗口」让用户切换
   - PRD-004 操作窗口数据直接可用
   - 默认 24h（保持 MVP 用户认知），切换后利用率分子分母都按操作窗口算
   - 这条对"晚上看排班够不够吃满"价值大但不紧急

### PRD Lifecycle Changes（iter5）

- **prd-007-printer-status: 保持 `active`** — 2 CUJ 全 Caveats，无 Satisfied 项；建议下一 iter 用 1 个半 sprint 收齐上述优先级 1 + 2 + 3 后再升 `completed`。frontmatter 状态本次**不变**。

---

## Iter3 — prd-005（已 completed，保留历史记录）

Iteration: 3（prd-005 产品录入 — 全 5 CUJ 实现）
Scope: prd-005-intake 5 个 CUJ（CUJ-1 上传分类 / CUJ-2 LLM 识别 / CUJ-3 草稿校对 / CUJ-4 颜色矩阵 / CUJ-5 合并到 catalog）。prd-000~004 本轮未触及，不在产品评审范围。

### Overall Assessment

iter3 把整条产品录入链路从「设计纸面」推到了可日常使用的状态 — 9 张床头柜真实样本走通 upload → recognize → draft → color → merge → success 端到端无人工返工，catalog.yaml 实际追加 + DB 自动 reload + `/products` 立刻可见新产品。设计阶段定下的所有关键命名约定（`<产品基名>-<组件名>`、`<组件名>-<件数>`、`<产品基名> - 配色 N`）、视觉强对比（蓝/橙双色分栏、关键字段蓝色高亮、暗黑 YAML 预览、5 阶段事务带备份回滚）全部落地。两次 QA 回归暴露的 3 个 MEDIUM+ bug（state-loss-on-back、step-indicator-error-case、cancel-fake-error）已修复并验证关闭；剩余 LOW（AntD deprecation × 4、MergeStats schema drift、9 个 manual NOT_RUN 覆盖空白）转交下一 iter，不阻塞产品验收。**5 个 CUJ 全部 Satisfied，无 Caveats、无 Not done — prd-005 状态 active → completed。**

**Per-verdict 计数**：Satisfied = 5（CUJ-1/2/3/4/5）；Caveats = 0；Not done = 0。

### Per-CUJ Verdict（iter3 prd-005）

详细 per-CUJ 评审已在 prd-005 完成 iter3 时归档；iter4 review 不再展开。结论摘要：CUJ-1 启发式分类 / CUJ-2 3 步进度 + 错误页 / CUJ-3 蓝色高亮 + 撞名 surface + 原图复核 drawer / CUJ-4 N 平等变体 + 复制此列 + popover 3 段 / CUJ-5 暗黑 YAML 预览 + 5 阶段事务 + 备份回滚 — 全部 Satisfied。

### PRD Lifecycle Changes（iter3）

- **prd-005-intake: `active → completed`**

---

## Iter4 — prd-006

### Overall Assessment

iter4 把"晚间 10 分钟手抄 50 单"压到了"扫一次 + 校对几行 + 一键入待处理"的工程骨架 — 4 个 CUJ 的核心管线（XHS Chrome 扩展 → 后端 + LLM 匹配 / Xianyu ADB 截屏 → 异步 LLM 解析 → 二次匹配 / 预览表格 → commit 单事务 → 成功页）全部通电；后端契约（commit 原子性、`-redoN` override、partial unique index、stateless preview）live 验证扎实，344 backend tests + tsc clean；retry 1 把 5 个 MEDIUM+ bug 全部闭环（下载按钮、空 batch 空态、`adb_connected` 真值、前端联动）。但走完整 product walk 后我有 **3 个 CUJ 标 Caveats** — 不是阻塞产品价值的 bug，而是若干"PRD 写了但实现取了简化方向 / 取了别的方向"的差异，作坊主能完成主流程但会感觉到"和文档约定不太一样"的小毛刺。**CUJ-1/2/3 落地 Caveats，CUJ-4 仅 1 项 AC（`.env` 未配 key 时变红）未实现 → Caveats。** 由于 4 CUJ 全为 Caveats（无 Satisfied、无 Not done），PRD 状态**保持 active**，不升级 completed；建议下一 iter 用一个小回合收齐这些 spec-drift 项 + LOW carry-over 再升 completed。

**Per-verdict 计数**：Satisfied = 0；Caveats = 4（CUJ-1/2/3/4）；Not done = 0。

价值校验：作坊主拿到的"晚间 10 分钟同步 50 单 → 全自动入待处理队列"产品承诺，在以下条件下成立 ——
1. Chrome 扩展已装、已配 INFILL_EXT_ID、千帆已登录、catalog ≈ 50 SKU；
2. PC 上模拟器跑闲鱼、ADB endpoint 已 test-adb 绿；
3. DASHSCOPE_API_KEY 已配；
4. 没有撞到下面 Caveats 中标红的差异点。

满足这 4 条时主流程跑通；不满足任一条时 fallback 文案 / 错误指引大多 actionable（错误中文 + 三项诊断 + 自动跳设置页路径），只在 CUJ-4 有 1 处用户会"找不到为什么扫描时 LLM 报错"（key 未配未在设置页变红 → 用户跑到 CUJ-1 才看到失败）。整体可接受发布，但仍是 v1 起步形态，不是终形。

### Per-CUJ Verdict

#### CUJ-1（prd-006）: 扫描小红书千帆订单 — Caveats

**QA verdict** (from qa-report.md): PASS（retry 1）
**PM verdict**: Caveats

**Assessment**:

按用户指定的"晚间 10 分钟同步 50 单"价值校验逐项 walk —

1. **入口 / 路由 / 双 tab 切换**：`/orders/import` 渲染面包屑「订单管理 / 自动导入」、双 tab（小红书红 #ff2442 / 闲鱼橙 #ff7a00），切 tab 不丢前端态 — AC #1-3 PASS（QA 双 run 验证）。

2. **三态状态指示器**：未装态正确显示蓝点 + 4 步安装引导 + retry 1 新加的「下载扩展 zip」primary 按钮（href `/static/extensions/infill-xhs-scraper-v0.1.0.zip`，size large，`download` 属性、application/zip MIME — 服务端 curl 200 verified） — 用户路径"打开页 → 看到未装 → 点下载 → 装扩展 → 重新检测"全程可走。**这条 Iter4 retry 1 修得很扎实。** 已装态我没法 live 验证（无 ext 环境），代码层 ProbeState 三态 switch case 完整。

3. **5 步扫描进度的实现方向**：实现把进度步骤拆成了 `['已找到千帆 Tab', '注入 content script 完成', '正在抓取订单列表 DOM', '后端去重 + LLM 匹配 SKU', '跳转预览页']`，与 PRD AC #7 明确的 `① 连接扩展 ② 定位千帆 tab ③ 抓取 DOM ④ 解析订单 ⑤ LLM 匹配 SKU` 不完全一致。**这是 spec 与实现之间的工程取向差异** — 实现把"连接扩展"与"定位千帆 tab"合并到第 1 步（已 probe 就 OK），把"解析订单 / LLM 匹配 SKU"合并到第 4 步（后端一站式处理），加了第 5 步"跳转预览页"作为前端态切换的回显。从用户体感"这一步系统在做什么"看，工程版反而更连贯（用户看不到 content script 注入但能看到"成功跳页面"的确认）；但对"通过 spec 阅读理解后端做了什么"的 docs 价值，差异不可忽略。建议下个 iter 决定：要么改 PRD 把 5 步描述对齐实现取向，要么把实现回归 PRD 5 步描述。

4. **进度卡片细节**：标题硬编码"正在捕捉千帆订单"，闲鱼路径如复用同组件会看到这串错文案 — QA 没标，但 grep 显示 ScanningProgress 当前只在 XhsTab 用，无回归风险，但是个有副作用的复用边界（被滥用即坏）。

5. **进度副文案 + 子计数**：PRD AC #7 提示"当前进行步骤的副文案可显示子计数（如「正在匹配第 18/42 条」）" — **实现未做**。当 LLM 匹配 50 条需要 30+ 秒时（既有 LOW carry-over 也指出串行 LLM 调用），用户看不到推进度，感觉像卡死。这是真实的 UX 痛点 — 推荐 iter5 加 SSE 或 polling 把当前匹配条数推回前端。

6. **probe 仍占位**：AC #4 要求"调 `POST /api/auto-import/xhs/probe` 探查千帆 tab"，后端实际永远返回 `has_xhs_tab=true` — QA 标 LOW（"探活由扩展前端做，后端可视为意图保留 hook"）。我认同这是 LOW 不阻塞，但需要 doc 一下：CUJ-1 实际探活是 `chrome.runtime.sendMessage(ping)` + 扩展回报，后端只是版本兼容性 placeholder；用户 navigate 进页面时如果千帆 tab 没开但扩展 ping 成功，**会显示「就绪」并允许点扫描，扫到 0 条**（PRD 也确实允许此路径——会跳 CUJ-3 空态，链路安全）—— 但 PRD AC #5 描述的"● 未发现千帆 tab"黄色态在真实环境永远不会触发。建议要么把扩展 `scrape_xhs` 加一个 `find_tab_only` action 让扩展先 probe 再回报（与 PRD 一致），要么改 PRD 删除"未发现千帆 tab"黄色态。

7. **失败路径覆盖**：扩展抓取格式异常 → 后端丢弃缺三件套订单 + 扫描汇总 toast — 后端 test (`test_scan_drops_missing_required_fields`) PASS；前端 toast 文案我没确认，可能与 spec 还有微差异（QA NOT_RUN）。LLM 匹配 90 秒超时 + 三按钮「重试 / 跳过 / 返回」**未实现** — `XhsTab` 只在 `error` 态显示"扫描失败 + retry" 单按钮，没有"跳过 SKU 匹配，进 CUJ-3"的逃逸路径。AC #14/15 未达成。这条对 LLM 服务降级时的用户体验影响较大：作坊主依赖 DashScope，DashScope 出问题时用户得能"跳过匹配直接进预览手指 SKU"才不会被卡住。**建议优先级 P1，下一 iter 加。**

**Caveats / gaps**:
- 5 步进度文案与 PRD AC #7 不完全对齐（实现合并了步骤，去掉了子计数）— spec 与 impl 取向差异
- LLM 匹配中无子计数（30~60 秒等待期间无推进度感）
- xhs/probe 仍占位 → AC #5「● 未发现千帆 tab」黄色态在真实环境不会触发
- AC #14/15 LLM 超时三按钮分支（重试 / 跳过 SKU 匹配 / 返回）未实现 — 当前只有「重试」单按钮
- ScanningProgress 标题硬编码"正在捕捉千帆订单"，复用边界不干净

**Spec gap**:
- 是否要在 LLM 匹配步骤显示子计数（PRD 写了"可"，未强制） — 用户实际跑过几次后再决定是否补
- 5 步描述对齐方向（改 spec 还是改 impl）需要用户决策一次

---

#### CUJ-2（prd-006）: 扫描闲鱼订单 — Caveats

**QA verdict** (from qa-report.md): PASS（retry 1）
**PM verdict**: Caveats

**Assessment**:

按用户"作坊主用 MuMu 跑闲鱼 → 手动滚 + 逐次截屏 → 完成解析 → 进预览"的核心路径 walk —

1. **HIGH bug retry 1 闭环**：`adb_connected` 现在按 `device_state.ok` 判（不再 `bool(list_devices())`），前端 XianyuTab 加 `allDiagsOk` 防御层 — 我看了源码 `XianyuTab.tsx:61-66`，逻辑是 `resp.ok && resp.adb_connected && allDiagsOk` 三重 AND，对 false-green 完全屏蔽。**这是 iter4 最重要的修复 — 之前的 false-green 等价于让作坊主"以为 ADB 通了点截屏却失败"，会让产品在新机器上首次配置时彻底失去信任。** Retry 1 后 pc_ip="" / 配置 bogus IP 时统一显示「ADB 未连接」红块 + 三项诊断 + 「重新测试 ADB」+ 「打开设置页修改 endpoint →」link。Backend `TestQAFixAdbConnectedTruth` 4 测覆盖。Trust restored。

2. **手动滚 + 逐次截屏的核心心智**：PRD 设计阶段明确"MVP 不自动滚动" — 是产品的核心判断（自动 swipe 在模拟器 / 不同分辨率间不稳，让作坊主自己控制更可靠）。实现严格遵守：`grep` 验证后端无 `adb shell input swipe`；前端只有「截屏 (+1)」+「完成截屏，开始解析」+「取消」三按钮 — 用户路径清晰。AC #6 PASS。这条选择实际跑下来作坊主会感谢的 — 闲鱼改版后自动滚动多半会失效，手动反而是抗改版的护城河。

3. **缩略图条 + mini 订单卡片列表**：`ScreencapGrid` 渲染缩略图状态徽章（🔄/●/!/✗）已具备代码路径 — QA 因无 ADB 设备未 walk 实际渲染（NOT_RUN 8 项），但 grep 看到 `截屏 #${s.seq}` + 状态机字段都有。我对这条 code-review 后判断完整。

4. **截屏 + 解析重叠（异步）**：后端 `TestE2E_xianyu_screencap_async` PASS — 截屏命令完成立即返回，LLM 解析独立异步跑、不阻塞下一次截屏点击。这是 PRD "ADB 命令进行中通常 < 1 秒，命令返回后立即重新 enabled" 的精确实现，对用户"快速连续点截屏"的体感至关重要。

5. **AC 缺口**：
   - AC #16 LLM 失败率 > 30% 警告 warning 未在前端实现（grep 0 hit「失败率较低」「建议补几张」）。当 DashScope 间歇性挂 / 模拟器画面被通知遮挡时这是用户判断要不要补截屏的决策依据。**实际影响**：批次质量不可见 → 用户可能进 CUJ-3 才发现 N 张全失败 → 得退回重扫，浪费 20 秒。建议 P1。
   - AC #16 整体超时 5 分钟 abort + 「带这些进预览（红色低置信度）」/「丢弃重试」两按钮 **未实现** — 同 CUJ-1 LLM 超时降级路径缺失。建议合并到同一 iter5 修复点。
   - AC #18 跨 tab 互锁（小红书扫描进行中时本 tab 按钮 disabled + tooltip）— `XianyuTab.tsx:451` 用 `canScreencap`，无 `otherInProgress` 输入 prop，**未传入跨 tab 状态**；XhsTab 有 `otherInProgress` prop 但 `XianyuTab` 没等价机制。QA NOT_RUN。**实际影响**：用户可能在小红书还在 LLM 匹配时跑去截屏闲鱼，触发 DashScope 限流（PRD 设计阶段明确避免这条）。建议 P1。

6. **「完成截屏，开始解析」按钮文案**：实现 `完成截屏，开始解析 (${captureCount} 张)` — 这条文案设计在 captureCount=0 时是 `完成截屏，开始解析`（无后缀，由 disabled 防御），≥1 时附数量。**比 PRD 文案更显式地告诉用户"我要处理几张"** — 这是好的微调，留着即可。

7. **「取消」灰色提示 5 秒消失**：PRD AC 描述底部 5 秒灰色提示「已取消上次扫描」— 实现 `XianyuTab.tsx:hooks` 用 `message.info`（AntD 顶部 toast，3 秒默认），**位置 / 持续时间与 PRD 不一致**。微差，但 PRD 是底部 inline 提示更不打扰，顶部 toast 会盖住主区。建议 iter5 改为底部 inline。

8. **闲鱼 mini 订单卡片**：spec 描述"已解析订单 mini 卡片列表（每条订单一行 mini 摘要：买家昵称 + 商品标题 + 数量），随解析进度增长" — code 看到 `ScreencapGrid` 渲染缩略图但没找到 mini 订单卡片列表渲染。**这是 PRD 明显的视觉信号缺失 — 用户截屏时本想"看着列表长出来"确认 LLM 抓到东西，没有的话只能看缩略图徽章变绿，反馈层次少了一层。** 建议 iter5 加。

**Caveats / gaps**:
- AC #16 失败率 > 30% warning 未实现
- AC #16 LLM 整体超时 5 分钟 abort + 两按钮分支未实现（与 CUJ-1 同类缺口）
- AC #18 跨 tab 互锁未实现 — XianyuTab 没接收 `otherInProgress` prop
- 「已解析订单 mini 卡片列表」未实现 — 截屏阶段反馈层次缺失
- 「已取消上次扫描」用 toast 不是底部 inline — 与 PRD 微差

**Spec gap**:
- 无（PRD CUJ-2 spec 完整、问题在实现侧）

---

#### CUJ-3（prd-006）: 预览校对 + 一键导入 — Caveats

**QA verdict** (from qa-report.md): PASS（retry 1）
**PM verdict**: Caveats

**Assessment**:

这是整个 prd-006 链路最重 / 最复杂的 CUJ — 后端契约 9 项 AC（commit 原子性、`-redoN` override、partial unique index、ML 匹配率、stateless）live 验证全部 PASS（QA AC #13/19/25 三项 live 验 + 5 项 edge case live 验）；前端预览表格 22 项 AC 在 code 层全部 PASS（QA 标 code，因无 LLM key + 无扩展环境不能 e2e）。**后端 commit 单事务 + auto-rollback 是这条链路的安全锚 — `test_commit_zero_inserts_when_one_sku_missing_mid_batch` 验证了 5 单中第 4 单 SKU 缺失时整批回滚 0 写入。这是 CUJ-3 最该保的契约，保住了。**

按用户"作坊主对着 N 行预览校 SKU、点导入、看成功页确认 N 单进队列"的核心路径 walk —

1. **筛选 chips 与 PRD spec 取向差异**：PRD AC #3 描述 4 chips「✓ 高置信度（绿）/ ? 中置信度（黄）/ ! 低置信度（红）/ ↻ 重复订单（灰）」按置信度分类。实现是 3 chips「● 新单（绿）/ ○ 重复（灰）/ ! 未匹配（红）」加左侧「全部」按置信度合并到状态类别。**实现的取向**：把"中 / 高置信度"合并到「新单」（用户实际只关心"勾还是不勾"），「低置信度」改名「未匹配」（更直白 — 用户更容易理解"为什么不勾选我"= 因为没匹配），重复独立。这是个**合理的简化**，对 50 单 / 天的体量更顺手（4 chips 在用户视觉里反而太碎），但**与 PRD AC 不严格一致**。建议下个 iter 决定：改 PRD 对齐 impl，或恢复 4 chips。我倾向前者 — impl 取向更对，文档应该跟着改。

2. **行底色 + 默认勾选规则**：PRD 4 档（白 / 浅黄 / 浅红 / 灰）在 code 层有 `rowClassName` 完整实现（grep `cls-conf-high/mid/low + cls-row-dup`），但置信度阈值映射到底色的边界用户能不能"一眼看出哪行需要校对"我没法 live 验证（QA 也 NOT_RUN）— 静态走读判断 OK。

3. **重复单 + 改判 override**：PRD 设计阶段花了很多功夫在"重复单灰底 + 默认不勾 + 改判二次确认"的安全护栏上，实现非常扎实——
   - Modal 二次确认文案：`确认改判为新单？系统检测到该单已存在；改判后将作为新订单写入，可能产生重复。` — 比 PRD 文案稍简化但语义到位。
   - 后端 `-redoN` 后缀方案 live 验证：连续 override 两次产生 base / `-redo1`，partial unique index 保证 `external_order_id` IS NULL 时多行 NULL 允许（手动录单不互斥）。**这条 schema 选择对的，比"直接绕过唯一约束"安全得多 — 任何时刻 DB 看 `external_order_id` 列都能追溯到 platform 上的原单。**
   - 「改判为新单」link 与「重复」tag 视觉对齐 PRD（QA code review PASS）。
   - 边角缺陷：实现简化了"于 06-17 已导入 · order #87"的元信息为单行「已导入 · order #N」（缺日期），微差。

4. **SkuPicker 三段结构**：浮窗 360px 宽、当前匹配 + 原文 + LLM 候选 + 搜索 + "找不到 SKU？请先到 [产品录入](/intake)" link — code review PASS，PR-D AC #10/11 满足。这条是 LLM 不可靠场景的核心校对工具，结构对、跳产品录入的 escape hatch 对 — 让用户在 catalog 没有该 SKU 时不会卡死。

5. **空 batch 空态**：retry 1 修复后 `rows.length === 0` 居中渲染「未抓取到任何订单」+ 「请检查千帆 tab 是否打开，或闲鱼是否截取到订单页」副文案 + 「返回扫描页」按钮 — QA Run 1/2 双重验证 + onCancel hook 调用上层 dispatch 切回 tabs 模式。**这是 retry 1 的真正闭环 — 不只是补了 UI，连"返回扫描页"按钮的行为路径都接通了。**

6. **成功页缺 PRD 明确字段**：PRD CUJ-3 step 13 与 AC 描述成功页"灰底批次详情条"含「来源平台 / 扫描时间 / 扫描方式（Chrome 扩展 / ADB 截屏 N 张）/ 总耗时 / 批次号 batch_id / 平均置信度」6 字段；实现 `SuccessPanel.tsx:125-139` 渲染 6 字段为「来源平台 / 耗时 / 新增订单 / 跳过重复 / 手动跳过 / SKU 匹配率」 — 与 PRD spec 不完全对齐（缺 "扫描方式 / 扫描时间 / 批次号 batch_id / 平均置信度"，加了"新增 / 跳过重复 / 手动跳过 / SKU 匹配率"的细化）。**impl 实际取向其实更好 — stat 网格已经在上面渲染了 4 个数（新增 / 跳过重复 / 手动跳过 / SKU 匹配率），下方批次详情就该展示"环境元信息"（平台 / 扫描方式 / 总耗时 / batch_id / 平均置信度）而不是把 stat 数字重复一遍。** 建议下 iter 把批次详情改回 PRD 设计的环境元信息组合。

7. **「继续导入<另一平台>」用 `window.location.reload()` 而非 tab 切换**：PRD step 13 描述"按钮变「继续导入闲鱼」/「继续导入小红书」"且"自动切到另一平台 tab，回到该 tab 初始就绪态" — 实现是 `window.location.reload()`，**这是错的方向**。`window.location.reload()` 会丢失 React Router state、AntD message context，并且**回到的是默认 tab（小红书）而不是另一平台 tab**。用户从闲鱼成功页点「继续导入小红书」实际回到的是小红书 tab（OK），但从小红书成功页点「继续导入闲鱼」会先 reload 到小红书 tab 再要求用户手动切换 — 体感是"我点了继续导入闲鱼，怎么还是小红书"。建议 iter5 改为父组件 dispatch `{kind:'tabs', activeTab: otherPlatform}`。

8. **失败页**：`FailurePanel.tsx` 渲染红 ! + 标题「导入失败 — 未写入任何订单」+「返回预览继续校对」/「丢弃本批」(二次确认) — 与 PRD AC PASS。我看了 FailurePanel grep 输出，二次确认 Modal 文案是「丢弃本批？」— OK 但比 PRD 描述简化。

9. **commit 路径 / 单事务保证**：live + automated 双重验证 PASS。**这是 prd-006 整个链路最该保的契约 — 失败"全 or 无"语义不会污染待处理队列。**

**Caveats / gaps**:
- 筛选 chips 实现取向（3 chips by status）与 PRD AC #3（4 chips by confidence）不一致 — spec drift，倾向改 spec 而非 impl
- 重复单元信息文案缺日期（「已导入 · order #N」vs PRD「于 06-17 已导入 · order #N」）
- 成功页批次详情字段与 PRD spec 不一致 — 缺扫描方式 / 扫描时间 / batch_id / 平均置信度
- 「继续导入<另一平台>」用 `window.location.reload()` 而非父组件 tab 切换 — 用户从小红书跳闲鱼路径错位
- 顶部 chip 「平均置信度 / 最低」实现得很好但 AC 没明示，可考虑加入 PRD spec

**Spec gap**:
- 4 chips（PRD）vs 3 chips（impl）的取向选择应当走 user 决策

---

#### CUJ-4（prd-006）: 自动导入设置 — Caveats

**QA verdict** (from qa-report.md): PASS（retry 1 — 共享 CUJ-2 HIGH 修复链路）
**PM verdict**: Caveats

**Assessment**:

按"作坊主首次配 ADB + 检查扩展状态"路径 walk —

1. **核心配置链路 PASS**：GET/PUT `xianyu/config` roundtrip 3 个新 backend 测全绿；设备类型下拉端口自动填正确（MuMu→7555 / 蓝叠→5555 / 雷电→5555 / USB→5037 三个 live 验证）；「测试 ADB 连接」按钮点击后端 diagnostics 完整返回 + 修复后 `adb_connected` 不再 false-green；保存按钮 dirty 状态联动。**这是 CUJ-4 的骨架 — 骨架对。**

2. **小红书卡片**：未装态 / 已装态分支正确渲染，video 4 步引导 + 「下载扩展 zip」按钮（与 CUJ-1 一致）。

3. **闲鱼卡片**：表单字段（设备类型 / PC IP / 端口）布局对齐 cuj-4-initial.html mock，「测试 ADB 连接」+「保存配置」按钮分立。

4. **AC #14 缺口**：`.env` 未配 `DASHSCOPE_API_KEY` 时页底说明条**未变红** — 实现是 `AutoImportSettings.tsx:367-379` 一段固定灰底文案，**没有服务端检测 LLM key 状态的 endpoint，前端无法响应**。**实际影响**：作坊主首次配完 ADB → 跑去 CUJ-1/2 扫描 → LLM 匹配阶段才发现"DashScope key 未配 / 错"，得 ssh 后端改 `.env` 重启 — 这是产品的核心防呆缺失（PRD 设计阶段明确写过"避免 fail late"）。后端添加 `GET /api/auto-import/llm/status` → `{ ok, configured: bool }` 不复杂；前端在说明条前后切色即可。**建议 P1。**

5. **AC #15 缺口**：从 CUJ-1/2 故障态点「打开设置页」跳本页时**未自动滚 + 卡片 pulse** — QA 标 NOT_RUN，code 层无 pulse 动画 / scrollIntoView 逻辑。**实际影响**：作坊主在 ADB 错块点「打开设置页」link 跳到 `/settings/auto-import`，页面正常加载但他要自己往下找闲鱼卡片（这页只有两张并列卡片，找成本不高 — 影响最小）。**建议 P2（next iter+1）。**

6. **AC #11 PC IP 空时「测试 ADB」disabled + tooltip**：QA 标 NOT_RUN，code 检查显示无 disabled 防护。**实际影响**：用户空 IP 点测试 → 后端按 pc_ip="" 跑诊断 → 三项 ✗ 返回（修复后），用户能看出"IP 没填" — UX 仍可用但啰嗦了一步。**建议 P2。**

7. **测试连接不持久化 + 必须点保存才落库**：code review PASS（test-adb 不写 settings 表），契约对的。

**Caveats / gaps**:
- AC #14 `.env` 未配 LLM key 时说明条不变红 — 服务端检测 endpoint + 前端联动均缺
- AC #15 从 CUJ-1/2 故障态自动滚 + 卡片 pulse 未实现
- AC #11 PC IP 空时「测试 ADB」未 disabled

**Spec gap**:
- 无（PRD AC 写得很清楚，仅实现侧缺 3 项）

---

### Recommended Next-Iteration Priorities

ordered by user-value × cost：

1. **CUJ-4 AC #14 LLM key 未配检测 + 说明条变红**（高价值 / 低成本）— 增加 `GET /api/auto-import/llm/status` 服务端检测 `DASHSCOPE_API_KEY` 是否存在且非空 → 前端在 AutoImportSettings 页底 + 可选地在 CUJ-1/2 进入扫描前同步检查 → 未配时整页阻塞「请先配置 .env DASHSCOPE_API_KEY 并重启后端」。这是**作坊主首次配置时最容易掉坑的地方** — fail early 比 fail at scan 友好得多。估算 2-3 小时含测试。

2. **CUJ-1 + CUJ-2 LLM 超时降级路径**（高价值 / 中成本）— 加 90 秒（xhs）/ 5 分钟（xianyu）的端到端超时检测，弹错误卡片"重试 / 跳过 SKU 匹配（进 CUJ-3 全红低置信度）/ 返回" 三按钮。这是作坊主依赖第三方 LLM 服务时的核心 escape hatch — DashScope 偶发挂掉时用户得能仍录单。建议两 CUJ 一起改，复用同一 ErrorPanel 组件。估算 1 天。

3. **CUJ-2 跨 tab 互锁 + 失败率 > 30% warning + mini 订单卡片**（中价值 / 中成本）— 三项一起做 —
   - XianyuTab 接收 `otherInProgress` prop，对应 disabled 「截屏」+「完成解析」按钮（avoid DashScope 限流）
   - 「完成截屏，开始解析」点击时计算 LLM 解析成功率，< 70% 时弹 confirm dialog
   - 截屏过程中右侧渲染 mini 订单卡片列表（已解析订单的买家 + 标题 + 数量）
   估算 1 天。

4. **CUJ-3 成功页批次详情字段对齐 PRD + 「继续导入」用 tab 切换**（低价值 / 低成本）— SuccessPanel 拆「stat 网格」（保留）和「批次详情」（改为 来源平台 / 扫描方式 / 扫描时间 / 总耗时 / batch_id / 平均置信度）；「继续导入<另一平台>」改成 dispatch `{kind:'tabs', activeTab: otherPlatform}`，避免 `window.location.reload()`。估算 2 小时。

5. **筛选 chips 取向决策**（PRD 调整 / 不动 impl）— user 看 3 chips vs 4 chips 哪个更顺手，定后改一边。我倾向改 PRD 对齐 impl（3 chips 取向更直观），但请 user 确认。

6. **QA 提到的 LOW carry-over**（合并到 next-prd 一起做）—
   - xhs/probe 占位实现 → 要么真做（扩展 `find_tab_only` action），要么从 PRD 删除「未发现千帆 tab」黄色态
   - AntD Spin tip deprecation → 全局替换 `tip` → `description` 属性（与 prd-005 iter3 同类问题一起做）
   - TL review carry-over 5 项（N+1 / 串行 LLM / payload limit / CORS / 硬编码 backend URL）— 是次 iter 引入的技术债，建议作为 prd-006 收尾的"加固 iter"统一处理

7. **CUJ-1 5 步进度文案对齐方向 + LLM 匹配子计数**（中价值 / 低成本）— 与 user 商量是改 PRD 5 步描述对齐 impl 还是回归 PRD；同时 backend SSE / polling 推 LLM 匹配进度（"正在匹配第 18/42 条"），让 30~60 秒等待期间有推进度感。估算 半天。

8. **CUJ-4 AC #15 / #11 + 缩略图 manual 验证补全**（低价值 / 中成本）— 设置页 pulse 动画 + PC IP 空时 disabled + Playwright 补 CUJ-2 缩略图状态徽章自动化测试。CUJ-4 这两项 UX 抛光在用户实际使用后再决定是否做。

### PRD Lifecycle Changes（iter4）

- **prd-006-auto-import-orders: 保持 `active`** — 4 CUJ 全 Caveats，无 Satisfied 项；建议下一 iter 用 1 个加固回合收齐上述 P1 + P2 项后再升 `completed`。frontmatter 状态本次**不变**。
