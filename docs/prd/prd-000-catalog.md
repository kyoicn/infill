---
id: prd-000
title: 产品目录
status: active
created: 2026-06-13
deprecation_reason:
---

# PRD-000：产品目录

> 本 PRD 由现有实现反向补写（backfill），描述「当前产品里实际发生的行为」，不是重新设计。
> 与 [docs/specs.md](../specs.md) §1/§3/§8.2 的用户意图冲突时，以代码现状为准并在文中标注。
> 实现链路与差量同步语义见 [docs/design/design-catalog.md](../design/design-catalog.md)。

## 功能概述

产品目录（**组件 / 打印盘 / 产品 BOM**）的唯一数据源是 YAML 文件（`data/catalog.yaml`，可由 `CATALOG_PATH` 环境变量覆盖；Docker 内指向 `/app/data/catalog.yaml`）。网页对目录**只读**：用户在「产品目录」页查看三张表，并通过编辑 YAML 文件 + 点击「重新加载目录」按钮把修改同步进数据库后生效。

数据库中的 `Component`/`PrintConfig`/`Product`/`ProductComponent` 只是 YAML 的运行时镜像。`load_catalog(db)` 负责把 YAML **差量同步**进 DB（按名称/盘号匹配的 upsert + 安全删除），并联动维护 `Inventory` 颜色记录。`load_catalog` 有三个触发点：① 应用启动 `lifespan`；② `POST /api/catalog/reload`（本 PRD 的 CUJ-2）；③ `POST /api/config/reset-db` 重建库后重新加载（属于 PRD-系统设置，不在本 PRD 范围）。

本 PRD 范围：
- CUJ-1：浏览只读产品目录（产品 / 组件 / 打印盘三张表）
- CUJ-2：编辑 catalog.yaml 后重新加载使修改生效

不在本 PRD 范围：网页端目录的增删改（不做 CRUD UI）、YAML schema 校验/版本管理、目录变更历史/审计、`reset-db` 链路。

## 数据来源与字段映射

YAML 用中文键，与运行时 `catalog.yaml` 一致。字段映射（权威表见 design-catalog.md §字段映射）：

| YAML | DB / 接口字段 | 备注 |
|---|---|---|
| 组件.名称 | `Component.name` | 匹配键（按 name 查找/去重） |
| 组件.描述 | `Component.description` | 缺省 `""` |
| 组件.可选颜色 | `Component.colors`（JSON list） | 缺省 `[]`；驱动库存记录的颜色集合 |
| 打印盘.盘号 | `PrintConfig.plate_name` | 匹配键 |
| 打印盘.组件 | `PrintConfig.component_id` | 按组件名解析为 id，组件须先存在 |
| 打印盘.数量 | `PrintConfig.quantity` | 每盘产出 |
| 打印盘.耗时分钟 | `PrintConfig.duration_minutes` | |
| 产品.名称 | `Product.name` | 匹配键 |
| 产品.描述 | `Product.description` | 缺省 `""` |
| 产品.BOM[].组件/颜色/数量 | `ProductComponent.{component_id,color,quantity}` | 颜色缺省 `""`；每次加载全量重建 |

---

## CUJ-1：浏览只读产品目录

**Dependencies**: 无
**Priority**: P0（启动阻断 — 目录是订单/库存/排班的前置数据，且是用户核对 YAML 是否正确加载的唯一窗口）

### Context

作坊主在 YAML 里维护目录后，需要一个地方确认「我编辑的组件、打印盘、产品 BOM 是否被系统正确识别」。这页是只读视图，不提供编辑，目的就是让用户一眼核对当前 DB 中的目录镜像。它也是录单（需要看产品）、排库存（需要看组件/颜色）、看排班（需要看盘号）前的参照页。

### Preconditions

- 后端已启动；启动时 `lifespan` 已执行过一次 `load_catalog`，DB 中已有目录镜像（除非启动期加载失败，见边界）。
- 用户已在浏览器打开前端应用（前端为单页应用，左侧固定深色侧边栏 `Sider`，宽度随 `breakpoint="lg"` 折叠到 60px）。
- `catalog.yaml` 至少在启动时存在且可解析（否则启动期加载抛异常，DB 可能为空）。

### Journey Steps

1. **User action**: 点击左侧导航菜单中的「产品目录」项（图标 `AppstoreOutlined`，第二项，位于「仪表盘」下方）。
   - **System response**: 路由切换到 `/products`，渲染 `Products.tsx`。组件 `useEffect` 在挂载时触发 `reload()`，并发请求 `GET /api/components`、`GET /api/products`、`GET /api/components/configs/all` 三个接口。
   - **User sees**: 左侧菜单「产品目录」项高亮（`selectedKeys` 命中 `/products`）。右侧内容区（`margin: 24`）顶部一行：左侧标题「产品目录」（`<h2>`，`margin:0`），右侧一个带刷新图标（`ReloadOutlined`）的按钮「重新加载目录」（默认态非 loading）。标题下方一行灰色提示文字（`color:#999`）：`数据源：catalog.yaml — 修改文件后点击"重新加载目录"生效`。
   - **Details**: 三个 GET 请求并发发出，无统一 loading 占位（见边界：加载中空态）。页面无分页（三张表均 `pagination={false}`），表格密度为 `size="small"`。

2. **User action**: 等待数据返回（局域网/本地，通常 < 200ms）。
   - **System response**: 三个 `setState` 分别落地 `products`、`components`、`allConfigs`，三张 Ant Design `Card` + `Table` 渲染填充。
   - **User sees**: 自上而下三张卡片，间距 `marginBottom: 24`：
     - **卡片①「产品列表」**：表格列为 `名称` | `描述` | `BOM`。`BOM` 列把每条 BOM 项渲染成 `组件名(颜色) x数量` 用 `, ` 拼接的一行文本；颜色为空时不显示括号部分。组件名通过 `getCompName(component_id)` 在已加载的 `components` 里反查，查不到时回退显示 `#<id>`。
     - **卡片②「组件列表」**：表格列为 `名称` | `描述` | `可选颜色` | `打印盘`。`可选颜色` 列把 `colors` 数组用顿号「、」拼接；数组为空时显示灰色「无」。`打印盘` 列在 `allConfigs` 里过滤出 `component_id === 该组件id` 的盘，渲染成 `盘号(x数量)` 用 `, ` 拼接；无盘时显示灰色「无」。
     - **卡片③「打印盘」**：表格列为 `盘号`(宽120) | `组件` | `数量`(宽80) | `耗时(分钟)`(宽110)。`组件` 列用 `getCompName` 把 `component_id` 反查成组件名。
   - **Details**: 三张表均不分页、不排序、不可编辑，顺序即接口返回顺序（`db.query(...).all()`，即按主键 id 递增）。行数据量预期极小（specs §9：产品 <10、组件 20~30、打印盘数十）。

3. **User action**: 滚动页面浏览全部三张表，核对 YAML 编辑结果。
   - **System response**: 无额外请求，纯前端滚动。
   - **User sees**: 内容超出视口时整页滚动（表格本身无独立滚动容器/虚拟化）。
   - **Details**: 用户离开本页前无任何写操作 — 这是纯只读 CUJ。

### Edge Cases & Error States

- **空目录（YAML 为空 / 启动期加载失败导致 DB 空）**：三张接口返回空数组，三张卡片各显示 Ant Design `Table` 默认空态（居中「暂无数据 / No Data」图标占位）。当前**未定制中文空态文案**，也无「去编辑 catalog.yaml」之类引导（与 design-catalog 的「只读」定位一致，但对首次用户不友好）。
- **加载中空态**：三个 GET 并发期间页面无骨架屏 / 无 loading 占位，表格先以空态渲染再被数据替换，慢网下会有短暂「先空后有」闪烁。`loading` 状态只绑定在「重新加载目录」按钮上，不覆盖三张表。
- **单个 GET 失败（如后端 500 / 网络断）**：`reload()` 内三个 `.then()` 无 `.catch()`，失败的那一个 Promise rejection 不会被捕获也不会 `message` 提示；对应那张表保持空态/旧值，**页面不报错也不提示**（已知缺陷，记入 Open questions）。
- **组件名反查失败**：BOM 或打印盘引用的 `component_id` 不在已加载 `components` 里（理论上不应发生，因 DB 有外键关系；若 `components` 接口失败而另两者成功则可能出现），`getCompName` 回退显示 `#<id>`，用户看到的是裸 id 而非名称。
- **超长名称/描述**：无截断/省略号处理，长文本会撑高行 / 换行，依赖 Ant Design 表格默认换行行为。
- **无颜色组件**：`colors` 为 `[]`，「可选颜色」列显示灰色「无」；其在库存侧以 `color=""` 占位一条记录（由 `load_catalog` 维护，不在本页展示）。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters).

### Acceptance Criteria

- 左侧菜单存在「产品目录」项，点击后 URL 为 `/products` 且该菜单项高亮。
- 页面顶部显示标题「产品目录」与按钮「重新加载目录」（带刷新图标），下方显示灰色提示「数据源：catalog.yaml — 修改文件后点击"重新加载目录"生效」。
- 页面自上而下显示三张卡片，标题依次为「产品列表」「组件列表」「打印盘」。
- 「产品列表」每行显示名称、描述，以及把 BOM 渲染成 `组件名(颜色) x数量` 逗号拼接的一列；BOM 项颜色为空时不出现括号。
- 「组件列表」每行显示名称、描述、可选颜色（顿号拼接，空时显示「无」）、关联打印盘（`盘号(x数量)` 逗号拼接，空时显示「无」）。
- 「打印盘」每行显示盘号、组件名（由 id 反查）、数量、耗时（分钟）。
- 打印盘表中「组件」列与产品 BOM 中显示的是组件**名称**而非数字 id（仅当反查失败才回退为 `#<id>`）。
- 三张表均不分页、不可编辑（页面无任何新增/编辑/删除目录项的控件）。
- 进入页面时浏览器实际向 `/api/components`、`/api/products`、`/api/components/configs/all` 各发出一次 GET。

---

## CUJ-2：编辑 catalog.yaml 后重新加载使修改生效

**Dependencies**: CUJ-1
**Priority**: P0（这是用户维护目录的唯一入口；不可用则整个目录无法更新）

### Context

网页不提供目录编辑，用户维护目录的方式是：直接用文本编辑器改 `catalog.yaml`（增删组件/打印盘/产品、改数量/耗时/BOM/颜色），然后回到「产品目录」页点「重新加载目录」，把 YAML 差量同步进 DB 并刷新页面展示。这是「文本编辑 + 一键 reload」工作流的兑现点，替代了一整套 CRUD UI（取舍理由见 design-catalog「Alternatives Considered」）。

### Preconditions

- 后端运行中，`catalog.yaml`（或 `CATALOG_PATH` 指向的文件）存在且用户对其有写权限（本地运行 / Docker 卷挂载，宿主可直接编辑 — 即设计意图）。
- 用户已在「产品目录」页（CUJ-1），能看到「重新加载目录」按钮。
- 用户已在文件系统层面完成对 YAML 的编辑并保存。

### Journey Steps

1. **User action**: 在系统的文本编辑器中打开 `catalog.yaml`，修改内容（如给「组件A」新增一种「可选颜色: 蓝色」，或新增一个打印盘、改某盘「耗时分钟」、新增/删除一个产品或调整其 BOM），保存文件。
   - **System response**: 无（纯文件系统操作，后端此刻不感知；DB 尚未变化）。
   - **User sees**: 编辑器内文件已保存。回到浏览器「产品目录」页时，三张表仍显示**改动前**的旧值（因为还没 reload）。
   - **Details**: YAML 用中文键（组件/打印盘/产品/名称/描述/可选颜色/盘号/组件/数量/耗时分钟/BOM/颜色 等），格式见 `data/catalog.yaml.example`。匹配键是「名称/盘号」而非 id：改名等同「删旧建新」。

2. **User action**: 点击右上角「重新加载目录」按钮。
   - **System response**: 按钮进入 loading 态（`loading={loading}`，显示转圈），前端 `POST /api/catalog/reload`（无请求体）。后端用独立 `SessionLocal()` 执行 `load_catalog(db)`：按固定顺序差量同步 组件 → 打印盘 → 产品/BOM，并联动维护库存颜色记录，最后 `commit`，返回 `{"ok": true, "stats": {"组件": n, "打印盘": m, "产品": k}}`。
   - **User sees**: 「重新加载目录」按钮显示加载转圈，期间不可重复点击。
   - **Details**: `reload` 端点**不抛 HTTPException**，业务异常被 `try/except` 捕获为 `{"ok": false, "error": "<异常字符串>"}` 并以 HTTP 200 返回。

3. **User action**: 等待请求返回（数据规模极小，通常很快；但 `load_catalog` 内有较多逐条按名查询，规模放大时会变慢）。
   - **System response（成功）**: `res.ok === true` 时弹出绿色成功提示，并再次调用 `reload()`（CUJ-1 的三连 GET）刷新三张表。按钮退出 loading 态。
   - **User sees（成功）**: 顶部居中弹出 Ant Design `message.success`，文案为 `目录已重新加载：{n} 个组件，{m} 个打印盘，{k} 个产品`（数字来自后端 `stats`），约 3 秒后自动消失。随后三张表刷新为 YAML 的最新内容（新增颜色出现在「可选颜色」列、新增盘出现在「打印盘」表与对应组件的「打印盘」列、BOM 改动反映在「产品列表」等）。
   - **Details**: 成功后才刷新展示；刷新是重新拉接口而非局部更新，因此三张表整体替换。

4. **User action**:（失败分支）若 YAML 有错误（如打印盘/BOM 引用了不存在的组件），仍点了「重新加载目录」。
   - **System response**: `load_catalog` 抛 `ValueError`（如 `打印盘 'X' 引用了不存在的组件 'Y'`），端点捕获返回 `{"ok": false, "error": "<该消息>"}`。前端 `res.ok === false` 分支弹出红色错误提示，**不刷新表格**（保持旧值），按钮退出 loading。
   - **User sees**: 顶部弹出 `message.error`，文案为 `加载失败：<error 字符串>`（如 `加载失败：打印盘 '5号盘' 引用了不存在的组件 '组件D'`）。三张表保持改动前的旧值。
   - **Details**: 由于 `load_catalog` 在抛错前可能已对 DB 做了部分 `flush`（如组件已同步、打印盘阶段才报错），但**未 `commit`**；该请求用的是独立 session 且未提交，进程级影响有限，但事务是否完整回滚未显式处理（记入 Open questions）。

### Edge Cases & Error States

- **YAML 缺必填字段**（如打印盘缺「数量」/「耗时分钟」，或组件项缺「名称」）：`load_catalog` 直接 `KeyError`，被端点 `except Exception` 捕获，前端显示 `加载失败：'<缺失键>'`（裸键名，对用户不友好 — design-catalog Open Questions §4）。
- **YAML 语法错误**（缩进/冒号错）：`yaml.safe_load` 抛 `yaml.YAMLError`，同样落入 `{ok:false}`，前端 `加载失败：<解析器报错>`（信息冗长、定位靠用户自己看）。
- **文件不存在 / `CATALOG_PATH` 指错**：`open()` 抛 `FileNotFoundError`，前端 `加载失败：[Errno 2] No such file or directory: '<path>'`。
- **引用不存在组件**（打印盘或 BOM）：`ValueError`，中文友好提示（如上文 step 4）。
- **改名导致重复记录**：组件/产品按名称匹配，改名 = 删旧建新。若被改名组件库存非 0，旧记录不会被「删 YAML 中不存在的组件」误删（因有库存约束/级联，且安全删除仅删 qty=0 库存），可能出现「旧名 + 新名」两条组件记录并存（design-catalog Open Questions §1）。本页会把两条都展示出来。
- **删除颜色但库存非 0**：`load_catalog` 仅删除 `quantity==0` 的库存颜色记录；非 0 的颜色记录被保留以防误删库存，因此该颜色可能仍存在于库存侧但已从组件「可选颜色」列消失，两侧口径暂时不一致（设计上的安全删除取舍）。
- **网络/后端不可达**：`request()` 抛错被 `catch (e)` 捕获，弹 `message.error(e.message)`（如 `请求失败: 500` 或 fetch 网络错误信息）。
- **重复快速点击**：loading 期间按钮不可再次点击，避免并发 reload。
- **启动期加载失败**（非本按钮触发，但相关）：`lifespan` 内 `load_catalog` 抛异常会向上传播，导致应用启动失败/目录为空；此时进入本页是空目录态（CUJ-1 空态），用户需修好 YAML 后重启或（若服务仍在）点 reload 修复。

### Mocks / Reference Designs

No mocks (backfilled from existing impl — run /design-feature Route D to add mocks if visual fidelity matters).

### Acceptance Criteria

- 「产品目录」页右上角的「重新加载目录」按钮点击后会向 `POST /api/catalog/reload` 发出一次请求，按钮在请求期间显示 loading 转圈且不可重复触发。
- 重新加载成功时，顶部弹出绿色提示，文案形如「目录已重新加载：N 个组件，M 个打印盘，K 个产品」，其中数字与后端返回的 `stats` 一致。
- 重新加载成功后，三张表自动刷新为 `catalog.yaml` 的最新内容（无需手动刷新浏览器）：例如 YAML 中新增的颜色出现在对应组件的「可选颜色」列、新增的打印盘出现在「打印盘」表中。
- YAML 中删除某项后重新加载，对应项从相应表中消失（组件/打印盘/产品的删除均生效，受安全删除规则约束的库存联动除外）。
- 重新加载失败时（如 YAML 引用不存在的组件），顶部弹出红色提示，文案形如「加载失败：<错误信息>」，且三张表保持改动前的旧值不被清空。
- 重新加载是幂等的：同一份未改动的 YAML 连续点两次「重新加载目录」，目录展示结果不变。
- 修改 YAML 但**未**点击「重新加载目录」时，页面展示仍为旧值（修改不会自动生效）。

---

## Open Questions / 已知缺陷（来自代码现状，待产品决策）

1. **CUJ-1 的 GET 无错误处理**：`reload()` 内三个 `.then()` 无 `.catch()`，单个接口失败时页面静默空态、无任何提示。建议补统一错误态/重试。（design-catalog 未列，本次补写发现）
2. **失败时事务回滚不显式**：`reload` 端点对捕获的异常未对独立 session 显式 `rollback`，仅靠 `finally: db.close()` 与「未 commit」兜底。规模小无碍，但语义上不够干净。
3. **错误信息对用户不友好**：`KeyError`/`YAMLError`/`FileNotFoundError` 直接把裸异常字符串透出到 `message.error`，缺字段/语法错时定位困难（design-catalog Open Questions §4）。
4. **空目录/首次用户无引导**：空态用 Ant Design 默认「暂无数据」，未引导用户去创建/编辑 `catalog.yaml`。
5. **改名 = 删旧建新**：按名称匹配缺少稳定 id 映射，改名可能产生重复记录或库存/订单恢复丢失（design-catalog Open Questions §1/§2）。
6. **颜色为自由字符串**：BOM 颜色与「可选颜色」、库存口径必须人工保持一致，易因错别字产生「幽灵需求键」（design-catalog Open Questions §3）。
7. **无 `load_catalog` 单元测试**：差量删除/库存联动分支无测试覆盖（design-catalog Open Questions §5）。
