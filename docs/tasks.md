# Task Plan

Last updated: 2026-06-13 04:17:29 (UTC+8)

## Current State

本轮迭代**专注于排班算法修复**，不做任何新功能、不涉及其他 PRD。权威规格是 `docs/design/design-scheduler.md` 的「§目标设计（本轮迭代）」T0–T5，对应已批准的审计结论 方案 A/B/C/F。

修复目标（来自现状审计，均已在 design-scheduler.md 中裁决）：
- **同步惩罚三份拷贝**：`scheduler.py:273-278`（乘法）、`scheduler_core.py:192-194`（加法，在 `pick_task` 内）、`scheduler_core.py:394-398`（加法，在 `schedule_tasks._pick` 内）—— 统一为单一纯函数 `_sync_penalty()`。
- **贪心主循环留在 DB 层**：`product_first` / `utilization` 的分批主循环仍写在 `scheduler.py.generate_plan` 内（`_next_task` 辅助函数 666-701 行、`while True` 主循环 703-773 行），调用 `scheduler.py._pick_task`（211-286 行，乘法惩罚），未委托 core —— 下沉为 core 纯函数 `schedule_greedy()`，删除 `scheduler.py._pick_task` 与该段主循环。
- **two_phase partial 分支产出孤儿件 + overflow 口径不一致**：`scheduler_core.py:301-318` —— 改为「凑整放弃」（complete-or-skip）。
- **`SURPLUS_TARGET_PRODUCTS` 三处口径不一**：`scheduler_core.py:21`=20（core 内无引用）、`scheduler.py:40`=20、`schedule_specs.md §7.3`=5 —— 收敛到 core 单一定义=20，scheduler.py import，specs 改 20。
- **specs 与代码漂移**：`schedule_specs.md §9.3` 仍描述旧乘法机制 —— 用加法公式重写（依赖 A/B 落地，故置于 Group 2）。

**全程范围红线（适用所有任务，逐字来自 design-scheduler.md §T5；worker 必须遵守）：**
- ❌ 不引入贪心路径的动态批次启动（保持「首批 `custom_start`，后续批 `find_next_start(最早可用)`」）。
- ❌ 不下沉 `_build_surplus_tasks`（只要求其输出 `surplus_tasks` 作为 plain-data 传入新 core 主循环）。
- ❌ 不动 `routers`/前端中内联的 `changeover=15`（属 SystemConfig 初始化议题，不在本轮）。
- ❌ 不改 DB schema（`PrintPlan/PrintBatch/PrintTask` 字段）、不改 HTTP 契约（`routers/schedule.py` 所有端点请求/响应不变）、不改前端任何代码。
- ❌ 不改富余产出规模（`SURPLUS_TARGET_PRODUCTS` 维持实际值 20）。

**测试命令（所有任务统一）：** `cd backend && python -m pytest tests/ -v`

---

## Parallel Group 1

### Task: Task-A（P0）统一同步惩罚 + 贪心主循环下沉

- **Do**:
  1. **抽取单一同步惩罚纯函数** `_sync_penalty()` 到 `backend/app/services/scheduler_core.py`（顶部常量区下方，`pick_task` 之前）。签名与公式（逐字照 design-scheduler.md T1·A.1）：
     ```python
     SYNC_PENALTY_CHANGEOVER_MULT = 4   # 加到 scheduler_core.py 顶部常量区

     def _sync_penalty(duration: int, anchor_duration: int | None,
                       sync_strength: int, changeover: int) -> float:
         """同步惩罚 → 等效空闲分钟（加法，二次方缩放）。
         anchor_duration 为 None/<=0 或 sync_strength<=0 时返回 0.0。
         公式：|dur-anchor|/anchor × (sync/100)² × changeover × SYNC_PENALTY_CHANGEOVER_MULT
         """
         if not anchor_duration or anchor_duration <= 0 or sync_strength <= 0:
             return 0.0
         strength_factor = (sync_strength / 100) ** 2
         return abs(duration - anchor_duration) / anchor_duration * strength_factor * changeover * SYNC_PENALTY_CHANGEOVER_MULT
     ```
  2. **消除三份拷贝**：
     - `scheduler_core.py:192-194`（`pick_task` 内的内联加法公式）改为 `sync_penalty = _sync_penalty(dur, anchor_duration, sync_strength, changeover)`，评分维持 `score = (prod_score, idle + sync_penalty, dur)`。
     - `scheduler_core.py:394-398`（`schedule_tasks._pick` 内的内联加法公式）改为 `sp = _sync_penalty(dur, anchor_dur, sync_strength, changeover)`，评分维持 `score = idle + sp`。
     - 公式本就相同，行为不变，仅消除重复定义。
  3. **新增 core 主循环纯函数** `schedule_greedy()` 到 `scheduler_core.py`（放在 `schedule_tasks` 附近，两函数并列）。签名（逐字照 design-scheduler.md T1·A.2，**plain-data，无 `Session`**）：
     ```python
     def schedule_greedy(
         *,
         demand_tasks: list[tuple[int, str, int]],   # 需求池 (config_id, color, order_priority)
         surplus_tasks: list[tuple[int, str]],        # 富余池 (config_id, color)，无富余传 []
         configs: dict[int, ConfigInfo],
         num_printers: int,
         windows: list[tuple[int, int]],
         custom_start: int,
         deadline: int,
         changeover: int,
         sync_strength: int,
         use_product_first: bool,                     # True=product_first, False=utilization
         sim_supply: dict[DemandKey, int] | None = None,
         product_units: list[tuple[int, int]] | None = None,
         bom_cache: dict[int, dict[DemandKey, int]] | None = None,
         assembled: set[int] | None = None,
     ) -> list[ScheduledTask]:
     ```
     内部逻辑（从 `scheduler.py` 现状主循环 666-773 行**原样下沉**，把 DB 写入改为收集 `ScheduledTask`）：
     - 分批循环：**首批从 `custom_start` 启动；后续批从 `find_next_start(min(printer_available))` 启动**（沿用 `scheduler.py:709-714` 现状，**禁止引入动态批次启动**）。
     - 每批逐台调 `pick_task`：先 `demand_tasks` 后 `surplus_tasks`（demand 全超 deadline 则 `clear()` 避免死循环，逻辑同 `scheduler.py:683-684/699-700`）。
     - 每批第一台设 `anchor_duration = cfg.duration_minutes`，后续台通过 `pick_task` 的 `anchor_duration`/`sync_strength` 参数叠加 `_sync_penalty`。
     - `use_product_first=True` 时：每选中一个任务，`sim_supply[(component_id, color)] += quantity` 后调 `try_assemble(sim_supply, product_units, bom_cache, assembled)`（就地修改入参，副作用语义同现状 `scheduler.py:748-751`）。
     - 死循环兜底沿用现状（无可用打印机→推进到下一窗口启动点，逻辑同 `scheduler.py:719-729`；空批→结束循环，同 `scheduler.py:766-768`）。
     - 返回 `list[ScheduledTask]`（与 `schedule_tasks` 同构）。
  4. **删除 `scheduler.py` 旧实现并改接线**：
     - 删除 `scheduler.py._pick_task`（211-286 行，乘法惩罚）。
     - 删除 `scheduler.py.generate_plan` 内 `_next_task` 辅助函数（666-701 行）与 `while True` 贪心主循环（703-773 行，含分批 / DB 写入 / `sim_supply` 更新）。
     - 把 `product_first`/`utilization` 路径改为：用现有 `_calc_ordered_tasks` / `_build_product_context` / `_build_surplus_tasks` 构建 plain-data（`demand_tasks` 三元组、`surplus_tasks` 二元组、`configs` 的 `ConfigInfo` 映射、`sim_supply`/`product_units`/`bom_cache`/`assembled`）→ 调 `schedule_greedy(...)` → 拿到 `list[ScheduledTask]`。
     - 移除对 `_pick_task_core` 的导入（27-37 行 import 块），改导入 `schedule_greedy`。
  5. **抽取共享写回辅助**：新增 `scheduler.py._persist_scheduled(db, plan, scheduled, printers)` 私有函数，把 `ScheduledTask → PrintBatch/PrintTask` 的写回逻辑（现状 two_phase 路径 583-607 行那段）抽出；**two_phase 路径与新贪心路径都调用它**，消除写回重复。
  6. **`demand_tasks` 元素统一为三元组** `(config_id, color, order_priority)`：`utilization` 也带 priority（FIFO），`pick_task` 的 `use_completion=False` 分支读 `item[2]` 作 prod_score（与现状一致，`_calc_ordered_tasks` 已返回三元组）。
  7. **新增/调整测试** 到 `backend/tests/test_scheduler.py`：
     - 新 `TestScheduleGreedy` 测试类：(a) product_first 下高 sync 不夺取 prod_score 主导（构造差 1 个瓶颈件即可凑齐的产品，断言 sync=100 仍优先选瓶颈件而非时长对齐件）；(b) utilization 下 sync 影响 idle 维度但 FIFO `order_priority` 仍优先；(c) demand 取完才取 surplus，demand 全超 deadline 时不死循环；(d) **批次启动断言：首批 == `custom_start`，后续批 == `find_next_start(min(printer_available))`，锁定「不引入动态批次启动」**；(e) 黄金值锁定：同一输入记录新加法惩罚的选择结果，防回退到乘法。
     - 新 `_sync_penalty` 单元测试：`anchor_duration=None`/`<=0`、`sync_strength=0` → 返回 `0.0`；取已知 `dur/anchor/changeover/sync` 手算比对公式数值；二次方缩放（sync=30 惩罚 ≪ sync=100）。
     - 同步梯度单调性扩展到贪心路径：参照现有 `test_sync_gradient_sensitivity`（533 行）/ `test_sync_100_fewer_batches_than_0`（487 行），新增覆盖 `schedule_greedy` 的批次数单调性用例。
- **Files**:
  - `backend/app/services/scheduler_core.py`（新增 `SYNC_PENALTY_CHANGEOVER_MULT`、`_sync_penalty`、`schedule_greedy`；改 `pick_task` 192-194、`schedule_tasks._pick` 394-398）
  - `backend/app/services/scheduler.py`（删 `_pick_task` 211-286；删 `generate_plan` 内 666-773 主循环；改 27-37 import；新增 `_persist_scheduled`；改 612 起的 product_first/utilization 接线）
  - `backend/tests/test_scheduler.py`（新增 `TestScheduleGreedy`、`_sync_penalty` 测试、贪心路径同步梯度用例）
- **Done when**:
  - `scheduler_core.py` 中同步惩罚只有一份定义（`_sync_penalty`），`pick_task` 与 `schedule_tasks._pick` 均调用它。
  - `scheduler.py` 中**不再存在** `_pick_task` 函数，且 `generate_plan` 内**不再存在**贪心分批 `while` 主循环（已下沉到 `schedule_greedy`）。
  - `scheduler.py` 两条路径（two_phase 与贪心）共用 `_persist_scheduled` 写回。
  - 评分元组统一为 `(prod_score, idle + additive_sync_penalty, dur)`，同步惩罚**绝不支配** `prod_score`（不再是元组前缀）。
  - 新测试全部通过，且 `cd backend && python -m pytest tests/ -v` 全绿。
- **Red lines（逐字遵守，见文件顶部「全程范围红线」）**: 不引入贪心路径的动态批次启动；不下沉 `_build_surplus_tasks`；不动 routers/前端中内联的 `changeover=15`；不改 DB schema / HTTP 契约 / 前端代码；不改富余量级。

### Task: Task-B（P0）two_phase partial 分支改为「凑整放弃」（complete-or-skip）

- **Do**:
  1. 在 `backend/app/services/scheduler_core.py` 的 `plan_two_phase` 中，**替换 301-318 行的 partial 分支**为「凑整放弃」：当 `time_needed > remaining_capacity`（即剩余产能放不下当前完整产品单元的全部短板盘），直接 `break`，**不产出任何部分盘**（不产孤儿件）。`plan_counts` 保留此前已完整 afford 的单元。
     - 伪代码（逐字照 design-scheduler.md T2·B.3）：
       ```python
       # 替换 301-318 行
       if time_needed > remaining_capacity:
           # 凑整放弃：放不下整个单元则不产出任何盘，结束分配
           break
       # time_needed <= remaining_capacity 时走原「产能足够」分支（320-334），逐组件记账
       ```
     - 把原 302-318 的「按 `plate_time` 降序塞 `bottleneck_items`」整段删除（含 `affordable`/`actual`/`overflow` 部分生产记账）。**316 行的 overflow 口径不一致随之消失**（部分生产路径不复存在）。
     - **不改**原「产能足够」分支（320-334 行）的 overflow 记账逻辑。
  2. **新增/扩充测试** 到 `backend/tests/test_scheduler.py` 的 `TestPlanTwoPhase` 类（161 行）：
     - **临界场景**：构造「产能恰好够 N 个完整产品、第 N+1 个差一点」的输入 → 断言产出恰为 N 个完整产品所需的盘、**无孤儿件**（断言每个出现的组件其总产出能整除其参与的 BOM 需求；或断言 `count_complete_products` 不因 partial 下降）。
     - **第一个产品就放不下**：构造 `remaining_capacity` 一开始就不够任何完整产品 → 断言 `plan_two_phase` 返回空列表（空增量计划）。
     - **边界恰好够**：构造某单元恰好填满剩余产能（`time_needed == remaining_capacity`）→ 断言该单元正常产出、走「产能足够」分支。
     - **回归**：确保原 `test_capacity_exhaustion`（220 行）与 `test_capacity_safety_margin`（252 行）仍通过（如断言因行为变更需调整，调整为反映「凑整放弃」语义，且不放松「无孤儿件」保证）。
- **Files**:
  - `backend/app/services/scheduler_core.py`（仅改 `plan_two_phase` 301-318 行 partial 分支）
  - `backend/tests/test_scheduler.py`（扩充 `TestPlanTwoPhase`）
- **Done when**:
  - `plan_two_phase` 的 partial 分支退化为单行 `break`，原「按 plate_time 降序塞盘」逻辑被删除。
  - 输出中不再出现使任一组件 `stock` 非整除其 BOM 需求的孤儿盘。
  - 新临界/边界/空计划测试通过；原 `test_capacity_exhaustion` / `test_capacity_safety_margin` 仍通过；`cd backend && python -m pytest tests/ -v` 全绿。
- **Red lines（逐字遵守，见文件顶部「全程范围红线」）**: 不引入贪心路径的动态批次启动；不下沉 `_build_surplus_tasks`；不动 routers/前端中内联的 `changeover=15`；不改 DB schema / HTTP 契约 / 前端代码；不改富余量级。

### Task: Task-C（P1）SURPLUS_TARGET_PRODUCTS 单一真相源

- **Do**:
  1. **保留** `backend/app/services/scheduler_core.py:21` 的 `SURPLUS_TARGET_PRODUCTS = 20` 作为**唯一定义**（值保持 20，不改）。
  2. 删除 `backend/app/services/scheduler.py:40` 的本地 `SURPLUS_TARGET_PRODUCTS = 20` 定义，改为从 core 导入：在 `scheduler.py` 顶部 `from .scheduler_core import (...)` 块（27-37 行）中加入 `SURPLUS_TARGET_PRODUCTS`。`scheduler.py` 内对该常量的所有引用（如 `_plan_two_phase` 444/458 行、`generate_plan` 639 行）继续指向导入的同一名称。
  3. **更新文档** `docs/schedule_specs.md §7.3`（259 行）：把 `SURPLUS_TARGET_PRODUCTS = 5` 的数值由 **5 改为 20**。仅改数值，不改其它叙述（§9.3 的算法重写归 Task-F，不在本任务）。
- **Files**:
  - `backend/app/services/scheduler.py`（删 40 行本地定义，改 27-37 import 块）
  - `backend/app/services/scheduler_core.py`（确认 21 行定义保留，不改值）
  - `docs/schedule_specs.md`（§7.3，259 行，5 → 20）
- **Done when**:
  - 代码库中 `SURPLUS_TARGET_PRODUCTS` 只有 `scheduler_core.py:21` 一处定义；`scheduler.py` 通过 import 引用。
  - `grep -rn "SURPLUS_TARGET_PRODUCTS = " backend/app` 只返回 `scheduler_core.py` 一行。
  - `docs/schedule_specs.md §7.3` 数值为 20。
  - `cd backend && python -m pytest tests/ -v` 全绿（导入改动不破坏现有测试）。
- **Red lines（逐字遵守，见文件顶部「全程范围红线」）**: 不引入贪心路径的动态批次启动；不下沉 `_build_surplus_tasks`；不动 routers/前端中内联的 `changeover=15`；不改 DB schema / HTTP 契约 / 前端代码；不改富余量级（值维持 20）。

---

## Parallel Group 2（依赖 Group 1 全部合并后执行）

> **排序约束**：Task-F 必须在 Task-A、Task-B 代码落地并合并后再做，否则会把 specs 改成与代码不符的另一种形态（design-scheduler.md T4 明确：F 依赖 A/B）。

### Task: Task-F（P2）规格与常量对齐

- **Do**:
  1. **`docs/schedule_specs.md §9.3` 用加法惩罚重写**（314-332 行）：删除「penalty 作为评分元组前置因子」「乘以 `sync_strength/100`」的旧叙述，改写为：同步惩罚转为**等效空闲分钟**加到 idle 维度；公式 `|dur-anchor|/anchor × (sync/100)² × changeover × 4`；并说明两个缩放因子的来由——
     - `×4`：相对 changeover 的放大倍数（对应代码常量 `SYNC_PENALTY_CHANGEOVER_MULT`）。
     - `²`（二次方缩放）：低强度（<30）近乎无惩罚、高强度（>70）强惩罚。
     - 重写后必须与 `scheduler_core._sync_penalty`（Task-A 落地的单一实现）**数值一致**。
  2. **同步更新 §9.4 效果示例**（334-344 行）：若示例表与新加法公式不符，更正为与新公式一致的效果描述（或注明示例为定性说明）。
  3. **澄清 FIFO 语义（§5.1 / §10）**：实现是「**硬 FIFO + 跳过库存已满足订单**」（`scheduler.py:132` 按 `created_at` 升序、`136` 静态 `priority = enumerate(orders)`，库存满足则 `continue`）。把 §5.1 / §10 中任何「软 FIFO」「不完全阻塞后续订单」类措辞改为与实现一致的硬 FIFO 表述。**只改文档、不改代码**（用户未要求实现软 FIFO）。
  4. **算法常量集中到 core 顶部 + specs 引用「见代码常量」**：把 `SURPLUS_TARGET_PRODUCTS=20`、changeover 默认 `15`、`compute_effective_capacity` 安全系数 `0.9`、`SYNC_PENALTY_CHANGEOVER_MULT=4` 集中到 `backend/app/services/scheduler_core.py` 顶部常量区（`SURPLUS_TARGET_PRODUCTS` 与 `SYNC_PENALTY_CHANGEOVER_MULT` 在 Task-A/C 后已在顶部；本任务补 changeover 默认 15 与安全系数 0.9 的命名常量，例如 `DEFAULT_CHANGEOVER_MINUTES = 15`、`CAPACITY_SAFETY_MARGIN = 0.9`，并让 `compute_effective_capacity` 的 `safety_margin` 默认、`scheduler._get_changeover_minutes` 的 fallback 引用之）。`schedule_specs.md` 中相关数值改为「见代码常量 `<NAME>`」并保留一处数值快照便于阅读。
     - **范围说明**：只收敛 `scheduler_core.py` / `scheduler.py` 内的算法常量。`routers/schedule.py.start_batch` 与前端 `Schedule.tsx` 中内联的 `15` **不动**（属 SystemConfig 初始化议题，见红线）。
- **Files**:
  - `docs/schedule_specs.md`（§5.1、§9.3、§9.4、§10，及各处常量数值改「见代码常量」）
  - `backend/app/services/scheduler_core.py`（顶部常量区集中 `DEFAULT_CHANGEOVER_MINUTES`、`CAPACITY_SAFETY_MARGIN` 等；让 `compute_effective_capacity` 默认值引用）
  - `backend/app/services/scheduler.py`（`_get_changeover_minutes` fallback `15` 改引用 `DEFAULT_CHANGEOVER_MINUTES`）
- **Done when**:
  - `schedule_specs.md §9.3` 描述的是加法等效空闲惩罚，公式与 `_sync_penalty` 一致，含 `×4` 与 `²` 缩放因子说明。
  - §5.1 / §10 的 FIFO 表述为「硬 FIFO + 跳过库存已满足订单」。
  - core/scheduler.py 的算法常量（SURPLUS、changeover 默认、安全系数、penalty 放大倍数）集中在 `scheduler_core.py` 顶部，specs 以「见代码常量」引用并保留快照值。
  - `routers`/前端的 `15` 未被改动。
  - `cd backend && python -m pytest tests/ -v` 全绿。
- **Red lines（逐字遵守，见文件顶部「全程范围红线」）**: 不引入贪心路径的动态批次启动；不下沉 `_build_surplus_tasks`；不动 routers/前端中内联的 `changeover=15`；不改 DB schema / HTTP 契约 / 前端代码；不改富余量级。

---

## Conflict Risks

- **`backend/app/services/scheduler_core.py` 被 Group 1 三个任务同时触碰**，但编辑区域**互不重叠**：
  - Task-A：顶部常量 `SYNC_PENALTY_CHANGEOVER_MULT`、新增 `_sync_penalty`、改 `pick_task`（192-194）、改 `schedule_tasks._pick`（394-398）、新增 `schedule_greedy`。
  - Task-B：仅改 `plan_two_phase` 的 partial 分支（301-318）。
  - Task-C：仅确认/保留顶部 `SURPLUS_TARGET_PRODUCTS`（21 行），不改值。
  - **缓解**：各任务限定在不同函数/行段；worktree 隔离 + 区域不重叠可干净合并。合并顺序建议 C → B → A（C 改动最小、A 最大），**每次合并后立即跑全量 `python -m pytest tests/ -v` 再合下一个**。
- **`backend/app/services/scheduler.py` 被 Task-A 与 Task-C 触碰**：A 大改（删 `_pick_task`、删主循环、改接线、加 `_persist_scheduled`、改 27-37 import）；C 仅在 27-37 import 块加 `SURPLUS_TARGET_PRODUCTS` 并删 40 行本地定义。两者都动 import 块。
  - **缓解**：合并时先合 C（小改动落地 import + 删 40 行），再合 A（A 在已含 import 的基础上 rebase；A 的 import 块编辑需与 C 的新增行协调）。若 A 已自行加了 `SURPLUS_TARGET_PRODUCTS` import 则去重。
- **`backend/tests/test_scheduler.py` 被 A、B 触碰**：均为**新增测试类/方法**（A 加 `TestScheduleGreedy` + `_sync_penalty` 测试；B 扩充 `TestPlanTwoPhase`），追加式改动，冲突风险低。
  - **缓解**：各自新增独立测试块，避免改动同一现有测试方法体。
- **`docs/schedule_specs.md` 被 Task-C（§7.3 数值）与 Task-F（§5.1/§9.3/§9.4/§10 + 常量引用）触碰**：但 C 在 Group 1、F 在 Group 2（C 先合并），且改动章节不同（C 只改 §7.3 一个数字），无并发冲突。
- **Group 2 对 Group 1 的硬依赖**：Task-F 重写 §9.3 必须以 Task-A 落地的 `_sync_penalty` 为准、常量集中依赖 Task-A/C 已把 `SYNC_PENALTY_CHANGEOVER_MULT`/`SURPLUS_TARGET_PRODUCTS` 放到 core 顶部；Task-F 的 FIFO 澄清需对照最终代码。**Group 1 全部合并并测试通过后方可启动 Group 2**。
