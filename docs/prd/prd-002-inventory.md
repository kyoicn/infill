---
id: prd-002
title: 组件库存管理
status: active
created: 2026-06-13
deprecation_reason:
---

# PRD-002：组件库存管理

> 本 PRD 由现有实现反向补写（backfill），描述「当前产品里实际发生的行为」，不是重新设计。
> 与 [docs/specs.md](../specs.md) §1（核心需求·组件库存管理）、§6（库存管理）、§8.4（库存管理页面）、「富余生产策略」章节的用户意图冲突时，**以代码现状为准并在文中标注**。
> 实现链路、发货扣减事务语义、富余口径见 [docs/design/design-orders-inventory.md](../design/design-orders-inventory.md)。库存的**增加**（排班任务完成入库）属排班 PRD，不在本文范围。

## 功能概述

组件库存管理让作坊主在盘点时**实时查看各组件各颜色的当前库存**、**手动调整库存数量**（应对组装损耗、盘点差异），并**查看库存相对待处理订单需求的富余/缺口**。它是 specs.md §6.3（手动调整）与 §8.4（库存管理页）的落地，并通过 `/inventory/surplus` 这一只读口径同时驱动 **Dashboard 的库存预警**。

数据模型（详见 specs.md §3.7、models.py）：

- `Inventory(id, component_id, color, quantity)` —— 库存以「**组件 + 颜色**」为最小粒度，每个组合一条记录。`color` 默认空串 `""`（表示「无颜色」组件）。`quantity` 默认 0、非负。
- 唯一性（`(component_id, color)` 唯一）**仅靠目录加载逻辑保证，DB 层无 unique 约束**（design-orders-inventory.md §Open Questions #1）。
- 富余计算的需求来源是**待处理订单**经产品 BOM（`ProductComponent.component_id/color/quantity`）折算后的组件级需求。

实现文件：
- 后端：`backend/app/routers/inventory.py`（查询 / 增量调整 / 直接设置 / 富余计算），schema 见 `backend/app/schemas.py`（`InventoryOut` / `InventoryAdjust`）。
- 前端：`frontend/src/pages/Inventory.tsx`（库存表格 + 整表行内编辑），`frontend/src/pages/Dashboard.tsx`（库存预警 + 库存与需求表）。
- API 客户端：`frontend/src/api/client.ts`（`getInventory / adjustInventory / setInventory / getSurplus`）。

API 契约（与 design-orders-inventory.md §API 一致）：

| 方法 + 路径 | 请求 → 响应 | 说明 |
|---|---|---|
| `GET /api/inventory` | → `list[InventoryOut]` | 返回全部库存行（`{id, component_id, color, quantity}`），无排序保证、无分页 |
| `POST /api/inventory/adjust` | `InventoryAdjust{component_id,color,quantity}` → `InventoryOut` | **增量**调整：`quantity += data.quantity`，正加负减，结果 < 0 归 0；按 `(component_id,color)` 定位，无记录返回 404。**前端当前未调用此接口** |
| `PUT /api/inventory/{id}` | `InventoryAdjust` → `InventoryOut` | **直接设置**：`quantity = max(0, data.quantity)`；按 `inventory_id` 定位，无记录返回 404。前端整表编辑保存走此接口 |
| `GET /api/inventory/surplus` | → `list[{component_id, component_name, color, stock, demand, surplus}]` | 富余计算：`demand` = 全部 `pending` 订单按 BOM 折算的组件级需求；`surplus = stock - demand`；按 `(component_id, color)` 升序排序 |

关键口径说明（重要，影响多个 CUJ）：

- **富余 = 组件级「库存 − 待处理订单需求」，不是「折算为可组装产品数」**。specs.md §1 / 「富余生产策略」期望「界面显示当前库存折算为多少富余**产品**的量」；**当前实现只算到组件 + 颜色维度的盈亏**（`surplus = stock - demand`），未做「瓶颈组件 → 可组装整产品数」的折算。这是 specs 意图与代码现状的核心差异，本 PRD 以代码现状（组件级富余）为准并在文末标注。
- **富余口径只看待处理订单需求，不含已排班产出**。与排班算法 `_get_initial_supply`（库存 + 已排班产出）口径不同，用户在 Dashboard/库存页 与 排班页总结 看到的「富余/缺口」数字可能不一致（design-orders-inventory.md §Open Questions #3）。

本 PRD 范围：
- CUJ-1：查看组件库存与富余（库存管理页只读视图）
- CUJ-2：手动调整库存数量（整表行内编辑 → 直接设置）
- CUJ-3：Dashboard 库存预警与库存/需求总览（富余口径的只读消费）

不在本 PRD 范围：库存「增加」入库（排班任务完成，属排班 PRD）、发货扣减（属 PRD-001 订单管理 CUJ-3）、库存流水/审计日志、按颜色批量增减接口（`/adjust` 增量接口虽存在但前端未接入）、「库存折算为可组装产品数」的富余生产折算（未实现，仅记为差异）。

---

## CUJ-1：查看组件库存与富余

**Dependencies**: 无（功能上依赖 PRD-000 目录已加载从而有组件 + 库存行；富余的 `demand` 依赖 PRD-001 有待处理订单，但无订单时 `demand=0`、`surplus=stock`，页面仍可正常展示）
**Priority**: P0（晚间盘点核对库存、判断是否需要补料/排产的主视图）

### Context

作坊主盘点时需要一眼看清「每个组件每种颜色现在有多少、对照当前待处理订单是富余还是缺口」。这是 specs.md §8.4 的库存管理页核心只读视图。页面把两个数据源合并展示：`GET /api/inventory`（真实库存行，含 `id`，用于后续编辑定位）+ `GET /api/inventory/surplus`（提供 `component_name` 与 `demand`）。展示口径是**组件 + 颜色级**的库存 / 需求 / 富余，而非折算到可组装产品数（见功能概述差异说明）。

### Preconditions

- 后端已启动，PRD-000 目录已加载，`Inventory` 表中存在库存行（目录加载时为每个「组件 + 颜色」建一条，初始 `quantity=0`）。
- 用户在浏览器打开应用，已导航到「库存管理」页（左侧深色侧边栏，主区顶部页面标题「库存管理」）。

### Journey Steps

1. **User action**: 进入「库存管理」页。
   - **System response**: 组件挂载时 `reload()` 并行发起 `GET /api/inventory` 与 `GET /api/inventory/surplus` 两个请求；返回后在前端把两者**以库存行为主**合并：对每条 `inventory` 行，按 `component_id` + `color`（库存的 `color || ''`）在 `surplus` 数组里找对应项，取其 `component_name` 与 `demand`；找不到时 `component_name` 回退为 `组件#{component_id}`、`demand` 回退为 `0`。
   - **User sees**: 标题「库存管理」下一张卡片（`Card`），卡片右上角 `extra` 区有「编辑库存」按钮（带铅笔图标）。卡片内一张 `size="small"`、**不分页**（`pagination={false}`）的表格，列依次为：组件（`component_name`）、颜色（宽 80，空颜色渲染为 `-`）、当前库存（宽 140，数字）、订单需求（宽 100，数字）、富余（宽 100，Tag）。
   - **Details**: 富余列 = `stock - demand`，渲染为 Tag：`>= 0` 绿色显示 `+N`（含 `+0`），`< 0` 红色显示负数（如 `-3`）。表格行顺序跟随 `GET /api/inventory` 的返回顺序（后端无显式排序），与 surplus 接口的 `(component_id,color)` 升序排序**不一定一致** —— 见边界。

2. **User action**: 浏览表格，逐行核对库存与富余。
   - **System response**: 纯展示，无交互。
   - **User sees**: 每行形如「龙身 | 白色 | 12 | 7 | [+5]（绿）」或「眼片 | 黑色 | 2 | 6 | [-4]（红）」。
   - **Details**: 「订单需求」是该「组件 + 颜色」在所有待处理订单中按 BOM 折算的总需求量（产品级数量 × BOM 单位用量，跨订单累加）。同一组件不同颜色是**独立行、独立富余**，不汇总到组件层。

### Edge Cases & Error States

- **空库存（无库存行）**：`Inventory` 表为空时表格显示 Ant Design 默认空状态「暂无数据」。当前无定制空状态文案/引导（如「目录尚未加载或无组件」）—— **体验缺口**。
- **库存有行但无待处理订单**：`surplus` 接口对每个库存键返回 `demand=0`、`surplus=stock`，全部富余列为绿色 `+stock`；正常展示。
- **surplus 中存在「有需求但无库存行」的键**：surplus 接口会把「需求键 ∪ 库存键」并集输出（含 `stock=0` 的纯需求项），但**本页以 `inventory` 行为主**做合并，故这类「只在需求侧、库存表里没有对应行」的组件**不会出现在库存页**（只会出现在 Dashboard 的 surplus 表，见 CUJ-3）。两页因此可能出现行数差异 —— **已知口径差异**。
- **行顺序不稳定 / 颜色对齐**：库存行的展示顺序取决于 `GET /api/inventory` 返回顺序（无 `ORDER BY`），与 surplus 的升序不一致；合并匹配靠 `component_id + (color||'')`，若库存 `color` 为 `null` 也会被规整为 `''` 再匹配。规模小无功能影响，但行序在不同请求间可能不稳定。
- **请求失败**：`reload()` 中两个 `then` 未挂 `catch`，网络/服务端异常会抛到控制台、表格保持上一次（或空）状态、无错误提示给用户 —— **已知缺口**（与 Dashboard 的 `.catch(()=>{})` 静默处理也不同）。
- **库存为 0**：正常显示 `0`，富余按 `0 - demand` 计算（无订单时为 `+0` 绿色，有需求时为负红色）。
- **`Inventory` 重复行**：因 DB 无 `(component_id,color)` 唯一约束，理论上的重复行会各自成行展示、且都匹配到同一 surplus 项（demand 重复显示），单用户场景概率极低但口径上无保护（design-orders-inventory.md §Open Questions #1）。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 进入「库存管理」页展示一张表格，列含：组件、颜色（空颜色显示 `-`）、当前库存、订单需求、富余。
- 富余列等于 `当前库存 − 订单需求`，以 Tag 呈现：≥ 0 为绿色并带 `+` 前缀，< 0 为红色显示负数。
- 表格以 `GET /api/inventory` 的库存行为基础逐行展示，每条「组件 + 颜色」为独立一行，不同颜色不合并。
- 「订单需求」反映所有待处理订单按 BOM 折算到该「组件 + 颜色」的累计需求量；无待处理订单时需求为 0、富余为 `+库存`。
- 组件名缺失（surplus 未提供）时该行组件名回退显示为 `组件#{component_id}`。
- 库存表为空时表格显示「暂无数据」空状态。

---

## CUJ-2：手动调整库存数量

**Dependencies**: CUJ-1
**Priority**: P0（specs.md §6.3 手动调整；盘点差异、组装损耗的唯一人工修正入口）

### Context

盘点时库存与系统记录会有差异（组装损耗、报废、漏记入库等）。作坊主需要直接把某些组件库存改成实际盘点数。当前实现是**整表行内编辑**：点「编辑库存」让整张表进入可编辑态，把任意行的库存改成绝对数值，统一「保存」。保存对**每个被改动的行**调用 `PUT /api/inventory/{id}`（直接设置语义，`quantity = max(0, 输入值)`）。注意：后端另有一个 `POST /adjust` 的**增量**接口（正加负减），但**前端未使用** —— UI 上只有「设为绝对值」一种交互。

### Preconditions

- 用户在「库存管理」页，CUJ-1 的库存表已加载、至少有一行库存。
- 表格当前处于只读态（`editing=false`，右上角显示「编辑库存」按钮）。

### Journey Steps

1. **User action**: 点击卡片右上角「编辑库存」按钮（铅笔图标）。
   - **System response**: 进入编辑态：以当前每行 `stock` 为初值填充 `editValues`（按行 `id` 索引），`editing=true`。卡片右上角按钮组切换为「保存」（蓝色 primary，对勾图标）+「取消」（叉图标）。
   - **User sees**: 「当前库存」列的每一行从纯数字变为一个 `InputNumber` 数字输入框（`min=0`、宽 100、`size="small"`），预填当前库存值；「富余」列仍显示 Tag。其余列（组件/颜色/订单需求）保持只读。
   - **Details**: 编辑是**整表级**的（一次进入所有行可编辑），非单行编辑。`editValues` 是前端本地草稿，未保存前不落库。

2. **User action**: 在某些行的库存输入框里把数值改成盘点实际值（如把「眼片/黑色」从 `2` 改为 `8`）。
   - **System response**: 对应 `editValues[id]` 实时更新；该行「富余」Tag 实时按 `editValues[id] - demand` 重算颜色与数值（编辑态下富余跟随草稿值变化，未保存即可预览富余结果）。
   - **User sees**: 输入框显示新值 `8`；该行富余 Tag 从红色 `-4` 变为绿色 `+2`（示例 demand=6）。
   - **Details**: 输入框 `min=0`，无上限；清空/非法输入经 `val ?? 0` 兜底为 0。富余预览仅前端计算，不请求后端。

3. **User action**: 点击「保存」。
   - **System response**: 前端筛出**值发生变化的行**（`editValues[r.id] !== r.stock`），对每个变化行**并行**（`Promise.all`）调用 `PUT /api/inventory/{id}`，body 为 `{component_id, color, quantity: editValues[id]}`；后端对每行执行 `quantity = max(0, data.quantity)` 并落库。全部成功后退出编辑态、`reload()` 重新拉库存与富余、弹绿色 message「库存已更新」。
   - **User sees**: 表格回到只读态，被改动行显示新库存与重算后的富余；顶部绿色提示「库存已更新」。
   - **Details**: 只提交**变化的行**（未改动行不发请求）。`PUT` 用行 `id` 定位（直接设置语义），与 `/adjust` 的增量语义不同。后端 `max(0, ...)` 保证非负；前端 `min=0` 也已挡负值，双重兜底。

4. **User action**（可选）: 点「取消」。
   - **System response**: `editing=false`、清空 `editValues`，丢弃所有草稿改动。
   - **User sees**: 表格回到只读态，所有值恢复为编辑前的库存（草稿未提交不影响）。
   - **Details**: 取消不发任何请求。

### Edge Cases & Error States

- **未改动任何行就保存**：`filter` 后 `promises` 为空数组，`Promise.all([])` 立即 resolve，仍会退出编辑态、`reload()`、弹「库存已更新」 —— 即「空保存」也提示成功（无副作用，但提示可能误导）。**轻微体验瑕疵**。
- **保存中某行请求失败**：`Promise.all` 任一 reject 即进入 `catch`，弹红色 message 显示错误信息；但**此时表格仍停在编辑态、未 reload**，且**已成功的那些行已落库**（`Promise.all` 不回滚已完成的请求）—— 多行编辑批量保存**非原子**，可能部分成功。**已知风险**（design 未覆盖此前端批量路径，与订单批量提交同类问题）。
- **输入负数 / 清空**：`InputNumber min=0` 阻止手输负数；清空时 `onChange` 收到 `null`，经 `val ?? 0` 存为 0；后端再 `max(0,...)` 兜底。最终不会落库为负。
- **输入超大值**：输入框无上限，可设极大库存；后端不校验上限，原样落库并进入富余/需求计算。
- **库存行被并发改动**：单用户场景下并发概率低；若另一处（如发货扣减）在编辑期间改了库存，保存的是**绝对覆盖**（直接 set），会覆盖掉期间发生的扣减结果 —— **直接设置语义的固有风险**，无乐观锁/冲突检测。
- **`adjust` 增量接口未接入**：specs.md §6.3「增减」语义在后端有 `POST /adjust` 支持（正加负减），但前端只暴露「设为绝对值」的整表编辑；UI 上无「+N / −N」式的增量调整入口 —— **能力与 UI 的差异**（接口存在但未用）。
- **编辑态下新行/数据刷新**：编辑期间不会自动 reload，`editValues` 按进入编辑时的快照维护；新库存行（如目录重载新增组件）要等退出编辑并刷新后才出现。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 只读态下卡片右上角显示「编辑库存」按钮；点击后整表「当前库存」列变为预填当前值的数字输入框，按钮组切换为「保存 / 取消」。
- 编辑态下修改某行库存值，该行「富余」Tag 实时按 `新值 − 订单需求` 重算（颜色与数值随草稿变化），无需保存即可预览。
- 库存输入框最小值为 0，无法保存为负数（前端 `min=0` + 后端 `max(0,·)` 双重保证）。
- 点「保存」后，仅对值发生变化的行调用 `PUT /api/inventory/{id}` 直接设置库存；全部成功则退出编辑态、刷新表格、弹「库存已更新」。
- 点「取消」丢弃所有草稿改动、恢复编辑前数值、不发请求。
- 保存成功后表格回到只读态并展示更新后的库存与重算富余。

---

## CUJ-3：Dashboard 库存预警与库存/需求总览

**Dependencies**: CUJ-1（共用 `/inventory/surplus` 口径）
**Priority**: P1（specs.md §8.1 仪表盘的「库存预警」「富余状态」；盘点前的快速概览入口）

### Context

作坊主打开应用首先看到仪表盘。specs.md §8.1 要求仪表盘给出「库存预警（低库存组件提示）」与「富余状态」。当前 Dashboard 复用 `GET /inventory/surplus` 这一组件级口径：把 `surplus < 0`（即库存不足以覆盖待处理订单需求）的组件数作为「库存预警」计数，并用同一份 surplus 数据渲染一张「组件库存与需求」总览表。这是库存富余口径的**只读消费方**，与库存管理页同源但展示范围略有差异（surplus 含纯需求项）。

### Preconditions

- 后端已启动，目录已加载。
- 用户在浏览器打开应用，停在「仪表盘」首页（默认页）。

### Journey Steps

1. **User action**: 打开应用 / 进入「仪表盘」。
   - **System response**: 组件挂载时并行发起三个请求：`GET /api/orders?status=pending`、`GET /api/inventory/surplus`、`GET /api/printers`，三者各自 `.catch(() => {})` 静默吞错（失败则对应数据保持空数组）。前端计算 `lowStock = surplus.filter(s => s.surplus < 0)`。
   - **User sees**: 标题「仪表盘」下一行三张统计卡（各占 1/3 宽）：「待处理订单」（购物车图标，值 = 待处理订单数）、「打印机数量」（打印机图标，值 = 打印机数）、「库存预警」（收纳箱图标，值 = `lowStock.length`；当 > 0 时数字显示为红色 `#cf1322`，否则默认色）。
   - **Details**: 「库存预警」计数口径 = surplus 为负的「组件 + 颜色」**行数**（即有多少个组件颜色缺料），不是缺料总件数。

2. **User action**: 向下查看「组件库存与需求」卡片表格。
   - **System response**: 用整份 `surplus` 数据（含库存键 ∪ 需求键并集）渲染 `size="small"`、不分页的表格。
   - **User sees**: 卡片标题「组件库存与需求」，表格列：组件（`component_name`）、颜色（宽 80，空显示 `-`）、库存（`stock`）、订单需求（`demand`）、富余（`surplus`，Tag：≥0 绿 `+N` / <0 红负数）。`rowKey` 为 `component_id:color`。
   - **Details**: 与库存管理页（CUJ-1）相比，**本表用 surplus 接口的完整并集**，故会包含「有需求但库存表里无对应行（stock=0）」的组件；而库存管理页以 `inventory` 行为主、不含这类纯需求项。两页因此可能呈现不同的行集合 —— **已知口径差异**。

### Edge Cases & Error States

- **任一请求失败**：三个请求各自 `.catch(() => {})`，失败仅令对应数据为空数组、无报错提示。如 surplus 失败 → 库存预警显示 0、库存/需求表为空，**用户可能误判为「无预警」**。**已知缺口**（静默吞错掩盖真实状态）。
- **无待处理订单**：所有 `demand=0`、`surplus=stock≥0`，`lowStock` 为空、预警计数 0（非红）；表格全绿。
- **无库存且有需求**：纯需求项 `stock=0`、`surplus=-demand` 为负，计入预警、表中红色显示，提示需要补料/排产。
- **预警口径是行数非件数**：预警值是「缺料组件颜色的种类数」，不反映缺口严重程度（缺 1 件与缺 100 件都只 +1 行计数）。**口径局限**，盘点时需进表格看具体缺口。
- **富余 = 组件级盈亏，非可组装产品数**：与 specs.md §8.1「富余状态：当前库存相当于多少个富余产品」的措辞不一致 —— 当前仪表盘给的是**组件级**库存/需求/富余表，未给「可多组装几个整产品」的产品级数字。**已知差异**（见文末标注 #1）。
- **表格无空状态定制**：surplus 为空（无库存无订单）时表格显示默认「暂无数据」。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 仪表盘顶部展示三张统计卡：待处理订单数、打印机数量、库存预警数。
- 「库存预警」值等于 `/inventory/surplus` 中 `surplus < 0` 的行数；该值 > 0 时数字以红色显示。
- 「组件库存与需求」表用 surplus 完整数据渲染，列含组件、颜色（空显示 `-`）、库存、订单需求、富余（Tag，≥0 绿带 `+`、<0 红负数）。
- 该表包含 surplus 接口返回的全部项（含库存为 0 的纯需求项），可能多于库存管理页的行集合。
- 任一数据请求失败时页面不崩溃（对应区块以空/0 呈现，无报错弹窗）。

---

## 与 specs.md 用户意图的差异备注（backfill 标注）

下列为「specs.md / 设计意图」与「代码现状」的差异，本 PRD 以代码现状为准并在此集中标注，供后续迭代取舍：

1. **富余未折算为「可组装产品数」**：specs.md §1 / §8.1 / 「富余生产策略」期望「界面显示当前库存折算为多少富余**产品**的量」。**现状只算到组件 + 颜色级的盈亏**（`surplus = stock - demand`），未实现「找瓶颈组件 → 折算可多组装几个整产品」的产品级富余。库存页与仪表盘呈现的都是组件级数字。
2. **富余口径不含已排班产出**：`/inventory/surplus` 只看待处理订单需求，不叠加已排班/在产产出；与排班算法 `_get_initial_supply`（库存 + 已排班产出）不同口径。用户在库存页/仪表盘 与 排班页总结 可能看到不一致的富余/缺口数字（design-orders-inventory.md §Open Questions #3）。
3. **增量调整接口未接入 UI**：specs.md §6.3「手动调整库存（增减）」在后端有 `POST /adjust` 增量语义支持，但前端只提供「整表设为绝对值」的编辑交互，无「+N / −N」式增量入口。
4. **批量保存非原子**：库存整表编辑保存是多个独立 `PUT` 请求（`Promise.all`），中途失败会部分落库且停在编辑态，与「一次盘点修正应整体生效」的隐含期望存在落差（与订单批量提交同类问题）。
5. **直接设置无冲突检测**：保存用绝对覆盖（直接 set），编辑期间若库存被其他路径（如发货扣减）改动，保存会覆盖该改动，无乐观锁/版本校验。
6. **请求失败静默/无提示**：库存页 `reload` 未挂 `catch`（异常进控制台、无 UI 提示）；仪表盘三请求 `.catch(()=>{})` 静默吞错（失败时预警可能误显示为 0）。两处都缺少明确的错误反馈。
7. **库存预警口径是「缺料组件种类数」非缺口件数**：仪表盘预警值只计缺料行数，不反映缺口严重程度。
8. **库存表无 DB 唯一约束**：`(component_id, color)` 唯一性仅靠目录加载逻辑保证，DB 层无约束（design-orders-inventory.md §Open Questions #1）。
