# 订单与库存（Orders & Inventory）

> Last updated: 2026-06-18 18:54:18 (UTC+8)
> Serves: 订单管理（prd-001）、组件库存管理（prd-002）、富余情况展示（specs.md 第 1、6 节，§8.3、§8.4）
>
> 业务规格见 [docs/specs.md](../specs.md)。库存与排班的入库闭环另见 [design-scheduler.md](design-scheduler.md)。
> **自动导入（prd-006）写入 `Order` 的链路** — Chrome 扩展抓 DOM / ADB 截屏 / LLM SKU 匹配 / 批次预览 / 单事务 commit — 见 [design-auto-import.md](design-auto-import.md)。本文档维护 `Order` 表 schema 的完整定义（含 prd-006 新增 4 列），auto-import 是其写源之一。

## Overview

本组件管理订单队列与组件库存（按「组件+颜色」维度），并通过 BOM 折算实现：
- **发货扣减**：订单标记已发货时，按 BOM 自动扣减各组件库存。
- **富余计算**：库存相对于待处理订单需求的盈亏（折算到组件维度）。

库存的**增加**由排班执行控制完成（任务 complete 时入库，见 `design-scheduler.md`），本组件负责**扣减、手动调整、查询、富余展示**。

实现文件：
- `backend/app/routers/orders.py` — 订单 CRUD + 发货扣减。
- `backend/app/routers/inventory.py` — 库存查询/调整/设置 + 富余计算。

## Goals & Non-Goals

**Goals**
- 软 FIFO 订单队列（按 `created_at`）。
- 发货时按 BOM 原子扣减，库存不足则整单失败（事务回滚）。
- 库存以「组件+颜色」为最小粒度。

**Non-Goals**
- 不做订单与具体产出批次的追溯绑定。
- 不做库存流水/审计日志（仅当前数量）。
- 不在本组件做排班（仅提供需求/库存数据）。

## System Context

```mermaid
flowchart LR
    FE_O["前端 Orders.tsx"] --> RO["orders.py"]
    FE_I["前端 Inventory.tsx / Dashboard.tsx"] --> RI["inventory.py"]
    AutoImport["自动导入<br/>(design-auto-import)<br/>POST /auto-import/commit"] -->|单事务批量创建| Ord[("Order / OrderItem")]
    RO --> Ord
    RO -->|发货扣减(按 BOM)| Inv[("Inventory")]
    RI --> Inv
    RI -->|读 BOM 算需求| Bom[("ProductComponent")]
    RI -->|读订单算需求| Ord
    Scheduler["排班完成入库<br/>(design-scheduler)"] -->|+quantity| Inv
```

## Detailed Design

### 数据模型（详见 `system.md` §4）

#### `Order` 表（含 prd-006 扩展）

| 字段 | 类型 | Nullable | 说明 |
|---|---|---|---|
| `id` | INTEGER PK | — | |
| `created_at` | DATETIME | ❌ | infill 接收时间（人工录入是点提交时；自动导入是 commit 落库时） |
| `status` | VARCHAR | ❌ | `pending` / `shipped` |
| `shipped_at` | DATETIME | ✅ | 发货时间 |
| `notes` | TEXT | ✅ | 备注（人工录入可填） |
| `platform` | VARCHAR(16) | ✅ | **prd-006 新增**：`xhs` / `xianyu` / NULL（NULL = 人工录入） |
| `external_order_id` | VARCHAR(64) | ✅ | **prd-006 新增**：平台原始订单号；override 时追加 `-redoN` 后缀 |
| `buyer_nickname` | VARCHAR(128) | ✅ | **prd-006 新增**：平台买家昵称 |
| `external_created_at` | DATETIME | ✅ | **prd-006 新增**：平台侧下单时间（LLM 未识别时落 `now()` fallback） |

**新增唯一约束**（**prd-006 引入**，partial unique index）：

```sql
CREATE UNIQUE INDEX uq_orders_platform_external
  ON orders(platform, external_order_id)
  WHERE platform IS NOT NULL AND external_order_id IS NOT NULL;
```

- 仅对自动导入订单（两字段都非 NULL）去重；人工录入订单（两字段都 NULL）不受约束、可任意创建多条。
- 由 `services/catalog.py::ensure_order_auto_import_schema_exists` 在启动期幂等补建（`auto_migrate` 不覆盖 partial index）。

**`-redoN` 后缀 override 约定**：用户在 prd-006 CUJ-3 把重复订单点「改判为新单」时，后端在写入前给原 `external_order_id` 追加 `-redoN` 后缀（首次 `-redo1`、二次 `-redo2`，按 `SELECT external_order_id LIKE 'original-redo%'` 取最大数字 +1）。详见 [design-auto-import.md §3.4](design-auto-import.md)。

#### 其他表

- `OrderItem(order_id, product_id, quantity)` —— 订单明细。
- `Inventory(component_id, color, quantity)` —— 唯一性逻辑上是 `(component_id, color)`，由目录加载保证每组合一条（**注意：DB 层无 unique 约束，见风险**）。
- `ProductComponent(product_id, component_id, color, quantity)` —— BOM，折算依据。

### API / Interface Contract

#### 订单（`/api/orders`）

| 方法 + 路径 | 请求/响应 | 说明 |
|---|---|---|
| `GET /api/orders?status=` | `list[OrderOut]` | 按 `created_at` 升序；可按状态过滤 |
| `POST /api/orders` | `OrderCreate` → `OrderOut` | 新建订单（含多 item），人工录入路径；`platform / external_*` 字段均留 NULL |
| `PUT /api/orders/{id}` | `OrderUpdate` → `OrderOut` | 编辑订单（items / notes） |
| `GET /api/orders/{id}` | `OrderOut` | 取单 |
| `POST /api/orders/{id}/ship` | — | 标记发货 + 扣库存（见下） |
| `DELETE /api/orders/{id}` | — | 删除订单（级联删 item） |

> **自动导入路径**：prd-006 通过 `POST /api/auto-import/commit` 创建 `Order(platform=..., external_order_id=..., ...)` —— 走单事务批量写。详见 [design-auto-import.md §2](design-auto-import.md)。本组件**不**接收 auto-import 流量，但创建的 `Order` 行立即可被本组件的 `GET /api/orders?status=pending` 查询到。

#### 库存（`/api/inventory`）

| 方法 + 路径 | 请求/响应 | 说明 |
|---|---|---|
| `GET /api/inventory` | `list[InventoryOut]` | 全部库存行 |
| `POST /api/inventory/adjust` | `InventoryAdjust` → `InventoryOut` | 增量调整（正加负减，下限 0） |
| `PUT /api/inventory/{id}` | `InventoryAdjust` → `InventoryOut` | 直接设置数量（`max(0, qty)`） |
| `GET /api/inventory/surplus` | `list[{component_id,component_name,color,stock,demand,surplus}]` | 富余计算 |

### Logic & Behavior

#### 发货扣减（`ship_order`）

```mermaid
flowchart TB
    S["POST /orders/{id}/ship"] --> C0{"订单存在?"}
    C0 -->|否| E404["404"]
    C0 -->|是| C1{"已发货?"}
    C1 -->|是| E400["400 订单已发货"]
    C1 -->|否| Loop["遍历每个 OrderItem"]
    Loop --> BOM["查产品 BOM"]
    BOM --> Each["对每个 (component_id, color)"]
    Each --> Ck{"有库存记录?"}
    Ck -->|否| Eno["400 无库存记录"]
    Ck -->|是| Cq{"库存 ≥ bom.qty × item.qty?"}
    Cq -->|否| Eqty["400 库存不足"]
    Cq -->|是| Sub["inv.quantity -= needed"]
    Sub --> Done["全部通过 → status=shipped, shipped_at=now, commit"]
```

- 任一组件库存不足则**整单失败**（抛 HTTPException，事务未 commit，已扣的内存改动随 session 丢弃）。
- 扣减键是 `(bom.component_id, bom.color)`，与库存/BOM 颜色口径一致。

#### 富余计算（`get_surplus_info`）

1. 累加所有 `pending` 订单的 BOM 需求 → `component_demand[(comp_id,color)]`。
2. 读全部库存 → `inventory_map[(comp_id,color)]`。
3. 对需求键 ∪ 库存键的并集，输出 `{stock, demand, surplus = stock - demand}`。

> 该口径**仅看待处理订单需求，不含已排班产出**（与 `scheduler._get_initial_supply` 的「库存+已排班产出」不同）。前端 `Schedule.tsx` 的「排班总结」会另行叠加 earlier plans 产出做更完整的展示。

#### 手动调整两种语义

- `adjust`（增量）：`inv.quantity += data.quantity`，结果 < 0 归 0。按 `(component_id, color)` 定位。
- `set`（绝对）：`inv.quantity = max(0, data.quantity)`，按 `inventory_id` 定位（前端 Inventory.tsx 编辑用）。

## Data Flow

```mermaid
sequenceDiagram
    participant U as 用户
    participant FE as Orders.tsx
    participant R as orders.py
    participant DB as SQLite

    U->>FE: 新建订单(产品+数量)
    FE->>R: POST /orders
    R->>DB: 建 Order + OrderItems
    Note over U,DB: ...生产/排班完成入库（见 design-scheduler）...
    U->>FE: 标记发货
    FE->>R: POST /orders/{id}/ship
    R->>DB: 按 BOM 校验并扣减 Inventory
    alt 任一组件不足
        R-->>FE: 400（整单失败，不扣减）
    else 全部充足
        R->>DB: status=shipped, 扣减 commit
        R-->>FE: {ok:true}
    end
```

## Alternatives Considered

| 准则 | 发货即按 BOM 扣组件库存（现状） | 维护成品库存，发货扣成品 |
|---|---|---|
| 与生产模型一致性 | 高（生产/库存都是组件维度） | 低（需额外组装入库步骤） |
| 共享组件处理 | 自然（按组件扣） | 复杂 |
| 适合作坊 | 是（产品即时组装发货） | 过度建模 |
| **裁决** | **选用** | |

## Cross-Cutting Concerns

- **错误处理**：404/400 + 整单事务回滚（库存不足）。
- **一致性**：发货扣减在单请求事务内；失败不部分扣减。
- **安全**：无鉴权（`system.md` §5.3）。
- **性能**：每订单逐 item 查 BOM、逐组件查库存（N 次查询）。规模小无碍；潜在 N+1（同 `design-scheduler.md` 备注）。
- **测试**：当前无订单/库存路由的自动化测试。

## Dependencies & Integration Points

- **依赖**：目录表（`Product`/`ProductComponent`/`Component`，由 `design-catalog.md` 维护）。
- **与排班闭环**：库存增加来自排班任务 complete（`design-scheduler.md` 的 `complete_task`）；库存减少来自本组件发货扣减。两者共同维护 `Inventory`。
- **被依赖**：排班的需求计算读 `Order`，富余/Dashboard 读 surplus。
- **`Order` 写源**：本组件（人工录入 `POST /api/orders`）+ **自动导入 `POST /api/auto-import/commit`**（详见 [design-auto-import.md](design-auto-import.md)）。两者写入的 `Order` 行通过 `platform` 字段区分（NULL = 人工 / `xhs` / `xianyu` = 自动），但后续生命周期（待处理队列 / 发货 / 历史）完全统一在本组件。

## Open Questions & Risks

1. **`Inventory` 无 DB 级唯一约束**：`(component_id, color)` 的唯一性仅靠 `load_catalog` 的「不存在才建」逻辑保证，并发或异常路径下理论上可能重复行（单用户场景风险低）。
2. **发货后不可撤销**：无「退货/撤销发货」入口，误标发货只能手动调库存补回。
3. **富余口径不含已排班产出**：`/inventory/surplus` 与排班初始供给口径不同，用户可能在不同页面看到不一致的「富余/缺口」数字（Dashboard/Inventory 用前者，Schedule 总结用叠加后者）。
4. **删除订单无状态校验**：`DELETE /orders/{id}` 对已发货订单也可删除，删除已发货订单不会回补库存。
