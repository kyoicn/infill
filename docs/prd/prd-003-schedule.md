---
id: prd-003
title: 打印机排班
status: active
created: 2026-06-13
deprecation_reason:
---

# PRD-003：打印机排班

> 本 PRD 由现有实现反向补写（backfill），描述「当前产品里实际发生的行为」，不是重新设计。
> 与 [docs/specs.md](../specs.md)（§1 核心需求·打印机排班、排班约束、富余生产策略、订单优先级、每日工作流、§7 排班手动调整、§8.5 排班中心）、[docs/schedule_specs.md](../schedule_specs.md) 的**用户意图**冲突时，**以代码现状为准并在文中标注**。
> **算法细节不在本 PRD 范围**——任务选择评分、富余瓶颈算法、两阶段配比、同步惩罚公式等一律链接到权威活文档 [docs/schedule_specs.md](../schedule_specs.md)。本 PRD 只关心用户旅程（用户做什么、系统响应什么、屏幕显示什么）。
> 代码结构、双实现分歧、已知风险见 [docs/design/design-scheduler.md](../design/design-scheduler.md)，其 §Open Questions 在本文末「已知问题」如实引用。

## 功能概述

排班中心（前端单页 `Schedule.tsx`，路由「排班中心」）是作坊主在**晚间盘点最后一步**生成第二天可执行排班、并在**第二天白天**按排班表逐批执行的核心工作台。一个页面承载五条用户旅程：

1. **生成排班**：选日期/开始时间/时长，选三种调度策略之一，调同步强度滑块，开关富余生产，可选指定产品过滤 → 一键生成草稿排班。
2. **查看排班**（列表视图 + 甘特图视图 + 排班总结）：理解这批排班产出什么、可凑齐多少完整产品、各打印机利用率。
3. **确认与执行（按批次状态流转）**：草稿 → 确认 → 第二天逐批「开始」（按实际时间重排后续批次）→ 逐任务「完成 / 取消 / 失败」，完成自动入库。
4. **手动编辑排班**（仅草稿）：删除单任务、删除整批；后端另有「替换任务配置」端点但**前端未接入**（见 CUJ-4 与已知问题）。
5. **设闹钟（收菜提醒）**：为某批次或自定义分钟数设倒计时，到点蜂鸣 + 浏览器通知 + 页内告警。

数据模型（详见 specs.md §3.11–3.13、design-scheduler.md）：

- `PrintPlan(id, date, start_time, duration_hours, status, created_at, batches[])`——排班表头。`status ∈ {draft, confirmed}`。
- `PrintBatch(id, start_time, batch_order, status, tasks[])`——一组**同时启动**的任务。`status ∈ {pending, started, completed}`。`batch_order` 0-based。
- `PrintTask(id, printer_id, print_config_id, color, is_surplus, start_time, end_time, status)`——单台打印机的单个打印盘任务。`status ∈ {pending, completed, cancelled, failed}`（前端把 pending 显示为「进行中」，见 CUJ-3 边界）。
- 时间在 DB 中存为 `"HH:MM"` 字符串，**允许超过 24:00**（如 `33:40` 表示次日凌晨）；前端 `fmtTime` 把 ≥24 的小时折算为「MM-DD HH:MM」展示跨天。

实现文件：
- 后端：`backend/app/routers/schedule.py`（HTTP 入口 + 执行状态机）、`backend/app/services/scheduler.py`（DB 服务层 + 主调度循环）、`backend/app/services/scheduler_core.py`（纯函数算法核心）。
- 前端：`frontend/src/pages/Schedule.tsx`（生成表单 / 排班列表 / 详情：总结 + 甘特图 + 列表 / 执行按钮 / 闹钟，全部在一页内）。
- API 客户端：`frontend/src/api/client.ts`（`getPlans / getPlan / generatePlan / confirmPlan / deletePlan / deleteTask / replaceTaskConfig / deleteBatch / startBatch / completeTask / cancelTask / failTask`）。

API 契约（`routers/schedule.py`，与 design-scheduler.md §API 一致）：

| 方法 + 路径 | 请求 → 响应 | 说明 |
|---|---|---|
| `GET /api/schedule/plans` | → `list[PrintPlanOut]` | 全部排班，按 `date` **降序** |
| `GET /api/schedule/plans/{id}` | → `PrintPlanOut`（含 batches/tasks） | 取单个排班；不存在 404 |
| `POST /api/schedule/generate` | `GeneratePlanRequest` → `PrintPlanOut` | 生成排班；**先检查与已有排班时间是否重叠**，重叠抛 400；无打印机抛 400 |
| `POST /api/schedule/plans/{id}/confirm` | → `{ok:true}` | `draft → confirmed` |
| `DELETE /api/schedule/plans/{id}` | → `{ok, deleted_dates}` | **级联删除所有日期 ≥ 它的排班**（保供给链一致），返回被删日期列表 |
| `DELETE /api/schedule/tasks/{id}` | → `{ok:true}` | 删单任务（草稿编辑） |
| `PUT /api/schedule/tasks/{id}/config/{new_config_id}` | → `{ok:true}` | 替换任务配置并重算 `end_time`。**前端未接入** |
| `DELETE /api/schedule/batches/{id}` | → `{ok:true}` | 删整批 |
| `POST /api/schedule/batches/{id}/start` | `{actual_time:"HH:MM"}` → `{ok, delta_minutes}` | 标记批次 started，按实际时间**重排后续 pending 批次** |
| `POST /api/schedule/tasks/{id}/complete` | → `{ok, added_component_id, added_quantity}` | 完成 → **对应组件+颜色库存 +quantity**；批内全结束则批次 completed |
| `POST /api/schedule/tasks/{id}/cancel` | → `{ok, status}` | 取消（不入库） |
| `POST /api/schedule/tasks/{id}/fail` | → `{ok, status}` | 失败（不入库） |

`GeneratePlanRequest`（`schemas.py`）：`date` / `surplus_enabled=True` / `start_time="00:00"` / `duration_hours=24` / `strategy="product_first"` / `target_product_ids: list[int]|None=None` / `sync_strength=50`。

本 PRD 范围：
- CUJ-1：生成排班表（生成表单：日期/开始时间/时长 + 三策略 + 同步强度滑块 + 富余开关 + 指定产品过滤）
- CUJ-2：查看排班（排班列表 → 详情：排班总结 + 列表视图 + 甘特图视图）
- CUJ-3：确认并按批次执行（状态流转：confirm → start 批次 → complete/cancel/fail 任务 → 完成入库）
- CUJ-4：手动编辑草稿排班（删任务 / 删批次；替换配置端点存在但前端未接入）
- CUJ-5：设收菜闹钟（按批次 / 自定义分钟，倒计时 + 蜂鸣 + 通知）

不在本 PRD 范围：算法内部行为（链接 schedule_specs.md）；操作时间窗口 / 换料时间 / 打印机增减的**配置**（属系统设置 PRD，本页只读取换料时间用于「收菜时间」与闹钟换算）；目录与 BOM（PRD-000）；订单（PRD-001）；库存查看与手动调整（PRD-002，本 PRD 只触发库存**增加**）。

---

## CUJ-1：生成排班表

**Dependencies**: PRD-000（目录已加载，存在产品 / 组件 / 打印盘 / BOM）、PRD-001（待处理订单提供需求；无订单时仍可生成——见边界）；功能上还需至少 1 台打印机（无打印机后端抛 400）
**Priority**: P0（整个系统的核心产出；晚间盘点的最后一步）

### Context

作坊主每晚盘点完订单和库存后，需要一键得到第二天「每台打印机几点打什么盘」的排班表。生成表单把所有排班参数收敛在一张卡片里：时间窗（日期 + 开始时间 + 时长）、调度策略（三选一，决定优先凑齐 / 最大利用率 / 全局规划）、同步强度（决定同批打印机完成时间对齐程度，影响收菜跑动次数）、富余生产开关、指定产品过滤。参数语义的权威定义见 [schedule_specs.md §1、§5、§9、§11](../schedule_specs.md)。

### Preconditions

- 后端已启动，PRD-000 目录已加载（有产品 / 组件 / 打印盘 / BOM），至少有 1 台打印机。
- 通常已有待处理订单（PRD-001）。无订单也可生成（需求池为空；若开富余仍可能生成富余任务，见 schedule_specs.md §11.4）。
- 用户在「排班中心」页，看到顶部的「生成排班」卡片。

### Journey Steps

1. **User action**: 进入「排班中心」页。
   - **System response**: 挂载时 `reload()` 并行拉取 `getPlans / getPrinters / getComponents / getProducts / getSurplus / getSystemConfigs`（从 `getSystemConfigs` 取 `changeover_minutes` 存入本地 `changeoverMin`，默认 15），另发 `getAllConfigs` 拉全部打印盘；并请求浏览器通知权限（`Notification.requestPermission()`，default 时）。
   - **User sees**: 页面标题「排班中心」。下方依次：闹钟状态栏（无闹钟时不显示）、「快速闹钟」卡片、「生成排班」卡片、「排班表列表」卡片；选中某排班后底部再出「排班详情」卡片。
   - **Details**: 表单字段有受控默认值——日期默认**明天**（`dayjs().add(1,'day')`）、开始时间默认 `00:00`、时长默认 `24` 小时、策略默认「优先凑齐发货」（`product_first`）、富余生产默认**开**、指定产品默认空（全部产品）、同步强度默认 `50`。

2. **User action**: 设置排班时间——选日期、设开始时间、填时长。
   - **System response**: 三个受控控件分别更新 `date / startTime / durationHours`，纯前端无请求。
   - **User sees**: 「排班时间」一行：`DatePicker`（日期）+ `TimePicker`（`HH:mm` 格式）+ `InputNumber`（`addonAfter="小时"`，范围 1~168），三者等宽并排，最大宽度约 560px。
   - **Details**: 时长上限 168（一周），用于给超长任务足够周期（schedule_specs.md §4 注）。开始时间为空时回退 `00:00`，时长清空回退 `24`。

3. **User action**: 选择调度策略，并按需切换富余生产开关。
   - **System response**: `Radio.Group`（实心按钮组）更新 `strategy`；`Switch` 更新 `surplusEnabled`。纯前端。
   - **User sees**: 「调度策略」一行三个按钮：**优先凑齐发货** / **最大化利用率** / **智能规划**；按钮组下方右对齐灰色 12px 说明随选中项变化（「优先安排能凑齐完整产品的瓶颈组件」/「优先填满打印机，减少空闲时间」/「全局优化组件配比，凑齐最多完整产品」）。同行右侧是「富余生产」文字 + `Switch`，下方灰字「满足后继续备货」（开）/「仅生产订单所需」（关）。
   - **Details**: 三策略与同步强度、产品过滤完全正交可自由组合（schedule_specs.md §5.4、§9.2、§11.3）。策略语义详见 schedule_specs.md §5。

4. **User action**:（可选）指定产品过滤——在多选框选一个或多个产品。
   - **System response**: `Select mode="multiple"` 更新 `targetProductIds` 数组。纯前端。
   - **User sees**: 「指定产品」一行一个多选下拉，placeholder「全部产品（按订单顺序）」，选项为各产品名。选中后右侧出现「清除」小按钮，下方出灰字「仅排班选中产品的组件，其余产品不会被生产」。
   - **Details**: 生成时若 `targetProductIds.length > 0` 才传 `target_product_ids`，否则传 `null`（不过滤）。过滤影响需求计算 / 任务池 / 富余 / 凑齐评分四个环节（schedule_specs.md §11.2）。

5. **User action**: 拖动同步强度滑块（0~100）。
   - **System response**: `Slider` 更新 `syncStrength`。纯前端。
   - **User sees**: 「同步强度」一行：滑块（刻度标记 0 / 50 / 100）+ 右侧当前值数字。下方右对齐灰字随值变化（`0`:「不对齐，各打印机独立选最优任务」；`100`:「强制对齐，尽量所有打印机同时完成」；中间:「平衡最优任务和同批次打印机完成时间对齐」）。
   - **Details**: 语义见 schedule_specs.md §9。值越高，同批打印机越倾向选时长相近的任务 → 完成时间集中 → 用户一趟收菜。

6. **User action**: 点击「生成排班表」（蓝色 large 主按钮）。
   - **System response**: `POST /api/schedule/generate`，请求体含上述全部参数。后端**先检查时间重叠**：若新排班 `[start, start+duration)` 与任一已有排班的绝对时间区间相交 → 返回 400「与已有排班（{date} {time}，{h}h）时间重叠」。无打印机 → `generate_plan` 抛 `ValueError` → 400。否则按策略走两条路径（贪心 / 两阶段，见 design-scheduler.md），生成 `PrintPlan(status="draft")` 含若干 `PrintBatch`/`PrintTask` 并返回。
   - **User sees**: 成功——顶部绿色 toast「排班表已生成」；`reload()` 刷新「排班表列表」；新生成的排班自动设为 `selectedPlan`，底部「排班详情」卡片出现并展示总结 + 视图（见 CUJ-2）。失败——红色 toast 显示后端错误消息（如重叠 / 无打印机）。
   - **Details**: 生成的排班状态为草稿（`draft`），列表中以橙色「草稿」Tag 显示，可被手动编辑（CUJ-4）后再确认（CUJ-3）。

### Edge Cases & Error States

- **时间与已有排班重叠**：后端 400「与已有排班（… …，…h）时间重叠」，前端红色 toast 原样显示，不生成。这意味着同一时间段不能有两份排班。
- **无打印机**：后端 `generate_plan` 抛 `ValueError` → 400，前端 toast 显示该消息，不生成。
- **无待处理订单 + 不过滤 + 关富余**：需求池为空 → 生成的排班无任务（可能产生空批次并被算法删除，最终 `batches` 可能为空）。前端详情区「列表视图 / 甘特图」显示「无排班数据」。
- **指定产品但该产品无待处理订单**：需求池为空；若开富余，仍会生成该产品的富余任务（等价「直接备货某产品」，schedule_specs.md §11.4）。
- **超长任务放不进所选时长**：算法跳过会超出周期的任务（schedule_specs.md §4）。表现为该任务不出现在排班里；用户需手动延长时长（最大 168h）重新生成。
- **库存已满足所有订单**：FIFO 需求计算会跳过已满足订单（schedule_specs.md §10），需求池可能为空 → 同「无订单」情形。
- **重复点击「生成排班表」**：无防抖 / loading 禁用——第二次点击会用相同参数再发请求，但因第一份排班已占用该时间段 → 第二次必然 400 重叠。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 「生成排班」卡片含且仅含：排班时间（日期 + 开始时间 + 时长/小时，时长 1~168）、调度策略三选一（优先凑齐发货 / 最大化利用率 / 智能规划）、富余生产开关、指定产品多选、同步强度滑块（0~100，标记 0/50/100）、「生成排班表」主按钮。
- 表单初值：日期=明天、开始时间=00:00、时长=24、策略=优先凑齐发货、富余=开、指定产品=空、同步强度=50。
- 切换策略时按钮组下方灰字说明随之改变；切换富余开关、拖动同步强度滑块时对应灰字说明随之改变。
- 指定产品选中至少一个后出现「清除」按钮与「仅排班选中产品的组件…」提示；点「清除」清空选择。
- 点「生成排班表」后：成功时出现绿色 toast「排班表已生成」、新排班加入列表并自动展开详情；与已有排班时间重叠或无打印机时出现红色 toast 显示后端错误，且不新增排班。
- 生成的排班在列表中状态为「草稿」（橙色 Tag）。

---

## CUJ-2：查看排班（列表视图 + 甘特图视图 + 排班总结）

**Dependencies**: CUJ-1（需先有排班）
**Priority**: P0（生成后必须能看懂排了什么、能凑齐多少、利用率如何，才能决定确认或重排）

### Context

排班生成后，作坊主要在确认前判断「这批排班合不合理」：每个组件生产多少、排班后库存够不够订单、能凑齐几个完整产品、四台打印机忙不忙。页面通过「排班表列表」选中某排班，底部「排班详情」给出三块信息：**排班总结**（组件产量表 + 可组装产品 + 打印机利用率）、**列表视图**（按批次分组的任务表）、**甘特图视图**（横轴时间纵轴打印机的色块图）。列表 / 甘特图通过 Tabs 切换。

### Preconditions

- 已生成至少一份排班（CUJ-1）。
- 用户在「排班中心」页。

### Journey Steps

1. **User action**: 在「排班表列表」卡片中点击某一行排班。
   - **System response**: `onRow` 点击触发 `getPlan(id)` 拉取含 batches/tasks 的完整排班，设为 `selectedPlan`，底部渲染「排班详情」。
   - **User sees**: 「排班表列表」是 `size="small"`、每页 10 条的表格，列为：时间范围（如 `2026-06-13 00:00 ~ 24:00`，跨天结束显示为 `MM-DD HH:MM`）、状态（草稿=橙 / 已确认=绿 Tag）、批次数、操作（草稿行有「确认」蓝按钮，所有行有「删除」红按钮）。行可点击（cursor pointer）。
   - **Details**: 列表按 `date` 降序（后端排序）。点击行不止用于确认 / 删除，主要是把该排班载入下方详情。

2. **User action**: 查看「排班详情」顶部的「排班总结」。
   - **System response**: 前端 `renderSummary()` 纯前端计算：遍历该排班所有任务累加各 `(组件,颜色)` 产量、区分富余产量；合并 `getSurplus` 的需求与库存、以及「比当前排班更早的其他排班」的产出，估算排班后库存、仍缺量、可组装产品数；并按各任务时长累加每台打印机工作时长算利用率。
   - **User sees**: 三块——
     (a) 「排班总结」表：列 组件 / 颜色（空=`-`）/ 当前库存 / 本次生产（蓝 Tag `+需求量`、橙 Tag `+富余量`）/ 排班后库存 / 订单需求 / 仍缺（>0 红 Tag `-N`，否则绿 Tag「充足」）。
     (b) 「排班后可组装：」一行产品 Tag，每个 `产品名 xN`（N>0 蓝、=0 灰）。
     (c) 「打印机利用率（N小时）」表：列 打印机 / 工作时长（如 `18h30m`）/ 利用率（进度条 + 百分比，>80% 绿、>50% 蓝、否则黄）。
   - **Details**: 利用率分母是完整排班周期 `duration_hours×60`（非有效窗口时长）。「更早排班产出」计入当前库存估算，与排班供给链口径一致（但与库存页富余口径不同，见 PRD-002 差异说明）。

3. **User action**: 在详情区切换到「甘特图」Tab（默认初始 `viewMode='list'`，但 Tabs 中甘特图项排在前）。
   - **System response**: `renderGantt()` 渲染原生 HTML/CSS 甘特图（非图表库）。
   - **User sees**: 顶部时间轴（按总时长自动选 1/2/3/6 小时刻度避免拥挤，跨天处标日期 `M/D`）；每台打印机一行（左侧 100px 打印机名 + 右侧灰底时间条）；任务为色块，按相对开始时间定位、按时长定宽，块内显示打印盘信息文字，hover `title` 显示「盘信息 / 起止时间 / 状态」。色块颜色：completed=绿、cancelled=灰、failed=红、其余按 is_surplus 橙（富余）/ 蓝（需求）。横向可滚动。
   - **Details**: 甘特图时间范围取所有任务的最早开始~最晚结束（非完整排班周期）。无任务时显示「无排班数据」。

4. **User action**: 切换到「列表视图」Tab。
   - **System response**: `renderList()` 按批次渲染卡片。
   - **User sees**: 每个批次一张 `Card`：标题首批为「批次 1 — HH:MM 启动（首批）」，后续批为「批次 N — HH:MM 收菜，HH:MM 启动」（收菜时间 = 批次启动时间 − 换料时间 `changeoverMin`）。卡片内任务表列为：打印机 / 打印内容（盘名 + 组件/颜色 + 数量/时长，富余加橙 Tag）/ 开始 / 结束（跨天经 `fmtTime` 显示 `MM-DD HH:MM`）/（已确认时多「状态」列）/ 操作列（草稿时有「删除」，执行时有「完成/取消/失败」，见 CUJ-3、CUJ-4）。
   - **Details**: 草稿态卡片右上有「删除批次」；已确认态根据批次状态显示「开始 / 设闹钟」或任务级执行按钮（CUJ-3、CUJ-5）。

### Edge Cases & Error States

- **未选中任何排班**：底部不渲染「排班详情」卡片。
- **排班无批次 / 无任务**：列表视图与甘特图均显示灰字「无排班数据」；排班总结因 `!selectedPlan?.batches?.length` 返回 null（不渲染总结）。
- **任务跨天（end_time ≥ 24:00）**：`fmtTime` 把 `33:40` 渲染为 `MM-DD 09:40`；甘特图时间轴在跨天刻度处标注日期。
- **配置 / 组件查不到**（`configs`/`components` 尚未加载或被删）：`getConfigInfo` 回退 `配置#{id}`、组件名回退 `?`；打印机名回退 `打印机#{id}`。
- **甘特图色块过窄**（短任务）：块内文字 `overflow:hidden; white-space:nowrap` 截断，靠 hover `title` 补全。
- **利用率 > 100% 不会出现**（分母是完整周期），但若任务跨出周期（理论上算法禁止），进度条宽度可能溢出——当前未额外裁剪。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 点击「排班表列表」中某行 → 底部出现「排班详情」卡片，标题含该排班日期 + 开始时间 + 时长。
- 排班详情含「排班总结」表（组件 / 颜色 / 当前库存 / 本次生产 / 排班后库存 / 订单需求 / 仍缺）、「排班后可组装」产品 Tag 行、「打印机利用率」表（每台打印机一行，含工作时长与百分比进度条）。
- 本次生产列对需求量用蓝 Tag、富余量用橙 Tag 区分；仍缺 >0 用红 Tag、否则绿 Tag「充足」。
- 详情区可在「甘特图」与「列表视图」两个 Tab 间切换。
- 甘特图每台打印机一行，任务为按时间定位 / 按时长定宽的色块，hover 显示盘信息与起止时间；completed 绿、cancelled 灰、failed 红、富余橙、普通蓝。
- 列表视图按批次分组成卡片，首批标题含「（首批）」、后续批标题含「收菜」与「启动」两个时间；任务行显示打印机、打印内容（含富余橙 Tag）、开始、结束。
- 选中排班无任务时，列表视图与甘特图均显示「无排班数据」。

---

## CUJ-3：确认排班并按批次执行（状态流转）

**Dependencies**: CUJ-1（有排班）、CUJ-2（能看到批次/任务）；完成入库与 PRD-002 库存、PRD-000 打印盘产量挂钩
**Priority**: P0（排班的价值在「第二天能照着执行并把产出记回库存」）

### Context

第二天白天，作坊主照排班表操作打印机：到点收菜、启动下一批。系统用「批次状态机」承载这个过程：草稿先**确认**锁定；确认后每到一批就点该批「开始」（系统记录实际开始时间并据此重排后续批次的时间，吸收现实中的提前/延后）；每个任务打完后逐个标「完成」（自动把该盘产量入对应组件+颜色库存）、或「取消 / 失败」（不入库）。批内任务全部结束时批次自动转「已完成」。状态机权威描述见 [design-scheduler.md §执行控制状态机](../design/design-scheduler.md)。

### Preconditions

- 已选中一份排班（CUJ-2）。
- 执行类操作（开始 / 完成 / 取消 / 失败 / 设闹钟）仅在排班 `status==confirmed` 时显示。

### Journey Steps

1. **User action**: 在「排班表列表」中点草稿行的「确认」按钮（Popconfirm「确认排班？」）。
   - **System response**: `POST /plans/{id}/confirm` → `status: draft → confirmed`；`reload()` 刷新列表；若该排班是当前 `selectedPlan` 则重新 `getPlan` 刷新详情。
   - **User sees**: 列表中该行状态 Tag 从橙「草稿」变绿「已确认」；详情列表视图每个任务表多出「状态」列，批次卡片右上 / 任务行出现执行按钮。
   - **Details**: 确认后**不可再删除单任务 / 删批次**（草稿专属编辑消失，见 CUJ-4）。批次初始 `status=pending`（显示「待开始」灰 Tag），任务初始 `status=pending`（显示「进行中」灰 Tag——见边界口径说明）。

2. **User action**: 到第一批 / 某批启动时间，点该批次卡片右上「开始」按钮（Popconfirm「确认开始此批次？将以当前时间作为实际开始时间，后续批次会相应调整。」）。
   - **System response**: 前端取**当前本地时间** `HH:MM` 作为 `actual_time`，`POST /batches/{id}/start`。后端把本批 `status→started`、本批所有任务起止时间按实际时间重算，并按打印机可用时间 `+changeover` **重排所有 batch_order 更大且 pending 的批次**（与排班算法一致地按打印机跟踪可用时间），返回 `delta_minutes`（实际 − 计划）。
   - **User sees**: 该批 `Card` 左侧出现蓝色竖条、状态 Tag 变蓝「进行中」；后续批次的启动 / 收菜时间整体平移；toast 提示：delta=0「批次已开始（HH:MM，与计划一致）」，否则「批次已开始（HH:MM，比计划晚/早了 N 分钟，后续批次已调整）」。该批任务行出现「完成 / 取消 / 失败」按钮。
   - **Details**: 「开始」按钮仅在 `confirmed && batch.status=='pending'` 时显示。实际时间取浏览器本地时间，无法手填（见边界）。

3. **User action**: 某台打印机打完后，点该任务行「完成」（Popconfirm「确认完成？库存将自动增加。」）。
   - **System response**: `POST /tasks/{id}/complete` → 任务 `status→completed`，按其打印盘的 `(component_id, color)` 找库存行并 `quantity += 盘产量`；若批内所有任务都已结束（completed/cancelled/failed）则批次 `status→completed`。返回 `added_quantity`。前端 `refreshPlan()` + `reload()`（刷新库存相关数据）。
   - **User sees**: 任务状态 Tag 变绿「已完成」，甘特图对应色块变绿；toast「任务已完成，库存 +{N}」。批内最后一个任务结束后批次 Tag 变绿「已完成」、卡片左侧竖条变绿。
   - **Details**: 入库目标是「组件 + 颜色」精确匹配的库存行；若该组合无库存行则不增加（仅返回 added_quantity 但无行可加，见边界）。已结束的任务再次点击会被后端 400「任务已{status}」拦截。

4. **User action**:（异常路径）某任务需作废——点「取消」或「失败」。
   - **System response**: `POST /tasks/{id}/cancel` 或 `/fail` → 任务 `status→cancelled/failed`，**不入库**；批内全结束同样触发批次完成。
   - **User sees**: 取消——灰 Tag「已取消」、甘特块变灰、蓝色 info toast「任务已取消」；失败——红 Tag「失败」、甘特块变红、橙色 warning toast「任务已标记为失败」。
   - **Details**: 取消与失败的差别仅在语义与颜色 / toast，对库存与批次完成判定的影响一致（都算「已结束、不入库」）。

### Edge Cases & Error States

- **任务 pending 的前端文案歧义**：任务 `status=pending` 在 `taskStatusTag` 中被渲染为「**进行中**」（而非「待开始」）；批次 pending 才渲染为「待开始」。二者口径不一致——任务未开始时也显示「进行中」。如实记录为现状（design-scheduler.md 未覆盖此 UI 细节）。
- **重复结束任务**：对已 completed/cancelled/failed 的任务再调结束接口 → 后端 400「任务已{status}」，前端红色 toast。
- **完成入库时无匹配库存行**：`(component_id, color)` 在 `Inventory` 无对应行时，库存不增加（`if inv:` 才加），但接口仍返回 `added_quantity=盘产量`，toast 仍显示 `+N`——数字与实际库存变化可能不符（现状）。
- **「开始」用浏览器本地时间**：`actual_time` 来自 `new Date()` 本地时间，无手动输入入口；若用户在跨夜时段（如 01:00）启动一个计划在 25:00（次日 01:00）的批次，时间解析按 0~23:59 处理，跨夜重排可能偏差（design-scheduler.md §Open Questions #6）。
- **后续批次重排只动 pending 批次**：已 started / completed 的后续批次不被重排（按 `status=='pending'` 过滤）。
- **确认后再删除排班**：「删除」按钮在 confirmed 行仍可用，会**级联删除该日期及之后所有排班**（CUJ-4 边界同款级联），并清空 `selectedPlan`。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 点草稿行「确认」后该排班状态变「已确认」（绿 Tag），且详情列表视图出现「状态」列与执行按钮；草稿专属的删任务 / 删批次按钮消失。
- 已确认排班中，pending 批次卡片右上显示「开始」按钮；点击后该批状态变「进行中」（蓝 Tag + 卡片左侧蓝竖条），并按当前本地时间重排后续 pending 批次的时间。
- 「开始」后 toast 报告实际开始时间与计划的偏差（一致 / 晚 N 分 / 早 N 分，后续批次已调整）。
- started 批次中任务行显示「完成 / 取消 / 失败」三个按钮。
- 点「完成」后任务状态变「已完成」（绿 Tag）、甘特块变绿，toast「任务已完成，库存 +{N}」，且对应组件+颜色库存增加该盘产量。
- 点「取消」/「失败」后任务分别变灰「已取消」/ 红「失败」，库存不变。
- 批内所有任务结束后批次自动变「已完成」（绿 Tag + 绿竖条）。
- 对已结束任务再点结束类按钮被后端拒绝（400），前端显示错误 toast。

---

## CUJ-4：手动编辑草稿排班（删任务 / 删批次）

**Dependencies**: CUJ-1（有草稿排班）、CUJ-2（能看到任务 / 批次）
**Priority**: P1（specs.md §7 列了删除/替换/增减批次四类编辑；当前实现只落地了「删任务」「删批次」两类，且只对草稿开放）

### Context

生成的排班是启发式产物，作坊主可能想在确认前微调——去掉某个不想打的任务、整批删掉。当前实现把编辑限定在**草稿态**（`!isConfirmed`），提供删单任务、删整批两种操作。specs.md §7 设想的「替换任务配置」「增加批次」当前**前端未提供入口**（替换有后端端点 `PUT /tasks/{id}/config/{new_config_id}` 与 client 方法 `replaceTaskConfig`，但 `Schedule.tsx` 未渲染任何调用它的 UI；「增加批次」无端点也无 UI）。

### Preconditions

- 已选中一份 **草稿**（`status==draft`）排班。确认后所有编辑入口消失。
- 用户在详情「列表视图」Tab（甘特图视图不提供编辑入口）。

### Journey Steps

1. **User action**: 在某草稿批次卡片的任务表中，点某任务行「删除」（Popconfirm「删除此任务？」）。
   - **System response**: `DELETE /tasks/{id}` 删除该任务行 → `refreshPlan()` 重新 `getPlan` 刷新详情。
   - **User sees**: 该任务行从批次表中消失；甘特图对应色块消失；排班总结按剩余任务重算（产量 / 可组装 / 利用率随之下降）。
   - **Details**: 删除**不触发后续批次时间重算**（不同于执行期 start_batch 的重排）；其余任务时间不变，相应打印机这一批次空出。

2. **User action**: 点某草稿批次卡片右上「删除批次」（Popconfirm「删除此批次？」）。
   - **System response**: `DELETE /batches/{id}` 删整批（含其所有任务）→ `refreshPlan()`。
   - **User sees**: 整张批次卡片消失；甘特图该批所有色块消失；总结重算。后续批次序号 / 时间**不自动前移**（仅该批消失）。
   - **Details**: 「删除批次」按钮仅在 `!isConfirmed` 时显示。

3. **User action**:（现状缺口）想替换某任务的打印盘 / 增加一个批次。
   - **System response**: 无前端入口——任务表操作列只有「删除」；批次卡片 extra 只有「删除批次」。`replaceTaskConfig` 端点存在但无 UI 触发。
   - **User sees**: 找不到「替换配置 / 增加批次」按钮。
   - **Details**: 如需替换，当前只能删任务后重生成整份排班，或直接调后端 API。记入「已知问题」。

### Edge Cases & Error States

- **删到批次空 / 排班空**：可逐个删任务直至批次无任务（空卡片仍在，除非删批次）；可删到排班无批次 → 列表 / 甘特显示「无排班数据」。
- **删除不存在的任务 / 批次**（并发或重复点击）：后端 404「任务不存在」/「批次不存在」；前端无显式错误处理，`refreshPlan` 后状态自洽。
- **确认后想编辑**：必须先有草稿；已确认排班无删任务 / 删批次入口。要重排只能删整份排班（级联删该日期及之后所有排班）后重新生成。
- **删除中间日期的整份排班**：经「排班表列表」的「删除」走 `DELETE /plans/{id}`，**级联删除所有日期 ≥ 它的排班**（保供给链一致，schedule_specs.md §10 / design-scheduler.md），Popconfirm 会提示「将同时删除之后的 N 个排班」，删除后 toast 列出被删日期。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 草稿排班的列表视图中，每个任务行有「删除」按钮、每个批次卡片右上有「删除批次」按钮；已确认排班中两者均不显示。
- 点任务「删除」并确认后该任务从批次表与甘特图消失，排班总结随剩余任务重算。
- 点「删除批次」并确认后整批（含全部任务）消失。
- 当前页面**不提供**「替换任务配置」「增加批次」的 UI 入口（仅 `DELETE` 两类编辑可用）。
- 「排班表列表」的「删除」对存在后续排班的行，Popconfirm 提示将同时删除之后 N 个排班；删除后若实际级联删除多份，toast 列出被删日期。

---

## CUJ-5：设收菜闹钟（按批次 / 自定义分钟）

**Dependencies**: CUJ-3（按批次设闹钟需排班已确认且批次 pending）；自定义分钟闹钟无依赖
**Priority**: P1（作坊主白天会离开打印机，靠闹钟提醒「该回去收菜了」，减少反复跑动 / 错过收菜导致打印机空转）

### Context

排班把多台打印机的完成时间对齐（同步强度），但用户仍需在「收菜时刻」回到打印机旁。页面内置一个轻量闹钟：可针对某个待开始批次一键设到「该批收菜时间」，或手动输入「N 分钟后」。到点用 Web Audio 蜂鸣三声 + 浏览器通知 + 页内告警提醒。闹钟是纯前端、单实例（同时只有一个），刷新页面即丢失。

### Preconditions

- 用户在「排班中心」页（闹钟控件常驻，不依赖是否选中排班）。
- 「按批次设闹钟」需排班已确认且该批 `pending` 且非首批（首批无收菜，按钮不显示）。
- 浏览器通知：首次进页面会请求权限（未授权也能蜂鸣 + 页内告警）。

### Journey Steps

1. **User action**:（方式一）在某 pending、非首批的批次卡片点「设闹钟」。
   - **System response**: `setAlarmForBatch` 算该批收菜时刻（批次启动 − 换料时间 `changeoverMin`）距当前本地时间的分钟差 `diff`；`diff>0` 则 `setAlarm(diff)`，否则 warning「该批次收菜时间已过」。
   - **User sees**: 成功——页面顶部出现橙色「闹钟状态栏」卡片：铃铛图标 + 「闹钟：HH:MM 收菜」+ 「倒计时：MM:SS」+「取消」按钮；并弹绿色 toast「闹钟已设定：HH:MM（N分钟后）」。
   - **Details**: 「设闹钟」按钮仅在 `confirmed && batch.status=='pending' && batch_order>0` 时显示（首批无收菜）。设新闹钟会清掉旧定时器（单实例）。

2. **User action**:（方式二）在顶部「快速闹钟」卡片输入分钟数，点「设定」。
   - **System response**: `InputNumber`（min 1）更新 `alarmMinutes`，有值时「设定」按钮可点；点击 `setAlarm(alarmMinutes)`。
   - **User sees**: 「快速闹钟」卡片：时钟图标 +「快速闹钟：」+ 分钟输入框 +「设定」按钮 + 灰字「或在下方批次中点"设闹钟"」。设定后同样出现顶部橙色闹钟状态栏 + 倒计时。
   - **Details**: 闹钟目标时间 = `Date.now() + minutes×60000`，每秒刷新倒计时 `MM:SS`。

3. **User action**: 等待倒计时归零（或离开去操作打印机）。
   - **System response**: 倒计时每秒更新；归零时清定时器、清状态栏，触发提醒：Web Audio `AudioContext` 生成 800Hz 蜂鸣三声（间隔 ~400ms）+ 若通知权限已授予则弹系统通知「收菜时间到！/ 该去打印机收菜换版了」+ 页内 warning toast「收菜时间到！」（持续 10s）。
   - **User sees**: 顶部橙色状态栏消失；听到三声蜂鸣；（已授权时）系统通知；页内橙色 warning。
   - **Details**: 蜂鸣靠 `AudioContext` 即时合成，无音频文件依赖。提醒后闹钟自动结束（一次性，不重复）。

4. **User action**:（可选）提前取消——点状态栏「取消」。
   - **System response**: `cancelAlarm` 清定时器、清状态栏；info toast「闹钟已取消」。
   - **User sees**: 顶部橙色状态栏消失；蓝色 info toast。

### Edge Cases & Error States

- **批次收菜时间已过**：`diff<=0` → warning「该批次收菜时间已过」，不设闹钟。
- **重复设闹钟**：单实例——设新闹钟前 `clearInterval` 旧定时器，旧倒计时被覆盖。
- **通知权限被拒 / 不支持**：仍蜂鸣 + 页内 warning toast，仅缺系统通知。
- **页面刷新 / 关闭 / 离开本页**：闹钟为前端内存态，组件卸载 `clearInterval`，闹钟丢失，不会在后台触发。
- **浏览器自动播放策略 / AudioContext 受限**：`playBeep` 包在 `try/catch`，失败静默（无蜂鸣），页内 warning toast 仍出现。
- **跨夜换算**：收菜时刻按本地 `HH:MM` 用 `now.getHours()*60+...` 算分钟差，若批次启动时间跨过午夜（>24:00），分钟差计算可能不准（与 fmtTime 的跨天展示不在同一坐标系）。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 顶部「快速闹钟」卡片可输入分钟数（最小 1）并点「设定」起一个倒计时闹钟。
- 已确认排班中，pending 且非首批的批次卡片显示「设闹钟」按钮；点击后按该批收菜时间（批次启动 − 换料时间）设闹钟；若该时间已过则提示「该批次收菜时间已过」且不设。
- 设定后页面顶部出现橙色闹钟状态栏，显示目标收菜时间、每秒更新的 MM:SS 倒计时、「取消」按钮。
- 倒计时归零时蜂鸣提示、弹页内 warning「收菜时间到！」、并在通知权限已授予时弹系统通知。
- 点状态栏「取消」清除闹钟并提示「闹钟已取消」。
- 同一时刻只存在一个闹钟（再次设定覆盖前一个）。

---

## 已知问题与 specs 差异（如实引用，不在本 PRD 内修复）

> 以下条目来自 [design-scheduler.md §Open Questions & Risks](../design/design-scheduler.md) 与代码现状核对，影响用户可见行为或文档一致性。本 PRD 为 backfill，仅记录现状，不在此处改实现。

1. **`_pick_task` 双实现分歧**（design-scheduler.md §Open Questions #1）：`product_first`/`utilization` 走 `scheduler.py` 内 multiplicative 同步惩罚；`two_phase` 走 `scheduler_core.py` 的 additive 惩罚 + dynamic batch start timing。两份逻辑不一致，[schedule_specs.md §9.3](../schedule_specs.md) 描述的是**已过时的** multiplicative 版本。对用户的影响：同样的 `sync_strength` 在不同策略下对齐效果不完全一致；schedule_specs.md §9.3 文字与部分路径实现不符。
2. **`SURPLUS_TARGET_PRODUCTS` 口径不一**（#2）：`scheduler.py`=20、`scheduler_core.py`=20、`schedule_specs.md §7.3`=5。富余生成上限的文档与代码不一致；用户开富余时实际可能生成比 specs 文字更多的富余产品。
3. **操作窗口默认 fallback 硬编码**（#3）：无 `ScheduleConfig` 时回退 `[(480,720),(750,1080),(1110,1380)]`（8-12 / 12:30-18 / 18:30-23），与系统设置页前端默认值两处维护，可能漂移。
4. **`changeover` 默认 15 多处内联**（#4）：排班、`start_batch`、前端 `changeoverMin` 各自默认 15，无单一常量。
5. **跨夜时间解析**（#6）：DB 时间允许 >24:00（`33:40`），但 `start_batch` / 闹钟用 `H*60+M` 解析假定 0~23:59；在跨夜时段操作（如凌晨启动计划在次日的批次）时，后续批次重排与闹钟收菜换算可能偏差。
6. **任务 pending 文案**（本 PRD CUJ-3 观察，design 未覆盖）：任务 `status=pending` 前端显示「进行中」而非「待开始」，与批次 pending 的「待开始」文案不一致，易误导未开始任务的状态判读。
7. **完成入库无匹配库存行时数字不符**（本 PRD CUJ-3 观察）：`(component_id, color)` 无库存行时库存实际不增，但接口与 toast 仍报告 `+盘产量`。
8. **specs.md §7 编辑能力未完全落地**（本 PRD CUJ-4 观察）：「替换任务配置」有后端端点 + client 方法但无前端 UI；「增加批次」无端点无 UI。当前只支持删任务 / 删批次。
