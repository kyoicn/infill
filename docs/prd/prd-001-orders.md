---
id: prd-001
title: 订单管理
status: active
created: 2026-06-13
deprecation_reason:
---

# PRD-001：订单管理

> 本 PRD 由现有实现反向补写（backfill），描述「当前产品里实际发生的行为」，不是重新设计。
> 与 [docs/specs.md](../specs.md) §1（核心需求·订单管理）、§4（每日工作流）、§6.2（库存扣减）、§8.3（订单管理页面）以及「订单优先级·软 FIFO」章节的用户意图冲突时，**以代码现状为准并在文中标注**。
> 实现链路、发货扣减事务语义、富余口径见 [docs/design/design-orders-inventory.md](../design/design-orders-inventory.md)。

## 功能概述

订单管理是「晚间盘点」工作流的入口环节：作坊主在一个页面里**录入当天新订单**、查看**待处理订单队列**、**标记订单发货**（系统按 BOM 自动扣减组件库存）、回看**已发货历史**。

数据模型（详见 specs.md §3.5/§3.6）：

- `Order(id, created_at, status: pending|shipped, shipped_at)` 一对多 `OrderItem(product_id, quantity)`。
- 订单**没有客户、价格、备注、来源等字段** —— 一个订单就是「一组产品 + 各自数量 + 创建时间 + 状态」。
- 库存按「组件 + 颜色」维度记录（`Inventory(component_id, color, quantity)`）；发货扣减按产品 BOM（`ProductComponent.component_id/color/quantity`）折算。

实现文件：
- 后端：`backend/app/routers/orders.py`（订单 CRUD + 发货扣减），schema 见 `backend/app/schemas.py`。
- 前端：`frontend/src/pages/Orders.tsx`（单页，含列表、新增弹窗、发货/删除操作）。
- API 客户端：`frontend/src/api/client.ts`（`getOrders / createOrder / shipOrder / deleteOrder`）。

API 契约（与 design-orders-inventory.md §API 一致）：

| 方法 + 路径 | 请求 → 响应 | 说明 |
|---|---|---|
| `GET /api/orders?status=` | → `list[OrderOut]` | 按 `created_at` **升序**；`status` 可选，传 `pending` / `shipped` 过滤 |
| `POST /api/orders` | `OrderCreate{items:[{product_id,quantity}]}` → `OrderOut` | 新建单个订单 |
| `GET /api/orders/{id}` | → `OrderOut` | 取单（前端当前未调用） |
| `POST /api/orders/{id}/ship` | → `{ok:true}` | 标记发货 + 按 BOM 扣库存（整单事务） |
| `DELETE /api/orders/{id}` | → `{ok:true}` | 删除订单（级联删 item），**不校验状态、不回补库存** |

`OrderOut` 字段：`{id, created_at, status, shipped_at, items:[{id, product_id, quantity}]}`。注意：明细只返回 `product_id`，**不返回产品名** —— 前端用单独拉取的产品列表 `getProducts()` 在内存里做 id→name 映射展示。

### Order 表字段扩展（新增，prd-006 自动导入需要）

为支持 [PRD-006 自动导入订单](./prd-006-auto-import-orders.md)（小红书千帆 + 闲鱼），`Order` 表追加以下 4 个字段。**所有字段均 nullable，人工录入订单保持原行为（全部留空），向后兼容**：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `platform` | VARCHAR | 是 | 值域 `'xhs'`（小红书千帆） / `'xianyu'`（闲鱼） / `NULL`。NULL = 人工录入的本地订单。 |
| `external_order_id` | VARCHAR(64) | 是 | 平台侧原始订单号（如 `XHS-2026-001250`）；人工录入留空。 |
| `buyer_nickname` | VARCHAR(128) | 是 | 平台买家昵称；人工录入留空。 |
| `external_created_at` | DATETIME | 是 | 平台侧下单时间，区别于 `created_at`（infill 系统接收时间）。人工录入留空。 |

**新增唯一约束**：`UNIQUE (platform, external_order_id) WHERE platform IS NOT NULL AND external_order_id IS NOT NULL` —— SQLite 支持的 partial unique index，**人工录入订单（两字段均为 NULL）不参与去重**，可以重复创建。

**重复订单 override 约定**：当用户在自动导入预览阶段把一条被识别为「重复」的订单点「改判为新单」（典型场景：买家退货后下了同一份订单的复刻单，平台沿用了同一个 `external_order_id`），后端在写入前给原 ID 追加 `-redoN` 后缀（首次 `-redo1`、二次 `-redo2`，依此类推），例如 `XHS-2026-001250` 改写为 `XHS-2026-001250-redo1` 再落库。这样：
- 去重逻辑无需任何 schema 改动（唯一约束天然放行后缀化后的 ID）；
- 历史可追溯（原 ID 仍可从后缀反推）；
- 自动导入侧的「重复检测」只需按完整 `external_order_id`（含后缀）查 DB 即可。

详见 [prd-006 CUJ-2 的重复订单处理](./prd-006-auto-import-orders.md)。

本 PRD 范围：
- CUJ-1：录入新订单（一次可批量创建多个订单）
- CUJ-2：查看与管理待处理订单队列（含待处理需求汇总、删除订单）
- CUJ-3：标记订单发货并自动扣减库存
- CUJ-4：查看已发货订单历史

不在本 PRD 范围：订单的客户/价格/物流信息、退货/撤销发货、订单编辑（创建后不可改明细）、订单与具体产出批次的追溯绑定、库存的查询/手动调整/富余页面展示（属库存管理 PRD）、排班算法对订单需求的消费（属排班 PRD）。

---

## CUJ-1：录入新订单

**Dependencies**: 无（功能上依赖 PRD-000 的产品目录已加载 —— 没有产品则无法选择，但不构成本 PRD 内的 CUJ 依赖）
**Priority**: P0（晚间盘点的第一步，整个生产链路的需求源头）

### Context

作坊主每天晚间盘点时把当天新到的订单录进系统。订单是后续排班需求计算和库存扣减的源头。当前实现刻意做得很轻：一个订单只承载「买了哪些产品、各几个」，没有客户名/金额/平台来源等字段 —— 与 specs.md §3.5 的极简订单模型一致。为减少重复点击，新增弹窗支持**一次性起草多个订单、批量提交**。

### Preconditions

- 后端已启动，PRD-000 的产品目录已加载，DB 中至少有一个 `Product`（否则产品下拉为空，无法录入有效订单 —— 见边界）。
- 用户在浏览器打开应用，已导航到「订单管理」页（左侧深色侧边栏，主区顶部为页面标题「订单管理」）。

### Journey Steps

1. **User action**: 点击页面右上角卡片 `extra` 区的「新增订单」按钮（蓝色 primary，带 `+` 图标）。
   - **System response**: 弹出标题为「新增订单」的居中模态框（宽 600px，内容区 `maxHeight: 60vh` 可纵向滚动），初始化为**一个**草稿订单「订单 1」，其下含**一行**空产品明细（产品未选、数量空）。
   - **User sees**: 模态框内自上而下：粗体小标题「订单 1」；一行明细控件 —— 产品下拉（占位「选择产品」，宽 200px）+ 数量 `InputNumber`（占位「数量」，最小值 1）+ 一个红色删除按钮（垃圾桶图标）；下方虚线「+ 添加产品」按钮；再下方分隔线与块级虚线「+ 再加一个订单」按钮。底部主按钮文案为「创建 0 个订单」且**处于禁用态**（当前无有效订单）。
   - **Details**: 草稿状态全部存在前端组件 `drafts` 中，未提交前不落库。数量框无上限（`min={1}`，无 `max`）。

2. **User action**: 在「订单 1」第一行的产品下拉里选择一个产品（如「龙猫摆件」），并在数量框输入数量（如 `3`）。
   - **System response**: 该明细行变为有效；底部主按钮文案实时更新为「创建 1 个订单」并变为可点击（蓝色 primary）。
   - **User sees**: 下拉显示所选产品名，数量框显示 `3`；底部主按钮高亮「创建 1 个订单」。
   - **Details**: 有效性判定（前端 `validDrafts`）：一个草稿订单有效 ⟺ 其 `items` 非空且**每一行**都满足 `product_id != null && quantity != null && quantity > 0`。任意一行残缺会让整张草稿订单被判为无效、不计入按钮计数。

3. **User action**（可选，多产品订单）: 点击「订单 1」下的虚线「+ 添加产品」。
   - **System response**: 在「订单 1」内追加一行空明细（产品未选、数量空）。
   - **User sees**: 「订单 1」下新增一行产品下拉 + 数量框 + 删除按钮。
   - **Details**: 同一订单内允许重复选同一产品（系统不去重、不合并；两行同产品会作为两条 `OrderItem` 提交）。新增的空行会立刻使该草稿订单重新变为无效，直到这行填全。

4. **User action**（可选，批量录单）: 点击底部块级虚线「+ 再加一个订单」。
   - **System response**: 追加一张新草稿订单「订单 2」，其上方出现分隔线，内含一行空明细；因「订单 2」尚未填全，底部按钮计数暂不增加。
   - **User sees**: 「订单 2」标题旁出现红色「删除此订单」按钮（仅当草稿订单数 > 1 时显示）。
   - **Details**: 移除明细行用行尾红色删除按钮；若把某张草稿订单的明细删到 0 行，该草稿订单整体被移除。

5. **User action**: 点击底部主按钮「创建 N 个订单」。
   - **System response**: 前端**逐个串行**调用 `POST /api/orders`（`for (const draft of validDrafts) await api.createOrder(...)`）；**只提交有效草稿订单**，无效草稿订单被静默跳过。全部成功后关闭模态框，弹出绿色全局提示「已创建 N 个订单」，并刷新列表（重新拉当前 Tab 的订单 + 产品）。
   - **User sees**: 模态框关闭；顶部居中绿色 message「已创建 N 个订单」；待处理列表新增 N 行，每行创建时间为刚才、状态标签「待处理」（橙色）。
   - **Details**: 后端为每个订单建 `Order`（`status` 默认 `pending`，`created_at` 默认 `datetime.now()`）+ 若干 `OrderItem`，单请求事务提交。批量提交是 N 个独立请求，**非单事务** —— 见边界（中途失败的半成功问题）。

### Edge Cases & Error States

- **无有效订单点提交**：底部按钮在 `validDrafts.length === 0` 时禁用，正常点不到。代码里 `submitAll` 仍有兜底：若被调用且无有效订单，弹红色 message「没有有效的订单」并直接返回。
- **明细半残缺**：某行只选了产品没填数量（或反之），整张草稿订单判无效、不计入提交、被静默跳过 —— 无逐行报错提示，用户可能误以为录进去了。**这是已知体验缺口**（无字段级校验反馈）。
- **产品目录为空**：产品下拉无选项，无法构造有效订单，按钮恒禁用。当前无空目录的引导文案。
- **批量提交中途失败**：N 个订单是 N 个独立 `await` 请求且**非原子**。若第 k 个请求抛错，`catch` 弹红色 message 显示错误信息，但**前 k-1 个订单已落库**，模态框不关闭、列表未刷新 —— 用户看到报错却不知已部分成功，重试会重复创建。**已知风险**（design-orders-inventory.md 未覆盖此前端批量路径）。
- **数量极大值**：数量框无上限，可输入超大整数；后端不校验，会原样落库并进入需求/扣减计算。
- **重复产品行**：同一订单内两行选同一产品不报错、不合并，落库为两条 `OrderItem`。
- **取消**：点模态框「取消」或遮罩关闭，丢弃所有草稿，不落库。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 点「新增订单」弹出模态框，初始含一张草稿订单「订单 1」与一行空明细。
- 仅当某草稿订单所有明细行都选了产品且数量 ≥ 1 时，该订单计入底部按钮计数；计数为 0 时按钮禁用。
- 可在一张草稿订单内通过「添加产品」增加多行明细；可通过「再加一个订单」增加多张草稿订单。
- 草稿订单数 > 1 时每张订单标题旁出现「删除此订单」按钮；删某订单的最后一行明细会整张移除该订单。
- 点「创建 N 个订单」后，N 个有效订单全部成功时模态框关闭、弹「已创建 N 个订单」、列表新增 N 行待处理订单。
- 新建订单 `status` 为 `pending`、`created_at` 为创建时刻、明细与录入的产品/数量一致。本 CUJ 的人工录入路径下，`platform / external_order_id / buyer_nickname / external_created_at` 四个字段均留空（自动导入订单还有 `platform / external_order_id / buyer_nickname / external_created_at` 几个字段，由 PRD-006 负责填充）。
- 无有效订单时按钮禁用，无法提交空内容。

---

## CUJ-2：查看与管理待处理订单队列

**Dependencies**: CUJ-1
**Priority**: P0（盘点时核对待处理需求、清理误录订单的主视图）

### Context

录单后，作坊主在「待处理」Tab 看当前积压的订单队列，并据此判断今天要排产什么。specs.md「订单优先级·软 FIFO」要求队列大致按创建时间排序、由排班算法弹性消费。**当前订单页只负责按时间升序呈现队列与汇总产品级需求，软 FIFO 的「跳过已满足订单/库存够则直接发货」逻辑不在本页实现** —— 它属于排班算法（PRD-排班），本页不展示任何「跳过/优先级」状态。本 CUJ 也覆盖**删除订单**这一行级操作。

### Preconditions

- 至少存在一个 `pending` 订单（否则表格为空）。
- 用户在「订单管理」页，Tab 默认停在「待处理」（`tab` 初始为 `pending`）。

### Journey Steps

1. **User action**: 进入「订单管理」页（或在三个 Tab 间切换）。
   - **System response**: 页面顶部为 Tabs「待处理 / 已发货 / 全部」。切 Tab 触发 `reload()`：`待处理`→`GET /api/orders?status=pending`、`已发货`→`?status=shipped`、`全部`→无 status 参数取全部；同时刷新产品列表用于 id→name 映射。后端按 `created_at` **升序**返回。
   - **User sees**: 当前 Tab 高亮。下方一张表格（`size="small"`，每页 20 条分页），列依次为：订单号（`id`，宽 80）、创建时间（宽 180，按 `zh-CN` 本地化显示）、状态（宽 100，标签：`pending`→橙色「待处理」/`shipped`→绿色「已发货」）、产品明细（把 `items` 渲染为 `产品名 xN, 产品名 xN` 逗号拼接）、操作（宽 160）。
   - **Details**: 排序固定按 `created_at` 升序（最早的在最上），表格列无客户端排序控件。产品明细的产品名来自内存映射；若映射缺失（产品被目录移除）回退显示 `#{product_id}`。

2. **User action**: 阅读「待处理需求」汇总条。
   - **System response**: 当列表中存在待处理订单时，表格上方渲染一条浅灰底（`#fafafa`）汇总条：把所有待处理订单的明细**按产品聚合数量**，按数量降序，渲染为蓝色 Tag 列表「产品名 xN」，末尾灰字「共 M 个订单」。
   - **User sees**: 形如「**待处理需求：** [龙猫摆件 x12] [皮卡丘 x7] [小恐龙 x3]   共 9 个订单」。
   - **Details**: 汇总口径是**产品级数量**（不折算到组件、不减库存），与排班/库存页的「组件级需求/富余」口径不同 —— 仅用于盘点时快速看「今天总共要交付多少个各产品」。在「待处理」Tab 直接用 `orders`；在「已发货/全部」Tab 则从 `orders` 里 `filter(status==='pending')` 取待处理子集再聚合（故「全部」Tab 也会显示同一条待处理需求）。汇总条仅在存在待处理订单时出现。

3. **User action**: 对某行点击操作列的红色删除按钮（垃圾桶图标）。
   - **System response**: 弹出 Popconfirm「确定删除？」；确认后调用 `DELETE /api/orders/{id}`，成功后 `reload()` 刷新列表。
   - **User sees**: 该行从表格消失；待处理需求汇总条相应重算。
   - **Details**: 删除走级联（`Order` 删除联动删 `OrderItem`）。**删除不校验订单状态**：已发货订单同样可删，且**删除已发货订单不会回补已扣减的库存** —— 见边界。删除成功路径**无 success message**（与发货/创建不同）。

### Edge Cases & Error States

- **空队列**：待处理 Tab 无订单时，表格显示 Ant Design 默认空状态（「暂无数据」），且因无待处理订单**不显示**待处理需求汇总条。当前无定制空状态文案/引导（如「今天还没有订单，点右上角新增」）—— **体验缺口**。
- **删除已发货订单**：后端 `delete_order` 不看 `status`，已发货订单可被删除；其已扣减的组件库存**不回补**，造成库存与历史不一致。**已知风险**（design-orders-inventory.md §Open Questions #4）。
- **产品被目录移除后看历史/队列**：若订单引用的产品已从 catalog.yaml 删除，`getProdName` 回退为 `#{product_id}`，明细与汇总都只显示编号，不报错但可读性下降。
- **大量订单**：表格每页 20 条、客户端分页；个人作坊规模无虞，但无服务端分页/搜索/按日期筛选，历史累积后翻页是唯一定位手段。
- **删除请求失败**：`deleteOrder` 未包 try/catch，网络/服务端异常会抛到控制台、列表不刷新、无错误提示给用户 —— **已知缺口**（与 `shipOrder` 有 catch 提示不一致）。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 「待处理 / 已发货 / 全部」三个 Tab 可切换，分别对应 `status=pending` / `status=shipped` / 无过滤的订单集合。
- 订单表格按创建时间升序展示，列含订单号、创建时间（本地化）、状态标签、产品明细（产品名 xN 拼接）、操作。
- 状态标签：待处理为橙色「待处理」，已发货为绿色「已发货」。
- 存在待处理订单时，表格上方显示「待处理需求」汇总条：按产品聚合数量、降序、蓝色 Tag、末尾「共 M 个订单」。
- 每行操作列含删除按钮，点击经二次确认后调用删除接口并刷新列表，被删行消失。
- 队列为空时表格显示空状态且不显示待处理需求汇总条。

---

## CUJ-3：标记订单发货并自动扣减库存

**Dependencies**: CUJ-1
**Priority**: P0（库存扣减闭环的「出库」端；specs.md §6.2 的核心动作）

### Context

订单生产组装完成、寄出后，作坊主在系统里标记发货。此动作触发 specs.md §6.2 的自动扣减：系统按订单内每个产品的 BOM，折算出各「组件 + 颜色」的消耗量，从库存里扣掉。这是库存「减少」的唯一入口（库存「增加」来自排班任务完成入库，见 design-scheduler）。设计上要求**整单原子**：任一组件库存不足则整单失败、不部分扣减。

### Preconditions

- 目标订单存在且状态为 `pending`（仅待处理订单的操作列显示「发货」按钮）。
- 订单内每个产品在目录里有 BOM（`ProductComponent` 记录），且每个 BOM 项对应的「组件 + 颜色」在 `Inventory` 里有记录。
- 相关组件库存数量 ≥ 该订单的折算需求。

### Journey Steps

1. **User action**: 在「待处理」Tab 某行操作列点击蓝色「发货」按钮。
   - **System response**: 弹出 Popconfirm「确认发货？库存将自动扣减。」。
   - **User sees**: 该行操作列出现确认气泡，含「确认 / 取消」。
   - **Details**: 「发货」按钮仅对 `status === 'pending'` 的行渲染；已发货行不显示该按钮。

2. **User action**: 点确认。
   - **System response**: 调用 `POST /api/orders/{id}/ship`。后端遍历订单每个 `OrderItem` → 查该产品 BOM → 对每个 BOM 项按 `(component_id, color)` 定位库存行，校验 `inv.quantity >= bom.quantity * item.quantity`；**全部校验通过后**逐项扣减、置 `status='shipped'`、`shipped_at=datetime.now()`、commit。成功返回 `{ok:true}`，前端 `reload()` 并弹绿色 message「订单已发货，库存已扣减」。
   - **User sees**: 该订单从「待处理」Tab 消失（移至「已发货」），顶部绿色提示「订单已发货，库存已扣减」。
   - **Details**: 扣减键为 `(bom.component_id, bom.color)`，与库存/BOM 颜色口径一致。需求量 = `bom.quantity × item.quantity`（同一组件若被同订单多个产品/多明细引用，会被多次独立扣减、不预先合并 —— 逐项顺序扣）。

3. **User action**: 切到「已发货」Tab 查看结果（衔接 CUJ-4）。
   - **System response**: `GET /api/orders?status=shipped` 返回含该订单。
   - **User sees**: 该订单出现在已发货列表，状态标签绿色「已发货」。
   - **Details**: 当前 `shipped_at` 已落库，但**列表表格未单列展示发货时间**（仅有创建时间列）—— 见 CUJ-4 边界。

### Edge Cases & Error States

- **订单已发货**：对已是 `shipped` 的订单再次调用 ship（正常 UI 走不到，因按钮不显示）后端返回 `400「订单已发货」`，前端 `shipOrder` 的 catch 弹红色 message 显示该文案。
- **组件无库存记录**：某 BOM 项的 `(component_id, color)` 在 `Inventory` 无行 → 后端抛 `400「组件 {id}（{颜色或"无颜色"}）无库存记录」`，**整单失败、不扣减**，前端红色 message 显示。
- **库存不足**：某组件 `inv.quantity < needed` → 后端抛 `400「组件 {id}（{颜色}）库存不足（需要 X，当前 Y）」`，**整单失败、事务回滚（已在内存中扣的部分随 session 丢弃，不落库）**，前端红色 message 显示。订单仍为待处理。
- **错误信息可读性**：报错文案用的是**组件 id（数字）而非组件名**（如「组件 7（白色）库存不足」），用户需自行对照 —— **已知体验缺口**。
- **订单不存在**：`POST .../{id}/ship` 对不存在 id 返回 `404「订单不存在」`（UI 正常路径不触发）。
- **发货不可撤销**：无「退货/撤销发货」入口。误发货只能去库存页手动调增补回、并（若需要）手动改回状态 —— 当前**无改回 pending 的接口**，实际上误发货无法在 UI 内完全回退。**已知风险**（design-orders-inventory.md §Open Questions #2）。
- **并发/重复点击**：Popconfirm + 单请求；快速重复确认在第二次会命中「已发货」400。无前端按钮 loading 禁用，理论上极短窗口内可双发，但第二次被后端状态校验挡下。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 仅待处理订单的操作列显示蓝色「发货」按钮；点击经 Popconfirm「确认发货？库存将自动扣减。」二次确认。
- 确认后，若订单所有 BOM 折算需求均 ≤ 对应「组件+颜色」库存，则订单状态变为已发货、`shipped_at` 记录发货时刻、各相关组件库存按 `bom.quantity × item.quantity` 扣减，并弹「订单已发货，库存已扣减」。
- 发货后该订单从「待处理」Tab 移出、出现在「已发货」Tab。
- 任一 BOM 组件无库存记录或库存不足时，整单发货失败、库存不发生任何扣减、订单仍为待处理，并弹出含具体组件与缺口数量的 400 错误提示。
- 对已发货订单再次发货返回「订单已发货」并提示，不重复扣减。

---

## CUJ-4：查看已发货订单历史

**Dependencies**: CUJ-3
**Priority**: P1（盘点核对「今天发了哪些」与历史追溯）

### Context

作坊主在盘点或事后需要回看已发货订单，确认哪些订单已出库、避免漏发或重复处理。当前历史就是「已发货」Tab —— 与待处理共用同一张表格，仅过滤条件不同。这是一个轻量的只读历史视图（删除除外）。

### Preconditions

- 至少有一个 `shipped` 订单（由 CUJ-3 产生），否则该 Tab 为空。
- 用户在「订单管理」页。

### Journey Steps

1. **User action**: 点击 Tabs 的「已发货」。
   - **System response**: `tab` 切为 `shipped`，触发 `GET /api/orders?status=shipped`，后端按 `created_at` 升序返回所有已发货订单。
   - **User sees**: 表格列与待处理 Tab 完全相同（订单号 / 创建时间 / 状态 / 产品明细 / 操作），每行状态标签为绿色「已发货」。**因当前列表无待处理订单子集（除非「全部」Tab），通常不显示待处理需求汇总条**。操作列：已发货行**不显示「发货」按钮**，仅保留红色删除按钮。
   - **Details**: 「已发货」Tab 下 `pendingOrders` 取 `orders.filter(status==='pending')`，结果为空 → 汇总条隐藏。

2. **User action**: 阅读某已发货订单的明细。
   - **System response**: 明细列把 `items` 渲染为「产品名 xN, ...」。
   - **User sees**: 形如「龙猫摆件 x3, 皮卡丘 x1」。
   - **Details**: **当前表格不单列展示 `shipped_at`（发货时间）**，尽管该字段已落库 —— 列表只有「创建时间」列。用户在 UI 内看不到具体发货时刻。**已知缺口**（与 specs.md §8.3「已发货订单历史」的隐含期望存在落差）。

3. **User action**（可选）: 点「全部」Tab 一次性看待处理+已发货。
   - **System response**: `GET /api/orders`（无过滤）返回全部订单，升序混排。
   - **User sees**: 同一表格混合两种状态行；因含待处理子集，**会重新显示**「待处理需求」汇总条。
   - **Details**: 全部 Tab 是查看全量历史与当前积压的合并视图；无按状态分组、无按日期分组。

### Edge Cases & Error States

- **空历史**：尚无已发货订单时「已发货」Tab 表格显示默认「暂无数据」空状态，无定制文案。
- **看不到发货时间**：`shipped_at` 已存但未在列表呈现，无法在 UI 内区分「今天发的」与「上周发的」（除非按创建时间近似推断）—— **体验缺口**，可作为后续迭代（加发货时间列 / 按发货日分组）。
- **历史中删除**：已发货行的删除按钮可用，删除已发货订单会**永久移除历史且不回补库存**（见 CUJ-2 / CUJ-3 风险）。当前无「历史只读、禁止删除」保护。
- **历史规模**：无日期范围筛选/搜索，长期累积后只能靠 20 条分页翻页定位；个人作坊短期无碍，长期是可用性隐患。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters)。

### Acceptance Criteria

- 「已发货」Tab 仅展示 `status==='shipped'` 的订单，状态标签为绿色「已发货」。
- 已发货行操作列不显示「发货」按钮，保留删除按钮。
- 「已发货」Tab 下不显示「待处理需求」汇总条（因无待处理子集）。
- 「全部」Tab 展示待处理与已发货混合的全部订单（升序），并因含待处理子集而显示待处理需求汇总条。
- 已发货订单的明细以「产品名 xN」形式正确展示（产品已被目录移除时回退为 `#{product_id}`）。

---

## 与 specs.md 用户意图的差异备注（backfill 标注）

下列为「specs.md / 设计意图」与「代码现状」的差异，本 PRD 以代码现状为准并在此集中标注，供后续迭代取舍：

1. **软 FIFO 仅部分落地**：specs.md「订单优先级」要求大致按创建时间排序、库存够则跳过/直接发货。**订单页只做了「按 `created_at` 升序展示」**；「跳过已满足订单、优先后续缺料订单」属排班算法（PRD-排班），订单页不展示任何优先级/跳过状态，也不在录单或队列页体现「库存够可直接发货」的提示。
2. **发货不可撤销 / 退货缺失**：specs.md 未要求退货，但工作流上「误标发货」无 UI 回退路径（无撤销发货接口）。
3. **删除不保护已发货订单、不回补库存**：`DELETE` 不校验状态、不回补库存，与「已发货历史应是可信账」的隐含期望冲突。
4. **发货时间不展示**：`shipped_at` 已落库但列表无对应列，§8.3「已发货订单历史」的核对价值被削弱。
5. **错误文案用组件 id 而非名称**：发货失败提示对用户不够友好。
6. **批量录单非原子**：前端「一次创建 N 个订单」是 N 个独立请求，中途失败会产生部分落库且无清晰反馈。
