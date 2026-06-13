# Task Plan — Iter 2: 算法测试加固

Last updated: 2026-06-13

## Current State

iter1 修复了算法 P0/P1 问题（A/B/C/F），74 测试全绿。本轮把测试覆盖推到位 — **只补测试，不改任何生产代码**。

聚焦三类缺口：
- **P0 关键纯函数零直接覆盖**：`product_completion_score`（凑齐评分心脏）、`compute_effective_capacity`（产能安全系数）、`pick_task`（决策树多分支）— 现有覆盖只是间接经由更高层函数
- **P0 不变量（property）测试零覆盖**：所有现有调度测试都是"输入→期望"对照，没有任何"无论什么输入都必须成立"的断言（打印机无重叠、任务在 deadline 内、start 在窗口内、供给非负、批次产出守恒）
- **P1 审计原始指控仍未真正测到**：schedule_greedy 跨天窗口拼接、富余池清空兜底（scheduler_core.py:547 那行）、sync_strength 边界值（1/30/99）、三策略偏序断言（product_first 完整产品数 ≥ utilization）

## 全程红线（适用所有任务，违反即任务失败）

- ❌ **不改 backend/app/ 任何生产代码**（包括 scheduler.py / scheduler_core.py / routers / models / schemas）— 本轮纯测试
- ❌ 不修改任何**已有**的 test_* 方法体或 test class 名（只能 **append** 新 class / 新方法 / 新模块顶部辅助函数）
- ❌ 不改 pytest 配置 / conftest（如不存在，不要创建）
- ❌ 不引入新的 pip 依赖（hypothesis 等留到下轮 P2）
- ✅ 可以在 test_scheduler.py 顶部 import 块**末尾**追加新 import；不重排现有 import

**测试命令统一**：`cd backend && python -m pytest tests/ -v`

合并策略：三任务都只 append 到 backend/tests/test_scheduler.py 末尾，理论无冲突。合并顺序 T1 → T2 → T3，每次合并后跑全量测试。

---

## Parallel Group 1（3 任务并行）

### Task: Task-T1（P0）不变量辅助 + 全场景应用

**Do**:
1. 在 `backend/tests/test_scheduler.py` 顶部 import 块**末尾**追加一个新区块 `# === Invariant helpers (Iter 2) ===`，定义 5 个纯函数（接受 `list[ScheduledTask]` + 必要上下文）：
   - `_assert_no_printer_overlap(scheduled)` — 同一 `printer_index` 的任务时间区间互不相交（按 start 排序后，前任务 end + changeover ≤ 后任务 start；这里 changeover 由调用方传入，函数签名 `(scheduled, changeover)`）
   - `_assert_within_deadline(scheduled, deadline)` — 所有 `task.end_min ≤ deadline`
   - `_assert_start_within_windows(scheduled, windows)` — 所有 `task.start_min` 落在某个 `[ws, we]` 内（注意是 start 落入，task 可跨窗口运行）
   - `_assert_no_negative_supply(scheduled, configs, initial_supply, bom_map, product_units)` — 仿真：按 batch_order 升序、批内按 printer 升序回放 task；每次 task 产出加供给、每次 `try_assemble` 减供给；任何时刻 sim_supply[k] ≥ 0
   - `_assert_batch_quantity_conservation(scheduled, configs)` — 对每个 batch_order，组件累加 = sum(configs[t.config_id].quantity)
2. 新增 `class TestInvariantsAcrossScenarios:` — **不重复跑现有测试**，而是构造 5~8 个**有代表性的场景**，跑 `schedule_greedy` 和 `schedule_tasks`，应用上述 5 个不变量：
   - 单产品 × 多打印机（4 台）+ DEFAULT_WINDOWS + 24h deadline + 三策略各跑一遍
   - 多产品共享组件 + 初始供给 + 168h deadline
   - 单打印机 + 跨午休的长任务（如 4h 任务在 11:00 启动跨午休）
   - 跨天场景（48h deadline，第二天有窗口）
   - 任务全部超 deadline（应返回空列表，不变量自然满足）
   - product_first / utilization / two_phase 各跑同一场景一遍
3. 每个场景至少应用 3 个不变量（视场景适用性）；编写测试时**先**用 brief 输入断言新不变量函数对**人工构造的违反案例会 fail**（在测试方法内 inline 验证 negative case），再断言真实调度输出 pass — 防止辅助函数本身有 bug。

**Files**:
- `backend/tests/test_scheduler.py`（只 append；新辅助函数 + 新 TestInvariantsAcrossScenarios 类）

**Done when**:
- 5 个不变量辅助函数存在且每个都有"会 fail 在人工违反案例"的内嵌验证
- TestInvariantsAcrossScenarios 至少 5 个测试方法，覆盖三策略
- `cd backend && python -m pytest tests/ -v` 全绿（基线 74 + 新增 ≥ 5）
- 任何**现有**测试方法 / class 未被修改

---

### Task: Task-T2（P0）关键纯函数直接单测

**Do**:
1. **`class TestProductCompletionScore`** — 直接调 `product_completion_score`，至少覆盖：
   - `comp_key not in bom`（返回 `(inf, 0.0, inf)` 初值）
   - 单产品单元、BOM 全缺货 → min_ratio=0、comp_ratio=0
   - 单产品单元、BOM 部分供给 → 验证 min_ratio 是 BOM 中**最低**比例
   - 多产品单元、不同 priority → priority 小的获胜
   - `assembled` 集合内的单元被跳过
   - `bom_qty == 0` 时 comp_ratio 返回 inf
   - tie-breaker：相同 pu_pri 时按 -min_ratio 升序（min_ratio 大的赢）
   - tie-breaker：相同 (pu_pri, -min_ratio) 时按 comp_ratio 升序
2. **`class TestComputeEffectiveCapacity`** — 直接调 `compute_effective_capacity`，至少覆盖：
   - 空 windows → 0
   - 单窗口、安全系数=1.0 → window 长度（精确值）
   - 默认 `CAPACITY_SAFETY_MARGIN`（0.9）应用：手算结果与函数返回值一致
   - 多窗口 + 间隙（gap_loss 计算）
   - 跨天 windows（offset > 1440）
   - safety_margin=0 → 0
3. **`class TestPickTaskDirect`** — 直接调 `pick_task`（已有 2 处间接，这里补独立单测），至少覆盖：
   - 空 remaining → 返回 None
   - 所有任务 `start + dur > deadline` → 返回 None（且不修改 remaining）
   - use_completion=True 路径：sim_supply/product_units 起作用 vs 不传 → 选择不同
   - use_completion=False 路径：`order_priority` (item[2]) 作 prod_score
   - 同分 tie-break：构造完全相同 (prod_score, idle, dur) 的两个任务，断言选 list 中**第一个**（实现的 `best_idx == -1 or score < best_score` 用严格 <，所以第一个胜出 — 锁定此行为）
   - anchor_duration=None + sync_strength=100 → penalty=0（已被 TestSyncPenalty 覆盖，但此处验证传到 pick_task 后行为一致）

**Files**:
- `backend/tests/test_scheduler.py`（只 append；三个新 class）

**Done when**:
- 三个 class 合计至少 18 个测试
- 每个 class 至少一个测试在故意构造的"错误"输入下断言函数的**精确**返回（不是宽松的 "is not None"）
- 全量测试通过

---

### Task: Task-T3（P1）算法行为广度 + 三策略偏序

**Do**:
1. **`class TestScheduleGreedyCrossDay`** — schedule_greedy 跨天窗口（审计 P1-6 仍未真正测到）：
   - 48h deadline，第一天 + 第二天各有 DEFAULT_WINDOWS（offset = 1440），任务总时长超 24h → 断言至少有 task `start_min ≥ 1440`（落在第二天）
   - 任务正好跨午夜（如 23:30 启动 60min 任务）→ 断言 end_min 正确（跨过 1440），且不变量满足
   - deadline=72h、需求量足够把跨天填满 → 不变量 + 完整产品数随天数递增
2. **`class TestSurplusPoolClearance`** — 富余池清空兜底（scheduler_core.py:547 那行）：
   - 构造：demand 为空、surplus 全部超 deadline → 断言 schedule_greedy 返回空 list（不死循环）
   - 构造：demand 排完后剩余产能小、surplus 都不再 fit → 同上
   - 构造：demand 排完后 surplus 部分 fit、部分不 fit → 断言 fit 的被排上，不 fit 的不出现
3. **`class TestBoundarySync`** — sync_strength 边界值（现有只测 0/50/100）：
   - sync=1 vs sync=0 → 行为几乎一致（≤1% 差异）
   - sync=99 vs sync=100 → 行为几乎一致
   - sync=30 vs sync=70 → 30 的惩罚显著小于 70（二次方缩放：0.09 vs 0.49，差 5.4×）
   - anchor=1（接近零除边界）+ dur=100 + sync=50 → penalty 大但不抛异常
4. **`class TestStrategyOrdering`** — 三策略本质偏序（spec 的核心承诺，现 0 断言）：
   - 同一输入（多产品多 BOM，sync=50）下：`product_first` 的 `count_complete_products` ≥ `utilization` 的（凑齐策略本质要求）
   - 同一输入下：`two_phase` 的 `count_complete_products` ≥ `utilization` 的（two_phase 阶段 1 全局规划应不劣于纯利用率）
   - 同一输入下：三策略的总任务时长 ≤ 同一上限（一致性）
   - sync=0 时：`product_first` 与 `utilization` 输出在"凑齐"维度上 product_first 仍占优（或至少不劣）

**Files**:
- `backend/tests/test_scheduler.py`（只 append；4 个新 class）

**Done when**:
- 4 个 class 合计至少 14 个测试
- 偏序断言**对每个具体场景**而非泛化输入（防 false-pass）
- 全量测试通过

---

## 合并冲突预期

三任务全是 append-only 到 test_scheduler.py 末尾，唯一冲突点是 import 块末尾（如各自加了同名 import）。合并顺序：T1 → T2 → T3，每合一个跑全量测试。
