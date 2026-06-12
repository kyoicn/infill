---
id: prd-004
title: 系统配置
status: active
created: 2026-06-13
deprecation_reason:
---

# PRD-004：系统配置

> 本 PRD 由现有实现反向补写（backfill），描述「当前产品里实际发生的行为」，不是重新设计。
> 与 [docs/specs.md](../specs.md) §1（核心需求）、§3.8~§3.10（Printer / ScheduleConfig / SystemConfig 数据模型）、「排班约束」章节、§8.6（系统设置页面）的用户意图冲突时，**以代码现状为准并在文中标注**（见文末「与 specs.md 用户意图的差异备注」）。
> ScheduleConfig / SystemConfig 在排班算法中的消费方式见 [docs/design/design-scheduler.md](../design/design-scheduler.md)（`_get_day_windows` / `_get_windows` / `_get_changeover_minutes`）与 [docs/design/system.md](../design/system.md) §实体关系。

## 功能概述

系统配置是 specs.md §8.6（系统设置页）的落地。作坊主在这一页维护**排班算法的输入参数**与**打印机台账**：

- **打印机管理**：增（支持一次批量录入多台）、删；查看当前打印机列表。
- **操作时间窗口配置**：按星期几（周一~周日）配置「用户可操作打印机的时间段」，支持每天多段。窗口是排班算法的硬约束（任务只能在窗口内启动）。
- **换版时间配置**：单个数值，收菜 + 换版 + 启动下一轮所需的预留分钟数（`changeover_minutes`）。
- **数据库重置**：删除排班等非核心数据、重建表结构、从 YAML 重载目录，并尽量恢复库存 / 订单 / 打印机 / 配置。

这些配置是**排班生成的上游输入**：操作窗口与换版时间直接决定排班算法的可行解（PRD-003）。配置页本身不触发排班，只持久化参数。

数据模型（详见 specs.md §3.8~§3.10、models.py）：

- `Printer(id, name)` —— 名称为自由字符串（如「1号机」），**无唯一约束、无格式校验**，可同名。所有打印机性能一致（specs.md §3.8）。
- `ScheduleConfig(id, day_of_week, windows)` —— `day_of_week` 为 `0=周一 … 6=周日`，**DB 层有 unique 约束**（每天至多一行）。`windows` 为 JSON：`[{"start":"HH:MM","end":"HH:MM"}, ...]`。
- `SystemConfig(id, key, value)` —— 通用 key-value 配置表，`key` 唯一，`value` 一律存为**字符串**。本页只读写 `changeover_minutes` 这一个 key。

实现文件：
- 后端：`backend/app/routers/printers.py`（打印机 CRUD）、`backend/app/routers/config.py`（操作窗口 upsert / 系统配置 upsert / 重置数据库）。schema 见 `backend/app/schemas.py`（`PrinterCreate/Out`、`TimeWindow`、`ScheduleConfigCreate/Out`、`SystemConfigOut/Update`）。
- 前端：`frontend/src/pages/Settings.tsx`（系统设置页：打印机卡片 + 换版时间卡片 + 操作时间窗口卡片 + 数据库维护卡片 + 两个弹窗）。
- API 客户端：`frontend/src/api/client.ts`（`getPrinters / createPrinter / updatePrinter / deletePrinter / getScheduleConfigs / upsertScheduleConfig / getSystemConfigs / upsertSystemConfig / resetDatabase`）。
- 配置的消费方：`backend/app/services/scheduler.py`（`_get_day_windows` / `_get_windows` / `_get_changeover_minutes`）。

API 契约：

| 方法 + 路径 | 请求 → 响应 | 说明 |
|---|---|---|
| `GET /api/printers` | → `list[PrinterOut]` | 返回全部打印机（`{id, name}`），**无排序保证、无分页** |
| `POST /api/printers` | `PrinterCreate{name}` → `PrinterOut` | 新建一台，无去重 / 无名称校验 |
| `PUT /api/printers/{id}` | `PrinterCreate{name}` → `PrinterOut` | 改名；无记录返回 404。**前端当前未调用此接口**（Settings.tsx 无改名入口） |
| `DELETE /api/printers/{id}` | → `{ok: true}` | 删除；无记录返回 404。**不检查是否被已存在排班引用** |
| `GET /api/config/schedule` | → `list[ScheduleConfigOut]` | 按 `day_of_week` 升序返回**已配置**的天（未配置的天不返回行） |
| `PUT /api/config/schedule/{day_of_week}` | `ScheduleConfigCreate{day_of_week, windows}` → `ScheduleConfigOut` | upsert 某天窗口；`day_of_week ∉ [0,6]` 返回 400「day_of_week 必须在 0~6 之间」；**不校验 windows 的格式 / 顺序 / 重叠 / 起止大小** |
| `GET /api/config/system` | → `list[SystemConfigOut]` | 返回全部 key-value 配置行 |
| `PUT /api/config/system/{key}` | `SystemConfigUpdate{key, value}` → `SystemConfigOut` | upsert 某 key（path 的 `key` 为定位键，body 的 `key` 当前未被用于定位）；`value` 存为字符串 |
| `POST /api/config/reset-db` | → `{ok, restored:{inventory, orders, printers}}` | 备份核心数据 → drop+create 所有表 → 重载 YAML 目录 → 恢复库存 / 订单 / 打印机 / 配置。**不 seed 默认 ScheduleConfig / SystemConfig** |

关键口径说明（重要，影响多个 CUJ）：

- **操作窗口默认值在两处硬编码、且与 DB 不同源**：未配置某天时，排班算法 `scheduler.py:53` 回退到硬编码 `[(480,720),(750,1080),(1110,1380)]`（即 08:00-12:00 / 12:30-18:00 / 18:30-23:00）；前端编辑弹窗（`Settings.tsx`）首次打开未配置的天时，**另有一份相同字面量**作为预填默认。二者是各自独立的字面量，存在漂移风险（design-scheduler.md §Open Questions #3、system.md §已知问题 #4）。详见文末差异 #1。
- **`changeover_minutes` 无初始化、内联默认 15 多处**：SystemConfig 启动时不写默认行，`_get_changeover_minutes` 与 `schedule.start_batch` 各自 `int(cfg.value) if cfg else 15`（system.md §已知问题 #5、design-scheduler.md §Open Questions #4）。详见文末差异 #2。
- **重置数据库不 seed 默认配置**：`reset-db` 只「备份→恢复」用户已有的 ScheduleConfig / SystemConfig 行；若重置前从未配过窗口 / 换版，重置后这两张表仍为空，排班继续走上述硬编码 fallback / 内联默认。详见文末差异 #3。
- **富余生产开关不在本页**：specs.md §8.6 / §3.10 把「富余生产开关 `surplus_enabled`」列在系统设置里。**当前实现里它不是持久化的系统配置，而是排班生成时的请求参数**（`GeneratePlanRequest.surplus_enabled`，默认 `true`，属 PRD-003 排班页），Settings.tsx 没有这个开关。详见文末差异 #4。

本 PRD 范围：
- CUJ-1：管理打印机（查看列表 / 批量新增 / 删除）
- CUJ-2：配置操作时间窗口（按星期几多时段）
- CUJ-3：配置换版时间
- CUJ-4：重置数据库

不在本 PRD 范围：富余生产开关（实为排班请求参数，属 PRD-003）、打印机改名（后端接口存在但前端无入口）、排班生成本身（PRD-003）、目录从 YAML 加载（PRD-000）。

---

## CUJ-1：管理打印机

**Dependencies**: 无（独立台账；删除某打印机会影响后续排班的可用机器数，但本 CUJ 不依赖排班已存在）
**Priority**: P0（打印机数量是排班算法的核心输入；specs.md §3.8「当前 4 台，后续可增减」）

### Context

作坊主新购 / 报废打印机时，需要在系统里增减台账，否则排班算法用到的「可用打印机数」与现实不符。specs.md §8.6 把「打印机管理（增减打印机）」列为系统设置首项。当前实现支持**一次批量录入多台**（购入多台时一次填完），删除为单台带二次确认。所有打印机性能一致，无型号 / 状态字段（specs.md §3.8）。

### Preconditions

- 用户已打开「系统设置」页。
- 后端 `Printer` 表可读写（初始可能为空，也可能在重置或初始化后已有若干台）。

### Journey Steps

1. **用户操作**：进入「系统设置」页。
   - **系统响应**：页面加载时调用 `GET /api/printers`、`GET /api/config/schedule`、`GET /api/config/system`，把打印机渲染进「打印机管理」卡片表格。
   - **用户看到**：页面顶部标题「系统设置」。第一张卡片标题「打印机管理」，右上角有蓝色主按钮「新增打印机」（带 + 图标）。卡片内是一张小号无分页表格，列为：`ID`（宽 60）、`名称`、`操作`（宽 80）。每行「操作」列是一个红色危险删除按钮（垃圾桶图标）。表格按后端返回顺序展示（无显式排序）。
   - **Details**：表格 `size="small"`、`pagination={false}`，行数即全部打印机。无打印机时表格显示 Ant Design 默认空态（「暂无数据」）。

2. **用户操作**：点击「新增打印机」。
   - **系统响应**：打开标题为「新增打印机」的弹窗；表单状态重置为单个空输入行（`printerNames = ['']`）。
   - **用户看到**：弹窗内一行文本输入框，placeholder 为「如：1号机」（按行号递增，第 i 行 placeholder 为「如：{i}号机」）。下方有一条虚线全宽按钮「再加一台」（带 + 图标）。弹窗底部为 Ant Design 默认「确定 / 取消」。
   - **Details**：当只有一行时，该行不显示行内删除按钮；有多行时每行末尾出现红色删除按钮（垃圾桶图标）。

3. **用户操作**：（批量场景）点击「再加一台」一次或多次，并在每行填入名称（如「5号机」「6号机」）。
   - **系统响应**：每次点击向 `printerNames` 追加一个空串，新增一行输入框。
   - **用户看到**：输入行依次增加；从第 2 行起每行右侧出现删除按钮，可移除该行。
   - **Details**：行的增删只改前端临时状态，未提交后端。

4. **用户操作**：点击弹窗「确定」。
   - **系统响应**：前端把各行 `trim()` 后过滤空串得到 `names`；若 `names` 为空，弹出警告 `message.warning('请至少输入一个名称')` 并**不关闭弹窗**；否则对 `names` 中每个名称**串行**调用一次 `POST /api/printers`（`createPrinter({name})`），全部成功后关闭弹窗、重置为单空行、弹出 `message.success('已添加 N 台打印机')`、重新拉取列表。
   - **用户看到**：成功后弹窗关闭，绿色成功提示「已添加 N 台打印机」，打印机表格刷新出新行。
   - **Details**：创建是逐条 `await` 串行；后端对名称无去重、无格式校验，**可创建同名打印机**。

5. **用户操作**：点击某行「操作」列的红色删除按钮。
   - **系统响应**：弹出 Ant Design `Popconfirm` 气泡，标题「确定删除？」。确认后调用 `DELETE /api/printers/{id}`（`deletePrinter(id)`），成功后重新拉取列表。
   - **用户看到**：气泡确认框（含「确定 / 取消」）。确认后该行从表格消失。
   - **Details**：删除无额外成功 toast（仅靠列表刷新反映结果）；后端**不校验该打印机是否已被某排班任务引用**即直接删除。

### Edge Cases & Error States

- **空名称 / 全空白**：批量弹窗里全部行为空或纯空格 → 前端 `trim+filter` 后为空 → `message.warning('请至少输入一个名称')`，弹窗保持打开，不发请求。
- **同名打印机**：后端无去重，填入与已有同名的名称会成功创建出第二台同名机；表格出现两行同名（ID 不同）。**已知：无唯一约束。**
- **批量创建中途失败**：`names` 逐条串行 `await`；若第 k 条 `POST` 抛错（如网络中断），`savePrinters` 在该次 `await` 处抛出、**前 k-1 条已落库且不会回滚**，弹窗不关闭、不重置、不显示成功 toast；用户再次点「确定」会**重复创建已成功的前若干条**（前端不去重）。**已知：批量创建非原子、无防重。**
- **删除不存在的打印机**：`DELETE` 命中已被删的 id → 后端返回 404「打印机不存在」；前端 `deletePrinter` 不做错误兜底，列表刷新后该行本就不在，用户基本无感。
- **删除被排班引用的打印机**：后端直接删除，不阻止；已存在的排班任务对应的 `printer_id` 将悬空（取决于排班渲染如何处理缺失打印机）。**已知：无引用完整性检查。**
- **打印机改名**：UI 无改名入口（虽 `PUT /api/printers/{id}` 与 `api.updatePrinter` 都存在），改名只能删后重建。**已知：能力存在但未接入 UI。**

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters).

### Acceptance Criteria

- 系统设置页首张卡片「打印机管理」展示一张含 `ID / 名称 / 操作` 三列的表格，行数等于 `GET /api/printers` 返回的打印机数。
- 点「新增打印机」打开弹窗；弹窗内可通过「再加一台」增加输入行，从第 2 行起每行可删除。
- 弹窗内全部输入为空时点「确定」，出现警告「请至少输入一个名称」且弹窗不关闭、列表不变。
- 在弹窗内填入 N（≥1）个非空名称点「确定」，成功后弹窗关闭、出现「已添加 N 台打印机」绿色提示、表格新增 N 行。
- 名称两侧空白被去除后入库（前后空格不保留）。
- 点某行删除按钮出现「确定删除？」气泡，确认后该行从表格消失。
- 输入两个相同名称能成功创建出两行同名打印机（验证无去重的现状行为）。

---

## CUJ-2：配置操作时间窗口（按星期几多时段）

**Dependencies**: 无（独立配置；其产物被 PRD-003 排班生成消费）
**Priority**: P0（操作窗口是排班算法的硬约束，specs.md「排班约束」：任务只能在操作窗口内启动）

### Context

作坊主只在固定时段操作打印机（窗口外睡觉 / 吃饭，specs.md「排班约束」）。这些时段**按星期几不同**（工作日与周末作息不同）。配置后，排班算法只在窗口内安排任务启动（可跨窗口运行）。当前实现把七天固定列成一张表，每天可独立编辑成多段时间窗口。**未配置的天在排班时回退到硬编码默认窗口**（见功能概述与文末差异 #1）。

### Preconditions

- 用户已打开「系统设置」页。
- `GET /api/config/schedule` 已返回**已配置**天的窗口（未配置的天不在返回里，前端按缺省渲染）。

### Journey Steps

1. **用户操作**：在系统设置页查看「操作时间窗口」卡片。
   - **系统响应**：前端用 `Array.from({length:7})` 固定渲染周一~周日七行，对每行用 `day_of_week` 去 `scheduleConfigs` 里找已配置的窗口。
   - **用户看到**：卡片标题「操作时间窗口」。表格列为：`星期`（宽 80，渲染为「周一…周日」）、`时间窗口`、`操作`（宽 80）。已配置的天在「时间窗口」列以「`HH:MM-HH:MM，HH:MM-HH:MM…`」形式逗号分隔展示各段；未配置的天显示灰色文字「未配置（使用默认）」。每行「操作」列有一个小号「编辑」按钮。
   - **Details**：七行恒定显示（即使后端一行都没有）；时间段拼接用中文逗号「，」。

2. **用户操作**：点击某天的「编辑」按钮（如「周一」）。
   - **系统响应**：记录 `editingDay`，从 `scheduleConfigs` 取该天已有窗口填入弹窗；**若该天未配置，则预填一份硬编码默认窗口** `[{08:00-12:00},{12:30-18:00},{18:30-23:00}]`。打开标题为「编辑时间窗口 — 周X」的弹窗（宽 500）。
   - **用户看到**：弹窗内每个时间段一行，每行两个 `TimePicker`（格式 `HH:mm`）中间夹一个「至」字，行末一个红色删除按钮（垃圾桶图标）。底部一条虚线全宽按钮「添加时间段」（带 + 图标）。
   - **Details**：`TimePicker` 用 dayjs 解析 `HH:mm`；新增的段默认 `{start:'08:00', end:'12:00'}`。

3. **用户操作**：调整某段的开始 / 结束时间，或点「添加时间段」增段，或点行末删除按钮减段。
   - **系统响应**：每次修改更新前端 `windows` 临时数组；`TimePicker` 选定时把对应字段写成 `HH:mm` 字符串。
   - **用户看到**：时间选择器值实时更新；段数随增删变化。
   - **Details**：纯前端临时状态，未提交。可把窗口删到 0 段（弹窗内无最少 1 段限制）。

4. **用户操作**：点击弹窗「确定」。
   - **系统响应**：调用 `PUT /api/config/schedule/{editingDay}`（`upsertScheduleConfig(editingDay, {day_of_week, windows})`）upsert 该天；成功后关闭弹窗、重新拉取配置、`message.success('已保存')`。
   - **用户看到**：弹窗关闭，绿色提示「已保存」，该天在表格「时间窗口」列更新为新窗口（或保存空段后变回「未配置（使用默认）」文案的反面——即显示空，详见 Edge Cases）。
   - **Details**：后端 upsert 按 `day_of_week` 命中则覆盖 `windows`，否则新建行。

### Edge Cases & Error States

- **保存空窗口（0 段）**：弹窗里把所有段删光后「确定」→ 后端为该天写入 `windows=[]`（一条存在但空数组的行）。该天**已配置但为空**：表格「时间窗口」列因 `rec.windows.length===0` 而仍显示灰色「未配置（使用默认）」文案，但其实 DB 里已有空行。排班算法 `_get_day_windows` 命中该（非 None）配置后返回**空窗口列表** → 这一天**没有任何可启动时段**（与「未配置走默认」语义相反）。**已知：空窗口与未配置在 UI 上同样显示「未配置（使用默认）」，但算法行为完全不同。**
- **未配置的天**：`GET /schedule` 不返回该天 → 表格显示「未配置（使用默认）」→ 排班算法回退硬编码默认窗口（见差异 #1）。
- **窗口顺序 / 重叠 / 起止反向**：后端 `upsert_schedule_config` **不校验** windows 内容（不查 start<end、不查段间重叠、不查跨午夜）。可保存 `22:00-06:00`、重叠段等；算法 `_get_day_windows` 会把字符串按 `HH:MM` 解析为分钟并 `sorted()`，但**不修正反向区间**（`end<start` 会得到「负长度」区间，行为未定义）。**已知：无窗口合法性校验。**
- **`day_of_week` 越界**：路径传入 <0 或 >6 → 后端 400「day_of_week 必须在 0~6 之间」。正常 UI 不会触发（只渲染 0~6）。
- **保存请求失败**：`saveWindows` 无 try/catch，请求抛错会冒泡（无错误 toast），弹窗保持打开状态。**已知：窗口保存无错误兜底提示。**

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters).

### Acceptance Criteria

- 「操作时间窗口」卡片恒定展示周一~周日七行，每行有「编辑」按钮。
- 已配置的天在「时间窗口」列以「`HH:MM-HH:MM`」（多段用中文逗号分隔）展示；未在 `GET /schedule` 返回的天显示灰色「未配置（使用默认）」。
- 点某天「编辑」打开标题含该天名称的弹窗；未配置的天预填三段默认窗口（08:00-12:00 / 12:30-18:00 / 18:30-23:00）。
- 弹窗内可通过「添加时间段」增段、行末删除按钮减段、`TimePicker` 改起止时间。
- 点「确定」后弹窗关闭、出现「已保存」、该天表格行更新为所配置窗口。
- 保存后再次进入该天编辑，预填的是上次保存的窗口（而非默认窗口），即配置已持久化到 DB（`GET /schedule` 含该天）。

---

## CUJ-3：配置换版时间

**Dependencies**: 无（独立配置；其值被 PRD-003 排班生成与批次执行消费）
**Priority**: P0（换版时间是排班算法的核心间隔参数，specs.md「排班约束」：每次任务结束后预留 15 分钟收菜换版）

### Context

每批打印结束后，作坊主要收菜、换打印版、启动下一轮，这段操作需要预留时间（specs.md 默认 15 分钟，可配置）。排班算法用它做「任务结束 + 换料」后的间隔（`idle_after` / `printer_available = end + changeover`）。当前实现是系统设置页上一个单数值输入 + 保存按钮，持久化到 `SystemConfig` 的 `changeover_minutes` key（存为字符串）。

### Preconditions

- 用户已打开「系统设置」页。
- 页面加载时 `GET /api/config/system` 已返回配置；前端从中找 `changeover_minutes`，若存在用其 `value` 初始化输入，否则用默认字符串 `'15'`。

### Journey Steps

1. **用户操作**：查看「换版时间（分钟）」卡片。
   - **系统响应**：前端把 `changeover_minutes` 的 `value`（字符串）转 `Number` 填入数字输入框；找不到该 key 时显示默认 15。
   - **用户看到**：卡片标题「换版时间（分钟）」。卡片内一行：一个数字输入框（`InputNumber`，最小值 0）+ 蓝色主按钮「保存」。输入框当前值为已保存的换版分钟数。
   - **Details**：`InputNumber min={0}`；`onChange` 时若值为空回退到字符串 `'15'`。

2. **用户操作**：修改输入框数值（如改成 20），点击「保存」。
   - **系统响应**：调用 `PUT /api/config/system/changeover_minutes`（`upsertSystemConfig('changeover_minutes', {key:'changeover_minutes', value})`）；`value` 以字符串形式 upsert 到 SystemConfig；成功后 `message.success('已保存')`。
   - **用户看到**：绿色提示「已保存」。输入框保留新值。
   - **Details**：保存不重新拉取配置（输入框已是用户输入的值）；后端按 key upsert，命中覆盖 `value`、否则新建行。

### Edge Cases & Error States

- **首次（无 `changeover_minutes` 行）**：`GET /system` 不含该 key → 输入框显示默认 15，但**此时 DB 里并无该行**；只有用户点过「保存」后才真正落库。在此之前，排班算法走 `_get_changeover_minutes` 的内联默认 15（见差异 #2）。
- **值为 0**：`min={0}` 允许 0，可保存「0 分钟换版」；排班算法将不预留间隔。无业务下限校验。
- **清空输入**：`InputNumber` 清空时 `onChange` 收到 `null`，前端回退为字符串 `'15'`（即清空后保存等于存 15）。
- **保存请求失败**：`saveChangeover` 无 try/catch，请求抛错会冒泡，无错误 toast。**已知：换版保存无错误兜底提示。**
- **非整数 / 极大值**：`InputNumber` 默认允许小数与任意大值（无 `precision` / `max` 限制）；`value` 原样存字符串，算法侧 `int(cfg.value)` 解析——若存了小数字符串（如 `15.5`），`int("15.5")` 会抛 `ValueError`。**已知：前端无整数约束、后端无解析兜底。**

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters).

### Acceptance Criteria

- 「换版时间（分钟）」卡片展示一个最小值为 0 的数字输入框 + 「保存」按钮。
- 页面加载时输入框值等于已保存的 `changeover_minutes`（无该配置时显示 15）。
- 修改数值后点「保存」出现「已保存」绿色提示。
- 刷新页面后输入框显示的是上次保存的值（验证已持久化到 `SystemConfig`）。
- 输入 0 可成功保存。

---

## CUJ-4：重置数据库

**Dependencies**: 无（横切维护操作；恢复阶段会重载 PRD-000 目录、尽量恢复 PRD-001/002 的订单/库存与本 PRD 的打印机/配置）
**Priority**: P1（数据维护 / 排障手段；非日常高频操作，但破坏性强需谨慎）

### Context

排班等衍生数据可能进入脏状态（如手动编辑后不一致），或目录结构调整后需要重建表。作坊主需要一个「清空衍生数据、重建表结构、从 YAML 重载目录、尽量保住核心业务数据（库存 / 订单 / 打印机 / 配置）」的一键维护操作。当前实现是系统设置页底部「数据库维护」卡片里的危险按钮，带二次确认。**注意：恢复阶段不会 seed 默认配置**（见差异 #3）。

### Preconditions

- 用户已打开「系统设置」页。
- 后端可访问 `catalog.yaml`（恢复阶段要 `load_catalog`）。

### Journey Steps

1. **用户操作**：滚动到「数据库维护」卡片。
   - **系统响应**：静态卡片渲染。
   - **用户看到**：卡片标题「数据库维护」。卡片内一段灰色说明文字：「重置数据库会删除所有排班数据并重建表结构。库存、订单、打印机和系统配置会保留，产品目录从 YAML 重新加载。」下方一个红色危险按钮「重置数据库」。
   - **Details**：说明文字与按钮纵向排列（`Space direction="vertical"`）。

2. **用户操作**：点击「重置数据库」。
   - **系统响应**：弹出 `Modal.confirm` 二次确认框。
   - **用户看到**：确认框标题「确定要重置数据库吗？」（带感叹号警告图标），正文「排班数据将被清除，库存和订单会保留。此操作不可撤销。」，按钮「确定重置」（红色危险）/「取消」。
   - **Details**：`okType="danger"`，`okText="确定重置"`。

3. **用户操作**：点击「确定重置」。
   - **系统响应**：调用 `POST /api/config/reset-db`。后端依次：(a) 备份库存（按组件名 + 颜色 + 数量）、订单（状态 + 时间 + 明细按产品名）、打印机（名称）、ScheduleConfig（day_of_week + windows）、SystemConfig（key + value）；(b) `drop_all` + `create_all` 重建全部表；(c) 新 session 里 `load_catalog` 从 YAML 重载目录，再按名称映射**尽量**恢复库存 / 订单（明细中产品名仍存在的才恢复）/ 打印机 / 配置；(d) 返回 `{ok, restored:{inventory, orders, printers}}`。前端成功后 `message.success('重置完成，已恢复 X 条库存、Y 条订单、Z 台打印机')` 并重新拉取页面数据。
   - **用户看到**：成功后绿色提示「重置完成，已恢复 X 条库存、Y 条订单、Z 台打印机」，打印机表格 / 窗口表格 / 换版输入刷新为恢复后的值。
   - **Details**：恢复是「按名称匹配」的尽力恢复，非按主键还原；ID 会重排。

### Edge Cases & Error States

- **未 seed 默认配置**：若重置前从未配过操作窗口 / 换版时间，备份为空 → 恢复后 ScheduleConfig / SystemConfig 仍为空表 → 排班继续走硬编码 fallback / 内联默认。**已知：reset-db 不写入任何默认配置行**（见差异 #3）。
- **目录变更导致数据丢失**：恢复库存只对「YAML 重载后仍存在的组件名」生效；恢复订单只保留「明细里产品名仍存在」的项，整单明细全失效则**跳过整张订单**。即重置 + 目录改名/删项会**静默丢弃**对不上的数据。`restored` 计数里 `orders` 统计的是备份的订单数（恢复循环对无有效明细的单 `continue`，实际落库可能少于该计数）。**已知：恢复为尽力而为，存在静默丢数与计数口径差异。**
- **库存颜色匹配**：库存恢复按 `(组件名, color)` 定位恢复后的 `Inventory` 行，`color` 缺省取 `""`。目录重载后若该 `(组件,颜色)` 行不存在则该条库存丢失。
- **重置过程中断 / YAML 解析失败**：`reset-db` 在 drop+create 之后才 `load_catalog`；若 YAML 此时损坏，目录恢复失败而表已被重建 → 数据库进入「空目录 + 部分恢复」状态。前端 `try/catch` 捕获异常并 `message.error(e.message || '重置失败')`。**已知：操作非事务化，中途失败不回滚到重置前状态。**
- **不可撤销**：确认框已明示「此操作不可撤销」；无导出 / 备份下载 / 撤销入口。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters).

### Acceptance Criteria

- 「数据库维护」卡片展示说明文字与红色「重置数据库」按钮。
- 点「重置数据库」弹出标题为「确定要重置数据库吗？」的二次确认框，含「确定重置 / 取消」。
- 点「确定重置」后调用 `POST /api/config/reset-db`，成功后出现含「已恢复 X 条库存、Y 条订单、Z 台打印机」的绿色提示。
- 重置后页面打印机 / 窗口 / 换版数据自动刷新。
- 重置前已配置的操作窗口与换版时间，在重置后仍保留（验证配置被备份恢复）。
- 重置前从未配过操作窗口的天，重置后仍显示「未配置（使用默认）」（验证不 seed 默认行的现状）。

---

## 与 specs.md 用户意图的差异备注（backfill 标注）

下列为「specs.md / 设计意图」与「代码现状」的差异，本 PRD 以代码现状为准并在此集中标注，供后续迭代取舍：

1. **操作窗口默认值两处硬编码、与 DB 不同源**：未配置某天时，排班算法 `scheduler.py:53` 回退硬编码 `[(480,720),(750,1080),(1110,1380)]`，前端编辑弹窗（`Settings.tsx`）又各自硬编码一份相同字面量作为预填默认。二者独立维护，存在漂移风险；应在初始化迁移里写入默认 `ScheduleConfig` 行以统一来源（system.md §已知问题 #4、design-scheduler.md §Open Questions #3）。

2. **`changeover_minutes` 无初始化、内联默认 15 多处**：SystemConfig 启动不写默认行，`_get_changeover_minutes` 与 `schedule.start_batch` 各自 `int(cfg.value) if cfg else 15`，默认数字 15 分散多处、无集中常量（system.md §已知问题 #5、design-scheduler.md §Open Questions #4）。

3. **重置数据库不 seed 默认配置**：`reset-db` 只备份恢复用户已有的 ScheduleConfig / SystemConfig；从未配过则重置后两表仍空，排班继续走硬编码 fallback / 内联默认。与「重置后应有一套可用默认配置」的合理预期不一致。

4. **富余生产开关不在系统设置页**：specs.md §8.6 / §3.10 把 `surplus_enabled` 列为系统设置项；当前实现里它是**排班生成请求参数**（`GeneratePlanRequest.surplus_enabled`，默认 true，属 PRD-003 排班页），系统设置页没有该开关、SystemConfig 也不持久化它。

5. **空窗口与「未配置」在 UI 上无法区分**：保存了 `windows=[]` 的天（已配置但空）与从未配置的天，在操作窗口表里都显示「未配置（使用默认）」，但算法行为相反（空窗口 = 当天无可启动时段；未配置 = 走默认窗口）。前端文案有歧义。

6. **打印机改名无 UI 入口**：`PUT /api/printers/{id}` 与 `api.updatePrinter` 都存在，但 Settings.tsx 未提供改名交互；改名只能删后重建。

7. **配置 / 打印机均无内容合法性校验**：打印机名可空白/同名（trim 后非空即可，无去重）；操作窗口不校验起止大小、段间重叠、跨午夜；换版分钟无整数/上限约束。单用户低频场景下风险可控，但缺乏防呆。
