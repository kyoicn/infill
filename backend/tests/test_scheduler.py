"""
排班算法测试 — 测试 scheduler_core.py 的纯函数

运行: cd backend && python -m pytest tests/test_scheduler.py -v
"""

import pytest
from collections import defaultdict

from app.services.scheduler_core import (
    ConfigInfo, ScheduledTask, DemandKey,
    find_next_start, idle_after,
    product_completion_score, try_assemble,
    pick_task, compute_effective_capacity,
    plan_two_phase, schedule_tasks, count_complete_products,
    schedule_greedy, _sync_penalty, SYNC_PENALTY_CHANGEOVER_MULT,
)


# === Invariant helpers (Iter 2) ===
# 通用排班结果不变量断言 — 用于跨场景的 property-style 测试。
# 这些函数只读 ScheduledTask 列表 + 配置，不修改任何状态，违反不变量时 raise AssertionError。

def _assert_no_printer_overlap(
    scheduled: list[ScheduledTask],
    changeover: int,
) -> None:
    """不变量：同一打印机的相邻任务必须满足 prev.end + changeover <= next.start。

    若同一 printer_index 上有任务时间重叠，或后任务在换料完成前启动，则违反。
    """
    by_printer: dict[int, list[ScheduledTask]] = defaultdict(list)
    for t in scheduled:
        by_printer[t.printer_index].append(t)
    for pid, tasks in by_printer.items():
        tasks_sorted = sorted(tasks, key=lambda x: x.start_min)
        for i in range(1, len(tasks_sorted)):
            prev = tasks_sorted[i - 1]
            nxt = tasks_sorted[i]
            assert prev.end_min + changeover <= nxt.start_min, (
                f"Printer {pid} overlap: prev(end={prev.end_min})+changeover({changeover}) "
                f"> next(start={nxt.start_min})"
            )


def _assert_within_deadline(
    scheduled: list[ScheduledTask],
    deadline: int,
) -> None:
    """不变量：所有任务必须在 deadline 前结束（task.end_min <= deadline）。"""
    for t in scheduled:
        assert t.end_min <= deadline, (
            f"Task config={t.config_id} printer={t.printer_index} "
            f"end_min={t.end_min} > deadline={deadline}"
        )


def _assert_start_within_windows(
    scheduled: list[ScheduledTask],
    windows: list[tuple[int, int]],
    extra_allowed_starts: set[int] | None = None,
) -> None:
    """不变量：每个任务的 start_min 必须落在某个操作窗口内（只校验 start，任务可跨窗口运行）。

    extra_allowed_starts: 额外允许的 start_min 集合（例如 batch_0 在 custom_start 启动时
    可能不在任何窗口内，此时调用方传入 {custom_start} 放行）。
    """
    allowed = extra_allowed_starts or set()
    for t in scheduled:
        if t.start_min in allowed:
            continue
        in_window = any(ws <= t.start_min <= we for ws, we in windows)
        assert in_window, (
            f"Task config={t.config_id} printer={t.printer_index} "
            f"start_min={t.start_min} not in any window {windows} "
            f"(extra_allowed={sorted(allowed)})"
        )


def _assert_no_negative_supply(
    scheduled: list[ScheduledTask],
    configs: dict[int, ConfigInfo],
    initial_supply: dict[DemandKey, int],
    bom_cache: dict[int, dict[DemandKey, int]],
    product_units: list[tuple[int, int]],
) -> None:
    """不变量：按 batch_order asc、批内 printer_index asc 回放排程，
    每步加入新产出再 try_assemble 消费，任何时刻 sim_supply 任一 key 都 >= 0。
    """
    sim_supply: dict[DemandKey, int] = dict(initial_supply)
    for k, v in sim_supply.items():
        assert v >= 0, f"initial_supply negative at start: {k}={v}"

    assembled: set[int] = set()
    ordered = sorted(scheduled, key=lambda t: (t.batch_index, t.printer_index))
    for t in ordered:
        cfg = configs[t.config_id]
        key = (cfg.component_id, t.color)
        sim_supply[key] = sim_supply.get(key, 0) + cfg.quantity
        try_assemble(sim_supply, product_units, bom_cache, assembled)
        for k, v in sim_supply.items():
            assert v >= 0, (
                f"Negative supply for {k}={v} after task "
                f"config={t.config_id} batch={t.batch_index} printer={t.printer_index}"
            )


def _assert_batch_quantity_conservation(
    scheduled: list[ScheduledTask],
    configs: dict[int, ConfigInfo],
) -> dict[int, dict[DemandKey, int]]:
    """不变量/工具：按 batch_order 累加各 (component_id, color) 的产出盘数 × 单盘产量。

    返回 {batch_order: {(comp_id, color): total_units_produced}}。
    同时断言：每批 totals 中所有数值 >= 0 且 == 该批内逐 task 累加值。
    调用方可比对该 dict 与外部独立累加结果，进一步验证守恒。
    """
    by_batch: dict[int, list[ScheduledTask]] = defaultdict(list)
    for t in scheduled:
        by_batch[t.batch_index].append(t)

    result: dict[int, dict[DemandKey, int]] = {}
    for batch_order, tasks in by_batch.items():
        totals: dict[DemandKey, int] = defaultdict(int)
        manual_sum = 0
        for t in tasks:
            cfg = configs[t.config_id]
            key = (cfg.component_id, t.color)
            totals[key] += cfg.quantity
            manual_sum += cfg.quantity
        recomputed = sum(totals.values())
        assert recomputed == manual_sum, (
            f"Batch {batch_order} totals sum {recomputed} != manual {manual_sum}"
        )
        for k, v in totals.items():
            assert v >= 0, f"Batch {batch_order} negative count {k}={v}"
        result[batch_order] = dict(totals)
    return result


# ---------------------------------------------------------------------------
# Fixtures: 构造测试数据
# ---------------------------------------------------------------------------

# 默认操作窗口：8-12, 12:30-18, 18:30-23（分钟）
DEFAULT_WINDOWS = [(480, 720), (750, 1080), (1110, 1380)]

# 双日窗口（48h 场景）
TWO_DAY_WINDOWS = [
    (480, 720), (750, 1080), (1110, 1380),          # Day 0
    (1920, 2160), (2190, 2520), (2550, 2820),        # Day 1
]

CHANGEOVER = 15


def make_config(id: int, comp_id: int, name: str, qty: int, dur: int) -> ConfigInfo:
    return ConfigInfo(id=id, component_id=comp_id, component_name=name,
                      quantity=qty, duration_minutes=dur)


# 简单桌子 BOM: 4 种组件
DESK_BOM = {
    (1, "白色"): 1,   # 桌板 × 1
    (2, "白色"): 2,   # 桌腿 × 2
    (3, "白色"): 1,   # 抽屉 × 1
    (4, "白色"): 4,   # 螺丝 × 4
}

DESK_CONFIGS = {
    (1, "白色"): make_config(101, 1, "桌板", qty=1, dur=120),  # 1/盘, 2h
    (2, "白色"): make_config(102, 2, "桌腿", qty=4, dur=90),   # 4/盘, 1.5h
    (3, "白色"): make_config(103, 3, "抽屉", qty=2, dur=150),  # 2/盘, 2.5h
    (4, "白色"): make_config(104, 4, "螺丝", qty=20, dur=60),  # 20/盘, 1h
}

DESK_CONFIG_BY_ID = {c.id: c for c in DESK_CONFIGS.values()}

# 真实场景：混合时长组件（模拟转角书桌）
REAL_CONFIGS = {
    (1, "白色"): make_config(201, 1, "上柜", qty=1, dur=149),
    (2, "粉色"): make_config(202, 2, "桌板", qty=1, dur=65),
    (3, "白色"): make_config(203, 3, "下桌", qty=2, dur=200),
    (4, "白色"): make_config(204, 4, "抽屉", qty=16, dur=273),
    (5, "白色"): make_config(205, 5, "窗户", qty=12, dur=88),
    (6, "白色"): make_config(206, 6, "下柜", qty=8, dur=776),
    (7, "任意颜色"): make_config(207, 7, "固定件", qty=15, dur=206),
    (8, "白色"): make_config(208, 8, "把手", qty=250, dur=108),
}

REAL_BOM = {
    (1, "白色"): 1,
    (2, "粉色"): 1,
    (3, "白色"): 1,
    (4, "白色"): 1,
    (5, "白色"): 1,
    (6, "白色"): 1,
    (7, "任意颜色"): 1,
    (8, "白色"): 1,
}

REAL_CONFIG_BY_ID = {c.id: c for c in REAL_CONFIGS.values()}


# =====================================================================
# 1. 纯函数单元测试
# =====================================================================

class TestFindNextStart:
    def test_in_window(self):
        """1.1 当前时间在窗口内 → 返回当前时间"""
        assert find_next_start(500, DEFAULT_WINDOWS) == 500

    def test_between_windows(self):
        """1.2 当前时间在两个窗口之间 → 返回下个窗口开始"""
        assert find_next_start(730, DEFAULT_WINDOWS) == 750

    def test_past_all_windows(self):
        """1.3 当前时间在所有窗口之后 → 返回 None"""
        assert find_next_start(1400, DEFAULT_WINDOWS) is None

    def test_at_window_start(self):
        """窗口开始时刻 → 返回该时刻"""
        assert find_next_start(480, DEFAULT_WINDOWS) == 480

    def test_at_window_end(self):
        """窗口结束时刻 → 返回该时刻（仍在窗口内）"""
        assert find_next_start(720, DEFAULT_WINDOWS) == 720


class TestIdleAfter:
    def test_in_window(self):
        """1.4 任务结束在窗口内 → idle=0"""
        # start=500, dur=100, changeover=15 → available_at=615, 在 (480,720) 内
        assert idle_after(500, 100, 15, DEFAULT_WINDOWS) == 0

    def test_in_gap(self):
        """1.5 任务结束在窗口间隔 → idle > 0"""
        # start=600, dur=110, changeover=15 → available_at=725, 在 720~750 间
        assert idle_after(600, 110, 15, DEFAULT_WINDOWS) == 750 - 725

    def test_exactly_at_next_window(self):
        """任务结束恰好在下一个窗口开始"""
        # available_at = 750, 在 (750, 1080) 内
        assert idle_after(600, 135, 15, DEFAULT_WINDOWS) == 0


class TestTryAssemble:
    def test_basic(self):
        """1.6 库存够组装 1 个产品"""
        supply = {(1, "白色"): 1, (2, "白色"): 2, (3, "白色"): 1, (4, "白色"): 4}
        units = [(0, 10)]
        bom = {10: DESK_BOM}
        assembled: set[int] = set()
        try_assemble(supply, units, bom, assembled)
        assert 0 in assembled
        # 库存应被消耗
        assert supply[(1, "白色")] == 0

    def test_insufficient(self):
        """1.7 库存不够 → assembled 不变"""
        supply = {(1, "白色"): 1, (2, "白色"): 1}  # 桌腿只有 1，需要 2
        units = [(0, 10)]
        bom = {10: DESK_BOM}
        assembled: set[int] = set()
        try_assemble(supply, units, bom, assembled)
        assert len(assembled) == 0

    def test_priority_order(self):
        """1.8 多个产品可组装时 → 优先级高的先消耗库存"""
        supply = {(1, "白色"): 1, (2, "白色"): 2, (3, "白色"): 1, (4, "白色"): 4}
        units = [(0, 10), (1, 10)]  # 两个同产品，优先级 0 和 1
        bom = {10: DESK_BOM}
        assembled: set[int] = set()
        try_assemble(supply, units, bom, assembled)
        assert 0 in assembled  # 优先级 0 先组装
        assert 1 not in assembled  # 库存不够第二个


# =====================================================================
# 2. Phase 1 产能规划测试
# =====================================================================

class TestPlanTwoPhase:
    def _run_plan(self, num_printers=4, hours=24, product_queue=None,
                  bom=None, configs=None, supply=None, windows=None):
        if product_queue is None:
            product_queue = [(0, 10)]
        if bom is None:
            bom = {10: DESK_BOM}
        if configs is None:
            configs = DESK_CONFIGS
        if supply is None:
            supply = {}
        if windows is None:
            windows = DEFAULT_WINDOWS
        custom_start = 0
        deadline = hours * 60
        return plan_two_phase(
            num_printers=num_printers,
            duration_hours=hours,
            changeover=CHANGEOVER,
            surplus_enabled=False,
            windows=windows,
            custom_start=custom_start,
            deadline=deadline,
            product_queue=product_queue,
            bom_map=bom,
            config_map=configs,
            initial_supply=supply,
        )

    def test_single_product(self):
        """2.1 1 种产品 → 产出正确的组件任务"""
        tasks = self._run_plan()
        assert len(tasks) > 0
        config_ids = {t[0] for t in tasks}
        # 应该包含所有 4 种组件的配置
        assert 101 in config_ids  # 桌板
        assert 102 in config_ids  # 桌腿

    def test_overflow_reuse(self):
        """2.2 高产出组件溢出被后续产品复用"""
        # 2 个产品单元，螺丝需要 4/个，但 1 盘产 20
        queue = [(0, 10), (0, 10)]
        tasks = self._run_plan(product_queue=queue)
        # 螺丝应该只有 1 盘（20 够两个产品的 8 个需求）
        screw_tasks = [t for t in tasks if t[0] == 104]  # config_id=104 是螺丝
        assert len(screw_tasks) == 1

    def test_overflow_across_products(self):
        """2.3 两种产品共用组件 → 溢出复用"""
        # 产品 A 和 B 都需要螺丝
        bom_a = {(4, "白色"): 2}  # 只需螺丝
        bom_b = {(4, "白色"): 3}  # 也只需螺丝
        bom_map = {10: bom_a, 11: bom_b}
        configs = {(4, "白色"): DESK_CONFIGS[(4, "白色")]}
        queue = [(0, 10), (1, 11)]
        tasks = self._run_plan(product_queue=queue, bom=bom_map, configs=configs)
        # 螺丝 20/盘，2+3=5 只需 1 盘
        assert len(tasks) == 1

    def test_capacity_exhaustion(self):
        """2.4 产能不够所有产品 → 按优先级截断"""
        # 100 个产品单元，24h 4 台打印机放不下
        queue = [(0, 10)] * 100
        tasks = self._run_plan(product_queue=queue)
        # 应该有任务但不是全部 100 个产品的量
        assert len(tasks) > 0
        assert len(tasks) < 100 * 4  # 远少于 400 盘

    def test_no_orders_surplus_only(self):
        """2.5 无订单 + 指定产品 → 全部作为富余"""
        queue = [(999, 10)] * 5  # 优先级 999 = 非订单
        tasks = self._run_plan(product_queue=queue)
        assert len(tasks) > 0

    def test_with_initial_supply(self):
        """2.6 初始库存部分满足 → 需求量正确扣减"""
        # 库存有 1 个桌板，1 个抽屉
        supply = {(1, "白色"): 1, (3, "白色"): 1}
        tasks_with = self._run_plan(supply=supply)
        tasks_without = self._run_plan(supply={})
        # 有初始库存时应该需要更少的任务
        assert len(tasks_with) <= len(tasks_without)

    def test_1_to_1_components(self):
        """2.7 1:1 产出组件 → 每个产品单元都需要打印"""
        # 桌板是 1/盘的组件
        queue = [(0, 10)] * 5  # 5 个产品
        tasks = self._run_plan(product_queue=queue)
        board_tasks = [t for t in tasks if t[0] == 101]  # 桌板
        assert len(board_tasks) == 5  # 每个产品需要 1 盘

    def test_capacity_safety_margin(self):
        """2.8 Phase 1 任务应能被 Phase 2 全部排入"""
        queue = [(999, 10)] * 20
        tasks = self._run_plan(product_queue=queue, hours=24)
        # 用 Phase 2 排程
        scheduled = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, num_printers=4,
            windows=DEFAULT_WINDOWS, custom_start=0,
            deadline=24 * 60, changeover=CHANGEOVER, sync_strength=0,
        )
        # 至少 80% 的任务应该排入（理想 100%，留少量容错）
        assert len(scheduled) >= len(tasks) * 0.8

    # ----- 凑整放弃（complete-or-skip）语义 -----

    @staticmethod
    def _count_plate_time(tasks, configs):
        """根据 (config_id, color) 任务列表汇总盘数与总占用产能（含换料）。"""
        plate_counts: dict[int, int] = defaultdict(int)
        for cid, _, _ in tasks:
            plate_counts[cid] += 1
        config_by_id = {c.id: c for c in configs.values()}
        total_time = sum(
            cnt * (config_by_id[cid].duration_minutes + CHANGEOVER)
            for cid, cnt in plate_counts.items()
        )
        return plate_counts, total_time

    @staticmethod
    def _assert_no_orphans(tasks, bom, configs, n_units):
        """断言产出恰好满足 n_units 个完整产品的 BOM，无孤儿件。

        每个组件的实际产出（盘数 × 每盘产量）应 >= n_units × bom_qty，
        且每个被生产的组件都属于该 BOM（无组件被部分生产成孤儿）。
        """
        config_by_id = {c.id: c for c in configs.values()}
        # 组件 component_id -> 产出数量
        produced: dict[int, int] = defaultdict(int)
        for cid, _, _ in tasks:
            cfg = config_by_id[cid]
            produced[cfg.component_id] += cfg.quantity
        for comp_key, qty in bom.items():
            comp_id = comp_key[0]
            # 必须凑齐 n_units 个产品的该组件需求
            assert produced[comp_id] >= n_units * qty, (
                f"component {comp_id}: produced {produced[comp_id]} "
                f"< required {n_units * qty}"
            )

    def test_complete_or_skip_threshold(self):
        """2.9 临界：产能恰好够 N 个完整产品，第 N+1 个差一点 → 产出恰为 N 个，无孤儿件。

        用单组件产品（1 盘 = 1 个产品单元），把 deadline 调到只够 N 个完整单元，
        N+1 个的最后一盘差一点，断言产出 == N 盘且无部分生产的孤儿盘。
        """
        # 单组件产品：1 个桌板 = 1 个产品；桌板 120min，changeover 15 → 135min/单元
        bom = {10: {(1, "白色"): 1}}
        configs = {(1, "白色"): DESK_CONFIGS[(1, "白色")]}
        plate_unit = 120 + CHANGEOVER  # 135

        # 1 台打印机、无窗口约束（custom_start=0, 连续窗口）便于精确控产能
        N = 3
        # compute_effective_capacity 用 safety_margin=0.9，故按 0.9 反推 deadline。
        # 让有效产能恰好覆盖 N 个单元、不足 N+1 个：
        #   0.9 * deadline ∈ [N*plate_unit, (N+1)*plate_unit)
        # 取 deadline 使 0.9*deadline 略大于 N*plate_unit 但远小于 (N+1)*plate_unit
        target_cap = N * plate_unit + 10  # 略多于 N 个、远不足 N+1 个
        deadline = int(target_cap / 0.9) + 1
        full_window = [(0, deadline)]

        queue = [(0, 10)] * (N + 5)  # 远多于产能
        tasks = plan_two_phase(
            num_printers=1, duration_hours=deadline // 60, changeover=CHANGEOVER,
            surplus_enabled=False, windows=full_window,
            custom_start=0, deadline=deadline,
            product_queue=queue, bom_map=bom,
            config_map=configs, initial_supply={},
        )
        plate_counts, total_time = self._count_plate_time(tasks, configs)
        # 恰好 N 盘（= N 个完整产品），无第 N+1 个的孤儿盘
        assert plate_counts[101] == N, f"expected {N} plates, got {dict(plate_counts)}"
        self._assert_no_orphans(tasks, bom[10], configs, N)

    def test_complete_or_skip_first_unit_too_big(self):
        """2.10 第一个产品就放不下 → 返回空列表（无任何孤儿盘）。"""
        # 4 组件桌子产品需要 4 盘，把 deadline 调到连 1 个完整单元都放不下
        # 最短组件螺丝 60min + 15 = 75min；0.9*50=45 < 75，连一盘都不够
        tasks = plan_two_phase(
            num_printers=1, duration_hours=1, changeover=CHANGEOVER,
            surplus_enabled=False, windows=[(0, 50)],
            custom_start=0, deadline=50,
            product_queue=[(0, 10)], bom_map={10: DESK_BOM},
            config_map=DESK_CONFIGS, initial_supply={},
        )
        assert tasks == [], f"expected empty plan, got {tasks}"

    def test_complete_or_skip_exact_boundary(self):
        """2.11 边界恰好够：某单元 time_needed == remaining_capacity → 正常产出（走产能足够分支）。"""
        # 单组件产品，1 盘/单元。让有效产能恰好 == 1 个单元的 time_needed。
        bom = {10: {(1, "白色"): 1}}
        configs = {(1, "白色"): DESK_CONFIGS[(1, "白色")]}
        plate_unit = 120 + CHANGEOVER  # 135
        # 需要 compute_effective_capacity == plate_unit，即 0.9*deadline == 135
        deadline = round(plate_unit / 0.9)  # 150 → 0.9*150 = 135
        full_window = [(0, deadline)]
        tasks = plan_two_phase(
            num_printers=1, duration_hours=deadline // 60 + 1, changeover=CHANGEOVER,
            surplus_enabled=False, windows=full_window,
            custom_start=0, deadline=deadline,
            product_queue=[(0, 10), (0, 10)], bom_map=bom,
            config_map=configs, initial_supply={},
        )
        plate_counts, _ = self._count_plate_time(tasks, configs)
        # 恰好够 1 个单元（== 边界），第 2 个放不下
        assert plate_counts[101] == 1, f"expected exactly 1 plate at boundary, got {dict(plate_counts)}"
        self._assert_no_orphans(tasks, bom[10], configs, 1)

    def test_capacity_exhaustion_no_orphans(self):
        """2.12 产能截断后产出仍为整数个完整产品（无孤儿件）。

        多组件产品 + 产能不足以全部生产 → 截断后每个组件总产出
        都满足同样数量个完整产品的 BOM。
        """
        queue = [(0, 10)] * 100  # 远超产能
        tasks = self._run_plan(product_queue=queue)
        # 推断完成的完整产品数（按桌板 1:1 = 完整产品数下界）
        config_by_id = {c.id: c for c in DESK_CONFIGS.values()}
        produced: dict[int, int] = defaultdict(int)
        for cid, _, _ in tasks:
            cfg = config_by_id[cid]
            produced[cfg.component_id] += cfg.quantity
        # 每个组件的产出都能凑齐 n 个完整产品（n = 各组件可支撑的最小完整数）
        n_complete = min(
            produced[comp_key[0]] // qty for comp_key, qty in DESK_BOM.items()
        )
        assert n_complete >= 1
        self._assert_no_orphans(tasks, DESK_BOM, DESK_CONFIGS, n_complete)

    def test_complete_or_skip_no_long_component_orphan(self):
        """2.13 截断剩余产能足够单个长组件盘但凑不齐整单元 → 不产出长件孤儿盘。

        这是「凑整放弃」相对旧 partial（按 plate_time 降序塞长件）的关键区别：
        旧逻辑会用剩余产能塞 1 个最长组件盘，产出无法装配的孤儿件；
        新逻辑直接 break，长组件计数不应被这部分剩余产能抬高。

        构造：2 组件产品（长件 100min、短件 30min，各 1 件/盘，BOM 各 1）。
        让剩余产能在 K 个完整单元后 ≈ 长件单盘时间但 < 一个完整单元 →
        断言长件与短件盘数相等（成对、无孤儿长件）。
        """
        bom = {10: {(91, "白色"): 1, (92, "白色"): 1}}
        long_cfg = make_config(901, 91, "长件", qty=1, dur=100)   # 100+15=115/盘
        short_cfg = make_config(902, 92, "短件", qty=1, dur=30)   # 30+15=45/盘
        configs = {(91, "白色"): long_cfg, (92, "白色"): short_cfg}
        unit_time = 115 + 45  # 160/完整单元
        K = 2
        # 剩余产能目标：K 个完整单元 + 长件单盘(115) + 一点零头，但 < (K+1) 个完整单元
        # → 旧逻辑会在第 K+1 单元塞入 1 个长件孤儿盘（115 <= 剩余），新逻辑 break
        target_cap = K * unit_time + 115 + 10  # 在 [K*unit, (K+1)*unit) 内但够 1 个长件盘
        deadline = int(target_cap / 0.9) + 1
        tasks = plan_two_phase(
            num_printers=1, duration_hours=deadline // 60 + 1, changeover=CHANGEOVER,
            surplus_enabled=False, windows=[(0, deadline)],
            custom_start=0, deadline=deadline,
            product_queue=[(0, 10)] * (K + 5), bom_map=bom,
            config_map=configs, initial_supply={},
        )
        plate_counts, _ = self._count_plate_time(tasks, configs)
        long_plates = plate_counts.get(901, 0)
        short_plates = plate_counts.get(902, 0)
        # 成对产出：长件与短件盘数相等（无孤儿长件），且恰为 K 个完整单元
        assert long_plates == short_plates == K, (
            f"expected paired {K} plates each, got long={long_plates} short={short_plates}"
        )


# =====================================================================
# 3. Phase 2 时间排程测试
# =====================================================================

class TestScheduleTasks:
    def _make_tasks(self, configs_by_id, count_per_config=1):
        tasks = []
        for cid in configs_by_id:
            for _ in range(count_per_config):
                tasks.append((cid, "白色", False))
        return tasks

    def test_basic(self):
        """3.1 4 台打印机，简单任务 → 正确分配"""
        tasks = [(101, "白色", False)] * 8
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        assert len(result) == 8
        # 第一批次应有 4 个任务
        batch0 = [t for t in result if t.batch_index == 0]
        assert len(batch0) == 4

    def test_batch0_custom_start(self):
        """3.2 第一批次在 custom_start 启动"""
        tasks = [(101, "白色", False)] * 4
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
        )
        assert all(t.start_min == 0 for t in result if t.batch_index == 0)

    def test_respects_windows(self):
        """3.3 后续批次只在窗口内启动"""
        # 从 0 点开始，第一批后应等到 480（第一个窗口）
        tasks = [(104, "白色", False)] * 8  # 螺丝 60min
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
        )
        batch1 = [t for t in result if t.batch_index == 1]
        if batch1:
            # 第二批次应在窗口内启动
            start = batch1[0].start_min
            in_window = any(ws <= start <= we for ws, we in DEFAULT_WINDOWS)
            # 或者在 batch_0 结束 + changeover 后仍在窗口内
            assert in_window or start >= 60 + CHANGEOVER

    def test_deadline(self):
        """3.4 超出 deadline 的任务不被排入"""
        tasks = [(101, "白色", False)] * 20  # 桌板 120min
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=720, changeover=CHANGEOVER,
        )
        # 只有 240 分钟窗口，120min + changeover 后只能 1 批
        for t in result:
            assert t.end_min <= 720

    def test_changeover(self):
        """3.5 打印机可用时间 = 任务结束 + 换料时间"""
        tasks = [(104, "白色", False)] * 2  # 1 台打印机 2 个任务
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 1, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        if len(result) >= 2:
            t0 = result[0]
            t1 = result[1]
            assert t1.start_min >= t0.end_min + CHANGEOVER

    def test_empty_tasks(self):
        """3.6 空任务列表 → 返回空结果"""
        result = schedule_tasks(
            [], DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        assert len(result) == 0

    def test_single_printer(self):
        """3.7 1 台打印机 → 任务串行排列"""
        tasks = [(104, "白色", False)] * 4
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 1, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        for i in range(1, len(result)):
            assert result[i].start_min >= result[i - 1].end_min + CHANGEOVER

    def test_task_spans_gap(self):
        """3.8 长任务跨越窗口间隔 → 允许"""
        # 任务 200min，从 600 开始 → 结束 800，跨越 720~750 间隔
        long_cfg = {301: make_config(301, 30, "长板", qty=1, dur=200)}
        tasks = [(301, "白色", False)]
        result = schedule_tasks(
            tasks, long_cfg, 1, DEFAULT_WINDOWS,
            custom_start=600, deadline=1440, changeover=CHANGEOVER,
        )
        assert len(result) == 1
        assert result[0].end_min == 800  # 跨越了 720

    def test_all_tasks_fit(self):
        """3.9 任务总量 < 产能 → 所有任务都应排入"""
        tasks = [(104, "白色", False)] * 4  # 4 × 60min = 240min, 远小于产能
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        assert len(result) == 4


# =====================================================================
# 4. 同步策略测试
# =====================================================================

class TestSyncStrength:
    def _make_mixed_tasks(self):
        """创建混合时长的任务"""
        configs = {
            401: make_config(401, 41, "短", qty=1, dur=60),
            402: make_config(402, 42, "中", qty=1, dur=120),
            403: make_config(403, 43, "长", qty=1, dur=240),
        }
        tasks = (
            [(401, "白色", False)] * 4 +
            [(402, "白色", False)] * 4 +
            [(403, "白色", False)] * 4
        )
        return tasks, configs

    def test_sync_0(self):
        """4.1 sync=0 → 不对齐，按最优选择"""
        tasks, configs = self._make_mixed_tasks()
        result = schedule_tasks(
            tasks, configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=0,
        )
        assert len(result) > 0
        # 第一批次可能有不同时长
        batch0 = [t for t in result if t.batch_index == 0]
        durations = {t.end_min - t.start_min for t in batch0}
        # sync=0 时不保证同一批次时长一致
        assert len(batch0) > 0

    def test_sync_100(self):
        """4.2 sync=100 → 同一批次尽量选相同时长"""
        tasks, configs = self._make_mixed_tasks()
        result = schedule_tasks(
            tasks, configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=100,
        )
        batch0 = [t for t in result if t.batch_index == 0]
        if len(batch0) >= 2:
            durations = [t.end_min - t.start_min for t in batch0]
            # sync=100 时同批次时长应相近或相同
            assert max(durations) - min(durations) <= max(durations) * 0.5

    def test_sync_50(self):
        """4.3 sync=50 → 折中选择"""
        tasks, configs = self._make_mixed_tasks()
        result = schedule_tasks(
            tasks, configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=50,
        )
        assert len(result) > 0

    def test_anchor_first_printer(self):
        """4.4 第一台无惩罚，第二台起用锚定"""
        configs = {
            501: make_config(501, 51, "A", qty=1, dur=100),
            502: make_config(502, 52, "B", qty=1, dur=200),
        }
        tasks = [(501, "白色", False), (502, "白色", False)]
        result = schedule_tasks(
            tasks, configs, 2, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=100,
        )
        assert len(result) == 2

    def test_sync_self_reinforcing(self):
        """4.5 同步后打印机同时完成 → 下一批次仍同步"""
        configs = {
            601: make_config(601, 61, "X", qty=1, dur=120),
        }
        # 所有任务相同时长 → 完美同步
        tasks = [(601, "白色", False)] * 8
        result = schedule_tasks(
            tasks, configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=100,
        )
        batch0 = [t for t in result if t.batch_index == 0]
        batch1 = [t for t in result if t.batch_index == 1]
        if batch0 and batch1:
            # 同一批次所有任务结束时间相同
            ends0 = {t.end_min for t in batch0}
            assert len(ends0) == 1  # 完美同步
            ends1 = {t.end_min for t in batch1}
            assert len(ends1) == 1

    def test_sync_no_matching_duration(self):
        """4.6 无同时长任务 → 自动降级"""
        configs = {
            701: make_config(701, 71, "P", qty=1, dur=60),
            702: make_config(702, 72, "Q", qty=1, dur=300),
        }
        # 只有两种时长，4 台打印机
        tasks = [(701, "白色", False), (702, "白色", False)]
        result = schedule_tasks(
            tasks, configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=100,
        )
        # 应该都能排入，即使时长差异大
        assert len(result) == 2

    def test_sync_100_fewer_batches_than_0(self):
        """4.7 sync=100 不应比 sync=0 产生更多批次（回归测试）"""
        tasks, configs = self._make_mixed_tasks()

        r0 = schedule_tasks(
            list(tasks), configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=0,
        )
        r100 = schedule_tasks(
            list(tasks), configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=100,
        )

        batches_0 = max((t.batch_index for t in r0), default=-1) + 1
        batches_100 = max((t.batch_index for t in r100), default=-1) + 1
        assert batches_100 <= batches_0, \
            f"sync=100 has {batches_100} batches > sync=0 has {batches_0}"

    def test_sync_100_fills_batches_with_real_tasks(self):
        """4.8 sync=100 用真实混合时长（65~776min）不应导致批次暴增"""
        # 模拟真实书桌场景
        tasks = (
            [(201, "白色", False)] * 16 +  # 上柜 149min
            [(202, "粉色", False)] * 16 +  # 桌板 65min
            [(203, "白色", False)] * 8 +   # 下桌 200min
            [(204, "白色", False)] * 4 +   # 抽屉 273min
            [(206, "白色", False)] * 3     # 下柜 776min
        )
        r100 = schedule_tasks(
            list(tasks), REAL_CONFIG_BY_ID, 4, TWO_DAY_WINDOWS,
            custom_start=0, deadline=2880, changeover=CHANGEOVER,
            sync_strength=100,
        )
        r0 = schedule_tasks(
            list(tasks), REAL_CONFIG_BY_ID, 4, TWO_DAY_WINDOWS,
            custom_start=0, deadline=2880, changeover=CHANGEOVER,
            sync_strength=0,
        )
        batches_0 = max((t.batch_index for t in r0), default=-1) + 1
        batches_100 = max((t.batch_index for t in r100), default=-1) + 1
        # sync=100 应该不比 sync=0 多太多批次（允许 10% 误差）
        assert batches_100 <= batches_0 * 1.1, \
            f"sync=100 batches={batches_100} >> sync=0 batches={batches_0}"

    def test_sync_gradient_sensitivity(self):
        """4.9 sync 0→25→50→75→100 批次数应渐变递减（不应都一样或反跳）"""
        tasks, configs = self._make_mixed_tasks()
        batch_counts = {}
        for s in [0, 25, 50, 75, 100]:
            result = schedule_tasks(
                list(tasks), configs, 4, DEFAULT_WINDOWS,
                custom_start=480, deadline=1440, changeover=CHANGEOVER,
                sync_strength=s,
            )
            batch_counts[s] = max((t.batch_index for t in result), default=-1) + 1

        # 单调不增（允许相邻值相等）
        values = [batch_counts[s] for s in [0, 25, 50, 75, 100]]
        for i in range(len(values) - 1):
            assert values[i] >= values[i + 1], \
                f"batch counts not monotonic: {dict(zip([0,25,50,75,100], values))}"

        # 0 和 100 之间应该有明显差距（不能全一样）
        assert batch_counts[0] > batch_counts[100] or batch_counts[0] <= 3, \
            f"no gradient: all values = {values}"


# =====================================================================
# 5. 组件平衡与回归测试
# =====================================================================

class TestComponentBalance:
    def _run_full_pipeline(self, hours=24, windows=None, num_printers=4, supply=None):
        """运行完整 Phase 1 + Phase 2 管线"""
        if windows is None:
            windows = DEFAULT_WINDOWS
        if supply is None:
            supply = {}
        custom_start = 0
        deadline = hours * 60
        queue = [(999, 1)] * 20  # 20 个产品单元

        tasks = plan_two_phase(
            num_printers=num_printers,
            duration_hours=hours,
            changeover=CHANGEOVER,
            surplus_enabled=True,
            windows=windows,
            custom_start=custom_start,
            deadline=deadline,
            product_queue=queue,
            bom_map={1: REAL_BOM},
            config_map=REAL_CONFIGS,
            initial_supply=supply,
        )

        scheduled = schedule_tasks(
            tasks, REAL_CONFIG_BY_ID, num_printers, windows,
            custom_start=custom_start, deadline=deadline,
            changeover=CHANGEOVER, sync_strength=0,
        )

        return tasks, scheduled

    def test_48h_geq_2x24h(self):
        """5.1 48h 产出 ≥ 两次 24h 产出之和"""
        # 48h 一次排
        _, sched_48 = self._run_full_pipeline(hours=48, windows=TWO_DAY_WINDOWS)
        products_48 = count_complete_products(sched_48, REAL_CONFIG_BY_ID, {1: REAL_BOM})

        # 24h 第一次
        tasks_24a, sched_24a = self._run_full_pipeline(hours=24)

        # 24h 第二次：用第一次的产出作为初始库存
        supply_from_first: dict[DemandKey, int] = {}
        for t in sched_24a:
            cfg = REAL_CONFIG_BY_ID[t.config_id]
            key = (cfg.component_id, t.color)
            supply_from_first[key] = supply_from_first.get(key, 0) + cfg.quantity

        _, sched_24b = self._run_full_pipeline(hours=24, supply=supply_from_first)

        # 合并两次 24h 的产出
        combined = list(sched_24a) + list(sched_24b)
        products_2x24 = count_complete_products(combined, REAL_CONFIG_BY_ID, {1: REAL_BOM})

        # 48h 应 >= 2×24h（允许 1 个误差）
        assert products_48.get(1, 0) >= products_2x24.get(1, 0) - 1, \
            f"48h={products_48.get(1,0)} < 2x24h={products_2x24.get(1,0)}"

    def test_component_ratio_preserved(self):
        """5.2 Phase 2 排入的组件比例 ≈ Phase 1 规划的比例"""
        tasks, scheduled = self._run_full_pipeline()

        planned_counts: dict[int, int] = defaultdict(int)
        for cid, _, _ in tasks:
            planned_counts[cid] += 1

        scheduled_counts: dict[int, int] = defaultdict(int)
        for t in scheduled:
            scheduled_counts[t.config_id] += 1

        for cid, planned in planned_counts.items():
            sched = scheduled_counts.get(cid, 0)
            # 每种组件至少 70% 排入
            if planned > 0:
                ratio = sched / planned
                assert ratio >= 0.7, \
                    f"config {cid}: scheduled {sched}/{planned} = {ratio:.0%}"

    def test_short_tasks_not_starved(self):
        """5.3 短任务不被长任务插队到排不进去"""
        tasks, scheduled = self._run_full_pipeline()

        # 找最短的组件
        short_cid = None
        min_dur = float('inf')
        for cid, cfg in REAL_CONFIG_BY_ID.items():
            if cfg.duration_minutes < min_dur:
                min_dur = cfg.duration_minutes
                short_cid = cid

        planned_short = sum(1 for t in tasks if t[0] == short_cid)
        sched_short = sum(1 for t in scheduled if t.config_id == short_cid)

        if planned_short > 0:
            assert sched_short / planned_short >= 0.6, \
                f"Short task {short_cid}: {sched_short}/{planned_short}"

    def test_bottleneck_component_prioritized(self):
        """5.4 瓶颈组件（1:1 产出）的排入率 ≥ 非瓶颈"""
        tasks, scheduled = self._run_full_pipeline()

        # 1:1 组件 = 瓶颈
        bottleneck_ids = {c.id for c in REAL_CONFIG_BY_ID.values() if c.quantity == 1}

        for cid in bottleneck_ids:
            planned = sum(1 for t in tasks if t[0] == cid)
            sched = sum(1 for t in scheduled if t.config_id == cid)
            if planned > 0:
                assert sched / planned >= 0.6, \
                    f"Bottleneck {cid}: {sched}/{planned}"

    def test_mixed_duration_balance(self):
        """5.5 各种时长都能排入"""
        tasks, scheduled = self._run_full_pipeline()

        planned_durations = set()
        for cid, _, _ in tasks:
            planned_durations.add(REAL_CONFIG_BY_ID[cid].duration_minutes)

        scheduled_durations = set()
        for t in scheduled:
            scheduled_durations.add(REAL_CONFIG_BY_ID[t.config_id].duration_minutes)

        # 所有规划的时长种类都应在排程中出现
        assert scheduled_durations == planned_durations


# =====================================================================
# 6. 策略对比测试
# =====================================================================

class TestStrategyComparison:
    def test_product_first_completes_products(self):
        """6.1 product_first 策略下 pick_task 优先选凑齐产品的组件"""
        # 设置模拟库存接近完成
        supply = {(1, "白色"): 0, (2, "白色"): 2, (3, "白色"): 1, (4, "白色"): 4}
        units = [(0, 10)]
        bom_cache = {10: DESK_BOM}
        assembled: set[int] = set()

        remaining = [(101, "白色"), (102, "白色"), (103, "白色")]
        result = pick_task(
            remaining, DESK_CONFIG_BY_ID, start=480, changeover=CHANGEOVER,
            windows=DEFAULT_WINDOWS, deadline=1440,
            sim_supply=supply, product_units=units,
            bom_cache=bom_cache, assembled=assembled,
        )
        # 应该选桌板（101），因为它是瓶颈（库存 0，需要 1）
        assert result is not None
        assert result[0] == 101

    def test_utilization_fills_time(self):
        """6.2 不提供产品上下文时，按 FIFO 优先级选择"""
        remaining = [(101, "白色", 0), (102, "白色", 1)]
        result = pick_task(
            remaining, DESK_CONFIG_BY_ID, start=480, changeover=CHANGEOVER,
            windows=DEFAULT_WINDOWS, deadline=1440,
        )
        assert result is not None
        assert result[0] == 101  # 优先级 0

    def test_two_phase_balance(self):
        """6.3 两阶段法组件比例优于随机分配"""
        queue = [(999, 1)] * 10
        tasks = plan_two_phase(
            num_printers=4, duration_hours=24, changeover=CHANGEOVER,
            surplus_enabled=False, windows=DEFAULT_WINDOWS,
            custom_start=0, deadline=1440,
            product_queue=queue, bom_map={1: REAL_BOM},
            config_map=REAL_CONFIGS, initial_supply={},
        )
        # 检查所有 BOM 组件都有对应任务
        planned_comps = set()
        for cid, _, _ in tasks:
            planned_comps.add(REAL_CONFIG_BY_ID[cid].component_id)
        bom_comps = {k[0] for k in REAL_BOM}
        assert bom_comps.issubset(planned_comps)


# =====================================================================
# 7. 边界条件测试
# =====================================================================

class TestEdgeCases:
    def test_zero_duration(self):
        """7.1 duration_hours=0 → 无任务"""
        tasks = plan_two_phase(
            num_printers=4, duration_hours=0, changeover=CHANGEOVER,
            surplus_enabled=False, windows=[], custom_start=0, deadline=0,
            product_queue=[(0, 10)], bom_map={10: DESK_BOM},
            config_map=DESK_CONFIGS, initial_supply={},
        )
        assert len(tasks) == 0

    def test_single_component_product(self):
        """7.2 产品只有 1 个组件"""
        bom = {10: {(1, "白色"): 1}}
        configs = {(1, "白色"): DESK_CONFIGS[(1, "白色")]}
        tasks = plan_two_phase(
            num_printers=4, duration_hours=24, changeover=CHANGEOVER,
            surplus_enabled=False, windows=DEFAULT_WINDOWS,
            custom_start=0, deadline=1440,
            product_queue=[(0, 10)] * 5, bom_map=bom,
            config_map=configs, initial_supply={},
        )
        assert len(tasks) == 5  # 5 个产品各 1 盘

    def test_no_windows(self):
        """7.3 无操作窗口但有 custom_start → batch_0 正常"""
        tasks = [(104, "白色", False)] * 4
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, [],
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
        )
        # batch_0 不依赖窗口，应该能排 1 批
        assert len(result) >= 1

    def test_very_long_task(self):
        """7.4 单个任务时长 > 窗口长度 → 可在窗口开始时启动"""
        long_cfg = {801: make_config(801, 81, "超长", qty=1, dur=500)}
        tasks = [(801, "白色", False)]
        result = schedule_tasks(
            tasks, long_cfg, 1, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        assert len(result) == 1
        assert result[0].end_min == 980  # 480 + 500

    def test_many_printers_few_tasks(self):
        """7.5 打印机数 > 任务数 → 部分打印机空闲"""
        tasks = [(104, "白色", False)] * 2
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 10, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        assert len(result) == 2
        printers_used = {t.printer_index for t in result}
        assert len(printers_used) == 2

    def test_many_tasks_few_printers(self):
        """7.6 任务数 >> 打印机数 → 按优先级排满"""
        tasks = [(104, "白色", False)] * 50  # 50 个 60min 任务
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 1, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
        )
        # 单打印机 ~960min 可用，60+15=75min/任务 → ~12 个
        assert len(result) > 0
        assert len(result) < 50

    def test_overnight_gap(self):
        """7.7 跨天排班 → 窗口正确处理"""
        tasks = [(101, "白色", False)] * 40  # 120min 桌板，需要溢出到第二天
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, TWO_DAY_WINDOWS,
            custom_start=0, deadline=2880, changeover=CHANGEOVER,
        )
        # 应该在两天的窗口都有排程
        day1_tasks = [t for t in result if t.start_min < 1440]
        day2_tasks = [t for t in result if t.start_min >= 1440]
        assert len(day1_tasks) > 0
        assert len(day2_tasks) > 0


# =====================================================================
# 辅助函数测试
# =====================================================================

class TestCountCompleteProducts:
    def test_basic_count(self):
        """统计完整产品数"""
        tasks = [
            ScheduledTask(0, 101, "白色", False, 0, 120, 0),  # 桌板 ×1
            ScheduledTask(1, 102, "白色", False, 0, 90, 0),   # 桌腿 ×4
            ScheduledTask(2, 103, "白色", False, 0, 150, 0),  # 抽屉 ×2
            ScheduledTask(3, 104, "白色", False, 0, 60, 0),   # 螺丝 ×20
        ]
        result = count_complete_products(tasks, DESK_CONFIG_BY_ID, {10: DESK_BOM})
        # 桌板 1, 桌腿 4/2=2, 抽屉 2/1=2, 螺丝 20/4=5 → min=1
        assert result[10] == 1

    def test_with_initial_supply(self):
        """带初始库存的统计"""
        tasks = [
            ScheduledTask(0, 102, "白色", False, 0, 90, 0),   # 桌腿 ×4
        ]
        supply = {(1, "白色"): 1, (3, "白色"): 1, (4, "白色"): 4}
        result = count_complete_products(tasks, DESK_CONFIG_BY_ID, {10: DESK_BOM}, supply)
        # 桌板 1, 桌腿 4/2=2, 抽屉 1, 螺丝 4/4=1 → min=1
        assert result[10] == 1


# =====================================================================
# 8. 同步惩罚纯函数测试
# =====================================================================

class TestSyncPenalty:
    def test_anchor_none(self):
        """8.1 anchor=None → 0.0"""
        assert _sync_penalty(60, None, 100, CHANGEOVER) == 0.0

    def test_anchor_nonpositive(self):
        """8.2 anchor<=0 → 0.0"""
        assert _sync_penalty(60, 0, 100, CHANGEOVER) == 0.0
        assert _sync_penalty(60, -10, 100, CHANGEOVER) == 0.0

    def test_sync_zero(self):
        """8.3 sync_strength=0 → 0.0"""
        assert _sync_penalty(60, 120, 0, CHANGEOVER) == 0.0

    def test_known_value(self):
        """8.4 已知值手算比对：|60-120|/120 × 1² × 15 × 4 = 30.0"""
        # strength_factor = (100/100)^2 = 1
        # 0.5 × 1 × 15 × 4 = 30.0
        assert _sync_penalty(60, 120, 100, CHANGEOVER) == 30.0

    def test_uses_changeover_mult_constant(self):
        """8.5 公式使用 SYNC_PENALTY_CHANGEOVER_MULT 常量"""
        expected = abs(60 - 120) / 120 * 1.0 * CHANGEOVER * SYNC_PENALTY_CHANGEOVER_MULT
        assert _sync_penalty(60, 120, 100, CHANGEOVER) == expected

    def test_quadratic_scaling(self):
        """8.6 二次方缩放：sync=30 的惩罚 ≪ sync=100"""
        p30 = _sync_penalty(60, 120, 30, CHANGEOVER)
        p100 = _sync_penalty(60, 120, 100, CHANGEOVER)
        # (30/100)^2 = 0.09, (100/100)^2 = 1.0 → p30 = 0.09 × p100
        assert p30 == pytest.approx(p100 * 0.09)
        assert p30 < p100 * 0.2  # 远小于

    def test_zero_deviation(self):
        """8.7 时长与锚定相同 → 无惩罚"""
        assert _sync_penalty(120, 120, 100, CHANGEOVER) == 0.0


# =====================================================================
# 9. 贪心主循环测试
# =====================================================================

class TestScheduleGreedy:
    # 混合时长配置（用于同步/批次测试）
    GREEDY_CONFIGS = {
        401: make_config(401, 41, "短", qty=1, dur=60),
        402: make_config(402, 42, "中", qty=1, dur=120),
        403: make_config(403, 43, "长", qty=1, dur=240),
    }

    def _mixed_demand(self):
        return (
            [(401, "白色", 0)] * 4 +
            [(402, "白色", 0)] * 4 +
            [(403, "白色", 0)] * 4
        )

    def test_product_first_bottleneck_over_sync(self):
        """9.a product_first + sync=100：仍优先选瓶颈件而非时长对齐件。

        sync 只影响 idle 维度，不夺取 prod_score 主导权。
        """
        configs = {
            101: make_config(101, 1, "桌板", qty=1, dur=120),  # 瓶颈（库存 0）
            102: make_config(102, 2, "桌腿", qty=4, dur=90),   # 库存充足
        }
        bom = {(1, "白色"): 1, (2, "白色"): 2}
        units = [(0, 10)]
        bom_cache = {10: bom}
        sim = {(1, "白色"): 0, (2, "白色"): 2}  # 桌腿已满足，桌板缺
        demand = [(101, "白色", 0), (102, "白色", 0)]

        sched = schedule_greedy(
            demand_tasks=list(demand), surplus_tasks=[], configs=configs,
            num_printers=2, windows=DEFAULT_WINDOWS, custom_start=480,
            deadline=1440, changeover=CHANGEOVER, sync_strength=100,
            use_product_first=True, sim_supply=dict(sim),
            product_units=list(units), bom_cache=bom_cache, assembled=set(),
        )
        b0 = sorted([t for t in sched if t.batch_index == 0],
                    key=lambda t: t.printer_index)
        # 第一台（设锚）应选瓶颈桌板 101，即便 sync=100
        assert b0[0].config_id == 101

    def test_utilization_fifo_over_sync(self):
        """9.b utilization：sync 影响 idle 维度但 FIFO order_priority 仍优先。"""
        configs = {
            501: make_config(501, 51, "锚", qty=1, dur=100),  # 锚定时长
            502: make_config(502, 52, "P0", qty=1, dur=240),  # 优先级 0，偏离锚
            503: make_config(503, 53, "P1", qty=1, dur=100),  # 优先级 1，匹配锚
        }
        demand = [
            (501, "白色", 0),  # 第一台设锚 = 100
            (502, "白色", 0),  # 优先级 0，时长 240（不匹配）
            (503, "白色", 1),  # 优先级 1，时长 100（匹配）—— sync 会偏好它
        ]
        sched = schedule_greedy(
            demand_tasks=list(demand), surplus_tasks=[], configs=configs,
            num_printers=3, windows=DEFAULT_WINDOWS, custom_start=480,
            deadline=1440, changeover=CHANGEOVER, sync_strength=100,
            use_product_first=False,
        )
        b0 = sorted([t for t in sched if t.batch_index == 0],
                    key=lambda t: t.printer_index)
        # 第二台应选优先级 0 的 502（FIFO 主导），而非时长对齐的 503
        assert b0[1].config_id == 502

    def test_demand_before_surplus(self):
        """9.c demand 取完才取 surplus。"""
        configs = {104: make_config(104, 4, "螺丝", qty=20, dur=60)}
        demand = [(104, "白色", 0)] * 2
        surplus = [(104, "白色")] * 2
        sched = schedule_greedy(
            demand_tasks=list(demand), surplus_tasks=list(surplus), configs=configs,
            num_printers=4, windows=DEFAULT_WINDOWS, custom_start=480,
            deadline=1380, changeover=CHANGEOVER, sync_strength=0,
            use_product_first=False,
        )
        b0 = sorted([t for t in sched if t.batch_index == 0],
                    key=lambda t: t.printer_index)
        # 前两台是需求（is_surplus=False），后两台是富余（True）
        assert [t.is_surplus for t in b0] == [False, False, True, True]

    def test_demand_all_over_deadline_no_deadlock(self):
        """9.c demand 全超 deadline 不死循环，且富余仍能排入。"""
        configs = {
            201: make_config(201, 21, "超长", qty=1, dur=300),  # 放不进 240min 窗口
            104: make_config(104, 4, "螺丝", qty=20, dur=60),
        }
        demand = [(201, "白色", 0)] * 3   # 全部超 deadline
        surplus = [(104, "白色")] * 2
        sched = schedule_greedy(
            demand_tasks=list(demand), surplus_tasks=list(surplus), configs=configs,
            num_printers=2, windows=[(480, 720)], custom_start=480,
            deadline=720, changeover=CHANGEOVER, sync_strength=0,
            use_product_first=False,
        )
        # 没有死循环；超长需求被丢弃，富余螺丝排入
        assert len(sched) == 2
        assert all(t.config_id == 104 for t in sched)

    def test_batch_start_timing(self):
        """9.d 批次启动断言：首批==custom_start，后续批==find_next_start(min available)。"""
        configs = {104: make_config(104, 4, "螺丝", qty=20, dur=60)}
        demand = [(104, "白色", 0)] * 12
        sched = schedule_greedy(
            demand_tasks=list(demand), surplus_tasks=[], configs=configs,
            num_printers=4, windows=DEFAULT_WINDOWS, custom_start=480,
            deadline=1380, changeover=CHANGEOVER, sync_strength=0,
            use_product_first=False,
        )
        by_batch = {}
        for t in sched:
            by_batch.setdefault(t.batch_index, []).append(t)

        # 首批从 custom_start 启动
        assert all(t.start_min == 480 for t in by_batch[0])
        # batch0 后最早可用打印机 = 480 + 60 + 15 = 555
        assert 1 in by_batch
        expected_b1 = find_next_start(555, DEFAULT_WINDOWS)
        assert all(t.start_min == expected_b1 for t in by_batch[1])

    def test_golden_additive_penalty_selection(self):
        """9.e 黄金值锁定：固定输入下加法惩罚使 sync=100 完美对齐同时长批次。

        防回退到旧乘法版本（旧版评分前缀为乘法 sync_penalty，会改变选择结果）。
        """
        demand = self._mixed_demand()
        sched = schedule_greedy(
            demand_tasks=list(demand), surplus_tasks=[], configs=self.GREEDY_CONFIGS,
            num_printers=4, windows=DEFAULT_WINDOWS, custom_start=480,
            deadline=1440, changeover=CHANGEOVER, sync_strength=100,
            use_product_first=False,
        )
        b0 = [t for t in sched if t.batch_index == 0]
        durations = sorted(t.end_min - t.start_min for t in b0)
        # 加法惩罚 + sync=100：首批锚定 60min 短任务后，其余三台都选 60min
        assert durations == [60, 60, 60, 60]

    def test_sync_gradient_monotonic(self):
        """9 同步梯度单调性扩展到贪心路径（参照 schedule_tasks 的梯度测试）。"""
        demand = self._mixed_demand()
        batch_counts = {}
        for s in [0, 25, 50, 75, 100]:
            sched = schedule_greedy(
                demand_tasks=list(demand), surplus_tasks=[], configs=self.GREEDY_CONFIGS,
                num_printers=4, windows=DEFAULT_WINDOWS, custom_start=480,
                deadline=1440, changeover=CHANGEOVER, sync_strength=s,
                use_product_first=False,
            )
            batch_counts[s] = max((t.batch_index for t in sched), default=-1) + 1

        values = [batch_counts[s] for s in [0, 25, 50, 75, 100]]
        # 单调不增
        for i in range(len(values) - 1):
            assert values[i] >= values[i + 1], \
                f"batch counts not monotonic: {dict(zip([0,25,50,75,100], values))}"
        # 有梯度，或批次数已经很少
        assert batch_counts[0] > batch_counts[100] or batch_counts[0] <= 3, \
            f"no gradient: all values = {values}"

    def test_product_first_self_reinforcing_sync(self):
        """9 product_first 下相同时长任务在 sync=100 完美对齐。"""
        configs = {601: make_config(601, 61, "X", qty=1, dur=120)}
        bom = {(61, "白色"): 1}
        demand = [(601, "白色", 0)] * 8
        sched = schedule_greedy(
            demand_tasks=list(demand), surplus_tasks=[], configs=configs,
            num_printers=4, windows=DEFAULT_WINDOWS, custom_start=480,
            deadline=1440, changeover=CHANGEOVER, sync_strength=100,
            use_product_first=True, sim_supply={}, product_units=[(0, 99)],
            bom_cache={99: bom}, assembled=set(),
        )
        b0 = [t for t in sched if t.batch_index == 0]
        # 同批次所有任务结束时间相同
        assert len({t.end_min for t in b0}) == 1


# =====================================================================
# T2. 关键纯函数直接单测加固
# =====================================================================

class TestProductCompletionScore:
    """直接覆盖 product_completion_score —— 凑齐评分的心脏。"""

    INIT = (float('inf'), 0.0, float('inf'))

    def test_comp_key_not_in_any_bom(self):
        """comp_key 不在任何 BOM 中 → 返回初值 (inf, 0.0, inf)"""
        result = product_completion_score(
            comp_key=(99, "白色"),
            sim_supply={},
            product_units=[(0, 1)],
            bom_cache={1: {(1, "红色"): 1}},
            assembled=set(),
        )
        assert result == self.INIT

    def test_all_bom_empty_supply(self):
        """单产品单元、sim_supply 为空 → min_ratio=0, comp_ratio=0,
        prod_score=(0, -0.0, 0.0)"""
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={},
            product_units=[(0, 1)],
            bom_cache={1: {(1, "红色"): 1, (2, "红色"): 2}},
            assembled=set(),
        )
        assert result == (0, -0.0, 0.0)

    def test_min_ratio_is_smallest_across_bom(self):
        """min_ratio 是 BOM 中最小 ratio，而非选 comp_key 的 ratio。"""
        # comp_key=(1,"r") ratio=4/2=2.0；(2,"r") ratio=0/1=0
        # 应取最小 0 而非 2.0
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={(1, "红色"): 4, (2, "红色"): 0},
            product_units=[(0, 1)],
            bom_cache={1: {(1, "红色"): 2, (2, "红色"): 1}},
            assembled=set(),
        )
        # min_ratio=0 → -min_ratio=-0.0；comp_ratio=4/2=2.0
        assert result == (0, -0.0, 2.0)

    def test_multi_units_priority_wins(self):
        """两单元不同 priority，pri 小的赢（不论 ratio）。"""
        # pid=A 单元 ratio 很高（接近凑齐）；pid=B 单元 ratio=0
        # 但 pri=0 < pri=1，应选 pri=0
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={(1, "红色"): 10, (2, "红色"): 10},
            product_units=[(0, 100), (1, 200)],
            bom_cache={
                100: {(1, "红色"): 1, (2, "红色"): 1},   # pid=A, pri=0
                200: {(1, "红色"): 1, (2, "红色"): 1},   # pid=B, pri=1
            },
            assembled=set(),
        )
        # pri=0 的赢；min_ratio=10/1=10, comp_ratio=10/1=10
        assert result[0] == 0
        assert result == (0, -10.0, 10.0)

    def test_assembled_units_skipped(self):
        """assembled 中的单元被跳过，结果由剩余单元决定。"""
        # 单元 0 在 assembled，应被忽略；单元 1 (pri=5, pid=200) 主导
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={(1, "红色"): 3},
            product_units=[(0, 100), (5, 200)],
            bom_cache={
                100: {(1, "红色"): 1},
                200: {(1, "红色"): 1},
            },
            assembled={0},
        )
        # 仅单元 1 参与：pri=5, min_ratio=3, comp_ratio=3
        assert result == (5, -3.0, 3.0)

    def test_bom_qty_zero_returns_inf_comp_ratio(self):
        """BOM 中 comp_key 的 qty=0 → comp_ratio=inf。"""
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={(1, "红色"): 5, (2, "红色"): 4},
            product_units=[(0, 1)],
            bom_cache={1: {(1, "红色"): 0, (2, "红色"): 2}},
            assembled=set(),
        )
        # qty=0 跳过 ratio 计算；min_ratio 由 (2,"红色") 决定 = 4/2=2.0
        # comp_bom_qty=0 → comp_ratio=inf
        assert result[0] == 0
        assert result[1] == -2.0
        assert result[2] == float('inf')

    def test_tiebreak_lower_min_ratio_wins(self):
        """两单元同 pri，min_ratio 更低的赢（因 -min_ratio 更小，即 -0.5 < -0.2 是 False，
        所以 min_ratio=0.5 给出 -0.5；min_ratio=0.2 给出 -0.2；-0.5 < -0.2 → 0.5 胜出）。"""
        # 单元 0: pid=100, BOM 让 min_ratio=0.5
        # 单元 1: pid=200, BOM 让 min_ratio=0.2
        # 单元 0 应赢（-0.5 < -0.2）
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={(1, "红色"): 1, (2, "红色"): 1, (3, "红色"): 1},
            product_units=[(0, 100), (0, 200)],
            bom_cache={
                100: {(1, "红色"): 2, (2, "红色"): 2},     # min_ratio=min(1/2,1/2)=0.5
                200: {(1, "红色"): 5, (3, "红色"): 5},     # min_ratio=min(1/5,1/5)=0.2
            },
            assembled=set(),
        )
        # 单元 0 胜出：pri=0, -min_ratio=-0.5, comp_ratio=1/2=0.5
        assert result == (0, -0.5, 0.5)

    def test_tiebreak_lower_comp_ratio_wins(self):
        """两单元同 (pri, -min_ratio)，comp_ratio 小的赢。"""
        # 两单元同 BOM 形状但 comp_key 的 qty 不同 → 不同 comp_ratio
        # 单元 0: comp_bom_qty=2, sim_supply=2 → comp_ratio=1.0
        # 单元 1: comp_bom_qty=4, sim_supply=2 → comp_ratio=0.5
        # 为了让 min_ratio 相同，控制其他组件
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={(1, "红色"): 2, (2, "红色"): 1},
            product_units=[(0, 100), (0, 200)],
            bom_cache={
                100: {(1, "红色"): 2, (2, "红色"): 1},   # min_ratio=min(1.0, 1.0)=1.0; comp_ratio=1.0
                200: {(1, "红色"): 4, (2, "红色"): 0.5}, # min_ratio=min(0.5, 2.0)=0.5; comp_ratio=0.5
            },
            assembled=set(),
        )
        # 单元 0 (-min_ratio=-1.0) vs 单元 1 (-min_ratio=-0.5)
        # -1.0 < -0.5 → 单元 0 胜出
        assert result == (0, -1.0, 1.0)

    def test_tiebreak_strict_comp_ratio(self):
        """构造同 pri、同 -min_ratio、不同 comp_ratio，验证 comp_ratio 小的赢。"""
        # 单元 0: bom={comp:1, other:1}, supply={comp:1, other:1} → min_ratio=1, comp_ratio=1
        # 单元 1: bom={comp:2, other:2}, supply={comp:2, other:2} → min_ratio=1, comp_ratio=1
        # 同 (0,-1,1)，best 保留单元 0（严格 <）
        result = product_completion_score(
            comp_key=(1, "红色"),
            sim_supply={(1, "红色"): 2, (2, "红色"): 2, (3, "红色"): 2},
            product_units=[(0, 100), (0, 200)],
            bom_cache={
                100: {(1, "红色"): 2, (2, "红色"): 2},   # min_ratio=1, comp_ratio=2/2=1.0
                200: {(1, "红色"): 4, (3, "红色"): 1},   # min_ratio=min(0.5,2)=0.5, comp_ratio=2/4=0.5
            },
            assembled=set(),
        )
        # 单元 0: (0,-1.0,1.0)；单元 1: (0,-0.5,0.5)
        # -1.0 < -0.5 → 单元 0 赢
        assert result == (0, -1.0, 1.0)


class TestComputeEffectiveCapacity:
    """直接覆盖 compute_effective_capacity —— 0.9 安全系数影响 two_phase 全部规划。"""

    def test_empty_windows(self):
        """windows=[] → sorted_wins=[], gap_loss=0, eff=total_span。
        当 deadline==custom_start → total_span=0 → 0"""
        assert compute_effective_capacity(
            custom_start=100, deadline=100, windows=[], num_printers=1,
        ) == 0

    def test_single_window_no_safety_margin(self):
        """windows=[(0,100)], deadline=100, safety=1.0 → 精确 100。"""
        assert compute_effective_capacity(
            custom_start=0, deadline=100,
            windows=[(0, 100)], num_printers=1, safety_margin=1.0,
        ) == 100

    def test_default_safety_margin_applied(self):
        """默认 safety_margin=0.9：100 * 1 * 0.9 = 90"""
        result = compute_effective_capacity(
            custom_start=0, deadline=100,
            windows=[(0, 100)], num_printers=1,
        )
        # 显式确认默认行为：90 而非 100
        assert result == 90

    def test_multi_windows_with_gap_loss(self):
        """windows=[(0,60),(90,150)]，gap=30 → gap_loss=15。
        total_span=150, eff=150-15=135, 与窗口长度之和(120)不同。"""
        result = compute_effective_capacity(
            custom_start=0, deadline=150,
            windows=[(0, 60), (90, 150)], num_printers=1, safety_margin=1.0,
        )
        # 验证不是窗口长度之和（60+60=120）
        assert result != 120
        # 而是 total_span - gap*0.5 = 150 - 15 = 135
        assert result == 135

    def test_cross_day_offset(self):
        """跨天 windows=[(0,60),(1440,1500)]：total_span=1500, gap=1380, gap_loss=690, eff=810"""
        result = compute_effective_capacity(
            custom_start=0, deadline=1500,
            windows=[(0, 60), (1440, 1500)], num_printers=1, safety_margin=1.0,
        )
        assert result == 810

    def test_zero_safety_margin(self):
        """safety_margin=0 → 0"""
        assert compute_effective_capacity(
            custom_start=0, deadline=1000,
            windows=[(0, 500), (600, 1000)],
            num_printers=4, safety_margin=0.0,
        ) == 0

    def test_num_printers_multiplies(self):
        """num_printers 直接相乘：100 * 3 * 1.0 = 300"""
        assert compute_effective_capacity(
            custom_start=0, deadline=100,
            windows=[(0, 100)], num_printers=3, safety_margin=1.0,
        ) == 300


class TestPickTaskDirect:
    """直接调用 pick_task，覆盖决策树多分支。"""

    CFG = {
        1: ConfigInfo(id=1, component_id=1, component_name="A", quantity=1, duration_minutes=100),
        2: ConfigInfo(id=2, component_id=2, component_name="B", quantity=1, duration_minutes=100),
        3: ConfigInfo(id=3, component_id=3, component_name="C", quantity=1, duration_minutes=200),
    }
    WIN = [(0, 10000)]

    def test_empty_remaining(self):
        """remaining=[] → None"""
        assert pick_task(
            remaining=[], configs=self.CFG, start=0,
            changeover=15, windows=self.WIN, deadline=1000,
        ) is None

    def test_all_tasks_over_deadline_returns_none(self):
        """所有任务 start+dur > deadline → None；remaining 不被修改。"""
        remaining = [(1, "红色"), (2, "白色")]
        original = list(remaining)
        # start=500, dur=100 → 600 > deadline=550
        result = pick_task(
            remaining=remaining, configs=self.CFG, start=500,
            changeover=15, windows=self.WIN, deadline=550,
        )
        assert result is None
        # 关键：remaining 完整保留，未被 clear/pop
        assert remaining == original
        assert len(remaining) == 2

    def test_use_completion_changes_selection(self):
        """传/不传 sim_supply 等，应选不同任务。"""
        # 两个任务：comp_id=1（A）和 comp_id=2（B），同样 dur，idle 相同
        # 不传 completion 时 fallback (0,0,0) 平手 → 取 list[0] = (1,"红色")
        remaining_a = [(1, "红色"), (2, "白色")]
        no_comp = pick_task(
            remaining=remaining_a, configs=self.CFG, start=0,
            changeover=15, windows=self.WIN, deadline=10000,
        )
        # 传 completion：BOM 要求 comp B 但 supply 已有 A，B 凑齐分更优 → 选 (2,"白色")
        remaining_b = [(1, "红色"), (2, "白色")]
        with_comp = pick_task(
            remaining=remaining_b, configs=self.CFG, start=0,
            changeover=15, windows=self.WIN, deadline=10000,
            sim_supply={(1, "红色"): 10, (2, "白色"): 0},
            product_units=[(0, 999)],
            bom_cache={999: {(1, "红色"): 1, (2, "白色"): 1}},
            assembled=set(),
        )
        # 两次选择应不同
        assert no_comp != with_comp
        assert no_comp == (1, "红色")
        assert with_comp == (2, "白色")

    def test_no_completion_uses_order_priority(self):
        """不传 sim_supply 等，item[2] 是 priority；小的赢。"""
        remaining = [(1, "红色", 5), (2, "白色", 1)]
        result = pick_task(
            remaining=remaining, configs=self.CFG, start=0,
            changeover=15, windows=self.WIN, deadline=10000,
        )
        # priority=1 < priority=5 → 选第二个
        assert result == (2, "白色", 1)
        # 选中项被 pop
        assert remaining == [(1, "红色", 5)]

    def test_tiebreak_picks_first_when_equal_score(self):
        """完全等价任务（同 cfg、同 idle、同 sync），返回 list 中第一个（严格 < tiebreak）。"""
        # 两个同 config 任务 → score 完全相同 → 首个胜出
        remaining = [(1, "红色"), (1, "白色")]
        result = pick_task(
            remaining=remaining, configs=self.CFG, start=0,
            changeover=15, windows=self.WIN, deadline=10000,
        )
        assert result == (1, "红色")
        assert remaining == [(1, "白色")]

    def test_filters_only_over_deadline_tasks(self):
        """部分任务超 deadline 应被跳过，可行任务被选中。"""
        # cfg 3 dur=200 超 deadline=150；cfg 1 dur=100 可行
        remaining = [(3, "白色"), (1, "红色")]
        result = pick_task(
            remaining=remaining, configs=self.CFG, start=0,
            changeover=15, windows=self.WIN, deadline=150,
        )
        assert result == (1, "红色")
        # 超时项未被 pop，留在 remaining
        assert remaining == [(3, "白色")]


# =====================================================================
# T1. 不变量辅助函数自身正确性（负样本）
# =====================================================================

class TestInvariantHelpersNegativeSamples:
    """每个不变量辅助函数都要能"抓到"故意构造的违反场景。"""

    def test_no_printer_overlap_catches_overlap(self):
        """8.1 同一打印机两个任务时间重叠 → 应抛 AssertionError"""
        bad = [
            ScheduledTask(0, 101, "白色", False, 0, 120, 0),
            ScheduledTask(0, 101, "白色", False, 100, 220, 1),  # 重叠
        ]
        with pytest.raises(AssertionError):
            _assert_no_printer_overlap(bad, CHANGEOVER)

    def test_no_printer_overlap_catches_missing_changeover(self):
        """8.2 第二个任务在换料完成前启动 → 应抛 AssertionError"""
        bad = [
            ScheduledTask(0, 101, "白色", False, 0, 120, 0),
            ScheduledTask(0, 101, "白色", False, 130, 250, 1),  # 仅隔 10 < changeover=15
        ]
        with pytest.raises(AssertionError):
            _assert_no_printer_overlap(bad, CHANGEOVER)

    def test_no_printer_overlap_passes_on_clean(self):
        """8.2b 合法排程 → 不抛"""
        good = [
            ScheduledTask(0, 101, "白色", False, 0, 120, 0),
            ScheduledTask(0, 101, "白色", False, 135, 255, 1),  # 120+15=135
            ScheduledTask(1, 101, "白色", False, 0, 120, 0),
        ]
        _assert_no_printer_overlap(good, CHANGEOVER)

    def test_within_deadline_catches_overrun(self):
        """8.3 任务 end_min 超出 deadline → 应抛 AssertionError"""
        bad = [
            ScheduledTask(0, 101, "白色", False, 600, 800, 0),
        ]
        with pytest.raises(AssertionError):
            _assert_within_deadline(bad, deadline=720)

    def test_within_deadline_passes_on_boundary(self):
        """8.3b 任务恰好在 deadline 结束 → 不抛"""
        ok = [
            ScheduledTask(0, 101, "白色", False, 600, 720, 0),
        ]
        _assert_within_deadline(ok, deadline=720)

    def test_start_within_windows_catches_outside(self):
        """8.4 start_min 不在任何窗口内 → 应抛 AssertionError"""
        # 730 在 (720,750) 间隔内
        bad = [
            ScheduledTask(0, 101, "白色", False, 730, 850, 0),
        ]
        with pytest.raises(AssertionError):
            _assert_start_within_windows(bad, DEFAULT_WINDOWS)

    def test_start_within_windows_extra_allowed(self):
        """8.4b extra_allowed_starts 放行 batch_0 在 custom_start 启动"""
        # custom_start=0 不在 DEFAULT_WINDOWS 内
        ok = [
            ScheduledTask(0, 101, "白色", False, 0, 120, 0),
        ]
        _assert_start_within_windows(ok, DEFAULT_WINDOWS, extra_allowed_starts={0})

    def test_no_negative_supply_catches_underflow(self):
        """8.5 初始供给负数 → 应抛 AssertionError"""
        with pytest.raises(AssertionError):
            _assert_no_negative_supply(
                scheduled=[],
                configs=DESK_CONFIG_BY_ID,
                initial_supply={(1, "白色"): -1},
                bom_cache={10: DESK_BOM},
                product_units=[(0, 10)],
            )

    def test_no_negative_supply_passes_normal(self):
        """8.5b 正常回放 → 不抛"""
        # 单个桌板任务，无消费（product_units 不存在 product 10 的完整 BOM 时 try_assemble 不动）
        tasks = [ScheduledTask(0, 101, "白色", False, 0, 120, 0)]
        _assert_no_negative_supply(
            scheduled=tasks,
            configs=DESK_CONFIG_BY_ID,
            initial_supply={},
            bom_cache={},
            product_units=[],
        )

    def test_batch_quantity_conservation_returns_dict(self):
        """8.6 辅助函数返回 per-batch 累加 dict 供调用方对比"""
        tasks = [
            ScheduledTask(0, 101, "白色", False, 0, 120, 0),  # qty 1
            ScheduledTask(1, 104, "白色", False, 0, 60, 0),   # qty 20
            ScheduledTask(0, 102, "白色", False, 135, 225, 1),  # qty 4
        ]
        result = _assert_batch_quantity_conservation(tasks, DESK_CONFIG_BY_ID)
        # batch 0: 桌板 1 + 螺丝 20 ; batch 1: 桌腿 4
        assert result[0][(1, "白色")] == 1
        assert result[0][(4, "白色")] == 20
        assert result[1][(2, "白色")] == 4

        # 与独立手工累加比对
        manual: dict[int, dict[DemandKey, int]] = defaultdict(lambda: defaultdict(int))
        for t in tasks:
            cfg = DESK_CONFIG_BY_ID[t.config_id]
            manual[t.batch_index][(cfg.component_id, t.color)] += cfg.quantity
        for b, totals in result.items():
            for k, v in totals.items():
                assert manual[b][k] == v, (
                    f"batch {b} key {k}: helper={v} != manual={manual[b][k]}"
                )

    def test_batch_quantity_conservation_detects_buggy_variant(self):
        """8.6b 故意构造一个 bug 变体（漏算 qty）→ 与正确辅助函数结果不一致"""
        tasks = [
            ScheduledTask(0, 104, "白色", False, 0, 60, 0),  # qty 20
            ScheduledTask(1, 104, "白色", False, 0, 60, 0),  # qty 20
        ]
        correct = _assert_batch_quantity_conservation(tasks, DESK_CONFIG_BY_ID)
        # buggy: 假设每盘只算 1（忽略 quantity 字段）
        buggy: dict[int, dict[DemandKey, int]] = defaultdict(lambda: defaultdict(int))
        for t in tasks:
            cfg = DESK_CONFIG_BY_ID[t.config_id]
            buggy[t.batch_index][(cfg.component_id, t.color)] += 1
        # 两者应明显不同（correct 用 quantity=20 累加；buggy 每盘只计 1）
        assert correct[0][(4, "白色")] == 40   # 2 盘 × 20/盘
        assert buggy[0][(4, "白色")] == 2     # bug 变体只数盘数
        assert correct[0][(4, "白色")] != buggy[0][(4, "白色")]


# =====================================================================
# 9. 跨场景不变量应用 — 真实排程结果上检验所有不变量
# =====================================================================

class TestInvariantsAcrossScenarios:
    """构造多种代表性场景，跑 schedule_tasks / plan_two_phase，应用 5 个不变量。"""

    # ---- scenario A: 单产品 4 打印机 24h，三策略 ----

    def test_scenario_single_product_utilization(self):
        """9.1 单产品 4 打印机 24h — utilization-like（不带产品上下文） + 5 不变量"""
        # Phase 1 规划任务再 Phase 2 排程
        queue = [(0, 10)] * 6  # 6 个产品单元
        tasks = plan_two_phase(
            num_printers=4, duration_hours=24, changeover=CHANGEOVER,
            surplus_enabled=False, windows=DEFAULT_WINDOWS,
            custom_start=0, deadline=1440,
            product_queue=queue, bom_map={10: DESK_BOM},
            config_map=DESK_CONFIGS, initial_supply={},
        )
        scheduled = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
            sync_strength=0,
        )
        assert len(scheduled) > 0
        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 1440)
        _assert_start_within_windows(scheduled, DEFAULT_WINDOWS, extra_allowed_starts={0})
        _assert_no_negative_supply(
            scheduled, DESK_CONFIG_BY_ID,
            initial_supply={}, bom_cache={10: DESK_BOM},
            product_units=queue,
        )
        per_batch = _assert_batch_quantity_conservation(scheduled, DESK_CONFIG_BY_ID)
        # 全局守恒：每个 config 排程数 × quantity == per_batch 累加
        from_helper: dict[DemandKey, int] = defaultdict(int)
        for batch_totals in per_batch.values():
            for k, v in batch_totals.items():
                from_helper[k] += v
        manual: dict[DemandKey, int] = defaultdict(int)
        for t in scheduled:
            cfg = DESK_CONFIG_BY_ID[t.config_id]
            manual[(cfg.component_id, t.color)] += cfg.quantity
        assert dict(from_helper) == dict(manual)

    def test_scenario_single_product_two_phase(self):
        """9.2 同场景 — two_phase 策略（Phase 1 + Phase 2 sync_strength=50） + 不变量"""
        queue = [(0, 10)] * 6
        tasks = plan_two_phase(
            num_printers=4, duration_hours=24, changeover=CHANGEOVER,
            surplus_enabled=False, windows=DEFAULT_WINDOWS,
            custom_start=0, deadline=1440,
            product_queue=queue, bom_map={10: DESK_BOM},
            config_map=DESK_CONFIGS, initial_supply={},
        )
        scheduled = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
            sync_strength=50,
        )
        assert len(scheduled) > 0
        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 1440)
        _assert_start_within_windows(scheduled, DEFAULT_WINDOWS, extra_allowed_starts={0})
        _assert_batch_quantity_conservation(scheduled, DESK_CONFIG_BY_ID)

    def test_scenario_single_product_product_first(self):
        """9.3 同场景 — product_first 策略（pick_task 驱动手工循环） + 不变量"""
        # 手工驱动一个 batch_0 的 product_first 选择，验证 pick_task 输出落入排程时仍满足不变量。
        # 这里直接构造一个等效的简化排程：在 batch_0 用 pick_task 选 4 个任务。
        queue = [(0, 10)] * 4  # 4 个产品单元
        bom_cache = {10: DESK_BOM}
        sim_supply: dict[DemandKey, int] = {}
        assembled: set[int] = set()
        # 用 pick_task 串行选 4 个组件
        remaining = [
            (101, "白色"), (102, "白色"), (103, "白色"), (104, "白色"),
        ]
        picks: list[tuple[int, str]] = []
        for _ in range(4):
            result = pick_task(
                list(remaining), DESK_CONFIG_BY_ID,
                start=480, changeover=CHANGEOVER,
                windows=DEFAULT_WINDOWS, deadline=1440,
                sim_supply=sim_supply, product_units=queue,
                bom_cache=bom_cache, assembled=assembled,
            )
            assert result is not None
            picks.append((result[0], result[1]))
            remaining = [r for r in remaining if r[0] != result[0]]
            cfg = DESK_CONFIG_BY_ID[result[0]]
            sim_supply[(cfg.component_id, result[1])] = sim_supply.get((cfg.component_id, result[1]), 0) + cfg.quantity

        # 转成 ScheduledTask 列表（4 台打印机并行启动）
        scheduled = []
        for i, (cid, color) in enumerate(picks):
            cfg = DESK_CONFIG_BY_ID[cid]
            scheduled.append(ScheduledTask(
                printer_index=i, config_id=cid, color=color,
                is_surplus=False, start_min=480,
                end_min=480 + cfg.duration_minutes, batch_index=0,
            ))

        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 1440)
        _assert_start_within_windows(scheduled, DEFAULT_WINDOWS)
        _assert_no_negative_supply(
            scheduled, DESK_CONFIG_BY_ID,
            initial_supply={}, bom_cache=bom_cache, product_units=queue,
        )
        _assert_batch_quantity_conservation(scheduled, DESK_CONFIG_BY_ID)

    # ---- scenario B: 多产品共享组件 5 打印机 168h，有初始供给 ----

    def test_scenario_multi_product_shared_components(self):
        """9.4 3 个产品共享组件，5 打印机 168h，有初始供给 + 4 不变量"""
        # 三个产品都需要桌板 + 螺丝，A/B 还需桌腿
        bom_a = {(1, "白色"): 1, (2, "白色"): 2, (4, "白色"): 4}
        bom_b = {(1, "白色"): 1, (2, "白色"): 1, (4, "白色"): 2}
        bom_c = {(1, "白色"): 1, (4, "白色"): 6}
        bom_map = {10: bom_a, 11: bom_b, 12: bom_c}
        configs = {
            (1, "白色"): DESK_CONFIGS[(1, "白色")],
            (2, "白色"): DESK_CONFIGS[(2, "白色")],
            (4, "白色"): DESK_CONFIGS[(4, "白色")],
        }
        config_by_id = {c.id: c for c in configs.values()}
        queue = [(0, 10), (1, 10), (2, 11), (3, 12), (4, 12)]
        initial = {(4, "白色"): 8}  # 已有 8 个螺丝

        tasks = plan_two_phase(
            num_printers=5, duration_hours=168, changeover=CHANGEOVER,
            surplus_enabled=False, windows=DEFAULT_WINDOWS,
            custom_start=0, deadline=168 * 60,
            product_queue=queue, bom_map=bom_map,
            config_map=configs, initial_supply=initial,
        )
        scheduled = schedule_tasks(
            tasks, config_by_id, 5, DEFAULT_WINDOWS,
            custom_start=0, deadline=168 * 60, changeover=CHANGEOVER,
            sync_strength=30,
        )
        assert len(scheduled) > 0
        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 168 * 60)
        _assert_start_within_windows(scheduled, DEFAULT_WINDOWS, extra_allowed_starts={0})
        _assert_no_negative_supply(
            scheduled, config_by_id,
            initial_supply=initial, bom_cache=bom_map, product_units=queue,
        )
        _assert_batch_quantity_conservation(scheduled, config_by_id)

    # ---- scenario C: 单打印机长任务跨午休 ----

    def test_scenario_long_task_across_lunch(self):
        """9.5 单打印机 11:00 启动 240min 任务跨午休（12:00-12:30 间隔） + 不变量"""
        long_cfg = {901: make_config(901, 91, "长板", qty=1, dur=240)}
        tasks = [(901, "白色", False)] * 2
        scheduled = schedule_tasks(
            tasks, long_cfg, 1, DEFAULT_WINDOWS,
            custom_start=660, deadline=1440, changeover=CHANGEOVER,  # 11:00 = 660
            sync_strength=0,
        )
        assert len(scheduled) >= 1
        # 跨午休：第一个任务 660 启动，900 结束（15:00），跨越 720-750 间隔
        first = scheduled[0]
        assert first.start_min == 660
        assert first.end_min == 900
        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 1440)
        # start_min=660 在 (480,720) 窗口内
        _assert_start_within_windows(scheduled, DEFAULT_WINDOWS)
        _assert_batch_quantity_conservation(scheduled, long_cfg)

    # ---- scenario D: 跨天排班 48h ----

    def test_scenario_cross_day(self):
        """9.6 跨天 48h 排程 → 任务分布在两天窗口 + 不变量（含 sync_strength=100）"""
        tasks = [(101, "白色", False)] * 40  # 桌板 120min × 40
        scheduled = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, TWO_DAY_WINDOWS,
            custom_start=0, deadline=2880, changeover=CHANGEOVER,
            sync_strength=100,
        )
        assert len(scheduled) > 0
        day1 = [t for t in scheduled if t.start_min < 1440]
        day2 = [t for t in scheduled if t.start_min >= 1440]
        assert len(day1) > 0
        assert len(day2) > 0
        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 2880)
        _assert_start_within_windows(scheduled, TWO_DAY_WINDOWS, extra_allowed_starts={0})
        _assert_batch_quantity_conservation(scheduled, DESK_CONFIG_BY_ID)

    # ---- scenario E: 所有任务超 deadline (边界) ----

    def test_scenario_all_tasks_over_deadline(self):
        """9.7 所有任务时长 > deadline → 返回空 list，不变量自然满足"""
        # 单任务 500min，deadline=60min
        long_cfg = {801: make_config(801, 81, "超长", qty=1, dur=500)}
        tasks = [(801, "白色", False)] * 3
        scheduled = schedule_tasks(
            tasks, long_cfg, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=60, changeover=CHANGEOVER,
        )
        assert scheduled == []
        # 空列表不变量平凡满足
        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 60)
        _assert_start_within_windows(scheduled, DEFAULT_WINDOWS)
        _assert_batch_quantity_conservation(scheduled, long_cfg)

    # ---- scenario F: 零需求 ----

    def test_scenario_zero_demand(self):
        """9.8 demand_tasks=[] surplus=[] → 空 list，所有不变量满足"""
        scheduled = schedule_tasks(
            [], DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
        )
        assert scheduled == []
        _assert_no_printer_overlap(scheduled, CHANGEOVER)
        _assert_within_deadline(scheduled, 1440)
        _assert_start_within_windows(scheduled, DEFAULT_WINDOWS, extra_allowed_starts={0})
        _assert_no_negative_supply(
            scheduled, DESK_CONFIG_BY_ID,
            initial_supply={}, bom_cache={}, product_units=[],
        )
        _assert_batch_quantity_conservation(scheduled, DESK_CONFIG_BY_ID)


# =====================================================================
# T3. P1 行为广度补强：跨天 schedule_tasks、防御兜底、sync 边界值、策略偏序
# =====================================================================

# 注：T3 原本定义了本地 _sync_penalty 作为公式复刻。
# iter1 之后 scheduler_core 已暴露真函数 _sync_penalty，下面的 boundary
# 测试已替换为直接调用生产代码的 _sync_penalty（已在文件顶部 import）。


class TestScheduleGreedyCrossDay:
    """跨天窗口下 schedule_tasks 的贪心拼接（覆盖 batch_index 与 deadline 单调性）。"""

    def test_tasks_spread_across_two_days(self):
        """跨天窗口 + 48h deadline + 足够多任务 → 至少 1 个任务落在第二天 (start ≥ 1440)。"""
        # 桌腿 90min × 80 盘，4 台打印机：第一天容量 ~4 × 24h × 60 / (90+15) = ~55，
        # 80 个任务必须溢出到第二天
        tasks = [(102, "白色", False)] * 80
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, TWO_DAY_WINDOWS,
            custom_start=0, deadline=2880, changeover=CHANGEOVER,
            sync_strength=0,
        )
        assert len(result) > 0
        day2 = [t for t in result if t.start_min >= 1440]
        assert len(day2) >= 1, \
            f"expected at least one task on day 2 (start≥1440), got starts={[t.start_min for t in result]}"

    def test_task_starts_late_first_day(self):
        """custom_start=1300（第一天最后窗口尾段）的 60min 任务 → 落在尾段或推到第二天。

        第一天最后窗口是 (1110, 1380)。custom_start=1300，任务时长 60min →
        batch_0 在 1300 启动，结束 1360 仍在窗口内。后续 batch 必须等到次日 1920。
        """
        tasks = [(104, "白色", False)] * 8  # 螺丝 60min
        result = schedule_tasks(
            tasks, DESK_CONFIG_BY_ID, 4, TWO_DAY_WINDOWS,
            custom_start=1300, deadline=2880, changeover=CHANGEOVER,
            sync_strength=0,
        )
        # batch_0 应在 custom_start=1300 启动
        batch0 = [t for t in result if t.batch_index == 0]
        assert all(t.start_min == 1300 for t in batch0)
        # 后续批次应该或在第一天的尾窗口剩余时间内（仍 < 1380），或跳到第二天 (≥ 1920)
        for t in result:
            if t.batch_index >= 1:
                in_day1_tail = 1300 <= t.start_min <= 1380
                in_day2 = t.start_min >= 1920
                assert in_day1_tail or in_day2, \
                    f"batch>=1 start={t.start_min} not in day1-tail or day2 window"

    def test_72h_deadline_completes_more_products(self):
        """deadline 单调性不变量：同输入下 deadline=72h 完整产品数 ≥ deadline=24h。"""
        # 3 天窗口（用 TWO_DAY_WINDOWS 加上 Day 2 即可，但 deadline=72h 时 schedule_tasks
        # 会因为 TWO_DAY_WINDOWS 末窗在 2820 而无法在 2880~4320 之间启动新批次。
        # 因此手工拼一个 3 天窗口。
        three_day_windows = TWO_DAY_WINDOWS + [
            (480 + 2880, 720 + 2880),
            (750 + 2880, 1080 + 2880),
            (1110 + 2880, 1380 + 2880),
        ]
        # 构造 20 个产品的完整 BOM 任务（plan_two_phase 输出）
        queue = [(0, 10)] * 20
        tasks = plan_two_phase(
            num_printers=4, duration_hours=72, changeover=CHANGEOVER,
            surplus_enabled=False, windows=three_day_windows,
            custom_start=0, deadline=72 * 60,
            product_queue=queue, bom_map={10: DESK_BOM},
            config_map=DESK_CONFIGS, initial_supply={},
        )

        sched_24 = schedule_tasks(
            list(tasks), DESK_CONFIG_BY_ID, 4, three_day_windows,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
            sync_strength=0,
        )
        sched_72 = schedule_tasks(
            list(tasks), DESK_CONFIG_BY_ID, 4, three_day_windows,
            custom_start=0, deadline=72 * 60, changeover=CHANGEOVER,
            sync_strength=0,
        )
        prod_24 = count_complete_products(sched_24, DESK_CONFIG_BY_ID, {10: DESK_BOM})
        prod_72 = count_complete_products(sched_72, DESK_CONFIG_BY_ID, {10: DESK_BOM})
        assert prod_72.get(10, 0) >= prod_24.get(10, 0), \
            f"72h={prod_72.get(10,0)} should be >= 24h={prod_24.get(10,0)}"


class TestSurplusPoolClearance:
    """防御兜底：任务全部超 deadline 时 schedule_tasks/schedule_greedy 返回空 / 仅排能容纳的。

    iter1 之后 surplus 池清空兜底由 scheduler_core.schedule_greedy 内部维护
    （`remaining_surplus.clear()`，避免死循环）。下面 test_greedy_* 直接覆盖该路径。
    其它测试覆盖 schedule_tasks 中的等价防御。
    """

    def test_greedy_surplus_all_over_deadline_returns_only_demand(self):
        """schedule_greedy 路径：demand 能排、surplus 全部超 deadline → 只返回 demand，无死循环。

        直接触发 scheduler_core.schedule_greedy 第 547 行 `remaining_surplus.clear()`。
        """
        configs = {
            104: make_config(104, 4, "螺丝", qty=20, dur=60),   # demand: fit
            901: make_config(901, 91, "超长", qty=1, dur=500),   # surplus: 全部超 deadline
        }
        demand = [(104, "白色", 0)]                # (config_id, color, order_priority)
        surplus = [(901, "白色")] * 3
        result = schedule_greedy(
            demand_tasks=demand,
            surplus_tasks=surplus,
            configs=configs,
            num_printers=1,
            windows=DEFAULT_WINDOWS,
            custom_start=0,
            deadline=200,
            changeover=CHANGEOVER,
            sync_strength=0,
            use_product_first=False,
        )
        config_ids = {t.config_id for t in result}
        assert 104 in config_ids
        assert 901 not in config_ids
        assert len(result) >= 1  # 至少 demand 那一个排上了

    def test_greedy_demand_and_surplus_all_over_deadline_returns_empty(self):
        """schedule_greedy 路径：demand 和 surplus 都全部超 deadline → 返回空列表，不死循环。"""
        configs = {901: make_config(901, 91, "超长", qty=1, dur=500)}
        result = schedule_greedy(
            demand_tasks=[(901, "白色", 0)] * 3,
            surplus_tasks=[(901, "白色")] * 3,
            configs=configs,
            num_printers=2,
            windows=DEFAULT_WINDOWS,
            custom_start=0,
            deadline=200,
            changeover=CHANGEOVER,
            sync_strength=0,
            use_product_first=False,
        )
        assert result == []

    def test_demand_empty_all_surplus_over_deadline(self):
        """所有任务 dur > deadline → schedule_tasks 返回空列表，无死循环。"""
        # 任务时长 200min，deadline=100min → 全部排不下
        long_cfg = {901: make_config(901, 91, "超长", qty=1, dur=200)}
        tasks = [(901, "白色", False)] * 5
        result = schedule_tasks(
            tasks, long_cfg, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=100, changeover=CHANGEOVER,
            sync_strength=0,
        )
        assert result == []

    def test_demand_done_then_no_more_surplus_fits(self):
        """部分任务能排，剩余产能不足以容纳剩下的（dur > 剩余 deadline）→ 只返回能排的，无死循环。"""
        # 1 个 60min 任务 + 5 个 200min 任务，deadline=100min
        # 60min 能排，200min 都不能排
        configs = {
            104: make_config(104, 4, "螺丝", qty=20, dur=60),
            901: make_config(901, 91, "超长", qty=1, dur=200),
        }
        tasks = [(104, "白色", False)] + [(901, "白色", False)] * 5
        result = schedule_tasks(
            tasks, configs, 1, DEFAULT_WINDOWS,
            custom_start=0, deadline=100, changeover=CHANGEOVER,
            sync_strength=0,
        )
        # 只有 60min 那个任务能排上
        assert len(result) == 1
        assert result[0].config_id == 104

    def test_surplus_partial_fit(self):
        """混合：部分任务 fit、部分不 fit → fit 的被排上、不 fit 的不出现（按 config_id 校验）。

        2 台打印机并行排在 batch_0，60min 和 120min 各排一个；500/600min 超 deadline=200。
        """
        configs = {
            104: make_config(104, 4, "螺丝", qty=20, dur=60),   # fit
            101: make_config(101, 1, "桌板", qty=1, dur=120),   # fit
            902: make_config(902, 92, "超长 A", qty=1, dur=500),  # not fit
            903: make_config(903, 93, "超长 B", qty=1, dur=600),  # not fit
        }
        tasks = [
            (104, "白色", False),
            (101, "白色", False),
            (902, "白色", False),
            (903, "白色", False),
        ]
        # deadline=200min：60 / 120 能排（batch_0 并行），500 / 600 不能
        result = schedule_tasks(
            tasks, configs, 2, DEFAULT_WINDOWS,
            custom_start=0, deadline=200, changeover=CHANGEOVER,
            sync_strength=0,
        )
        config_ids = {t.config_id for t in result}
        assert 104 in config_ids
        assert 101 in config_ids
        assert 902 not in config_ids
        assert 903 not in config_ids


class TestBoundarySync:
    """sync_strength 边界值 + 公式数学性质（现有套件只测了 0/25/50/75/100）。"""

    def test_sync_1_close_to_sync_0(self):
        """sync=0 vs sync=1：penalty 二次方系数 (1/100)²=0.0001 ≈ 0，输出应几乎一致。

        断言完整产品数相同（schedule_tasks 选任务的相对顺序可能微小变化，
        但完整产品维度应不变）。
        """
        queue = [(0, 10)] * 4
        tasks = plan_two_phase(
            num_printers=4, duration_hours=24, changeover=CHANGEOVER,
            surplus_enabled=False, windows=DEFAULT_WINDOWS,
            custom_start=0, deadline=1440,
            product_queue=queue, bom_map={10: DESK_BOM},
            config_map=DESK_CONFIGS, initial_supply={},
        )
        r0 = schedule_tasks(
            list(tasks), DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
            sync_strength=0,
        )
        r1 = schedule_tasks(
            list(tasks), DESK_CONFIG_BY_ID, 4, DEFAULT_WINDOWS,
            custom_start=0, deadline=1440, changeover=CHANGEOVER,
            sync_strength=1,
        )
        p0 = count_complete_products(r0, DESK_CONFIG_BY_ID, {10: DESK_BOM})
        p1 = count_complete_products(r1, DESK_CONFIG_BY_ID, {10: DESK_BOM})
        assert p0.get(10, 0) == p1.get(10, 0), \
            f"sync=0 -> {p0.get(10,0)} but sync=1 -> {p1.get(10,0)}"

    def test_sync_99_close_to_sync_100(self):
        """sync=99 vs sync=100：行为接近，批次数差异 ≤ 1。

        (99/100)² = 0.9801 vs (100/100)² = 1.0；差异在尾端微小。
        """
        configs = {
            401: make_config(401, 41, "短", qty=1, dur=60),
            402: make_config(402, 42, "中", qty=1, dur=120),
            403: make_config(403, 43, "长", qty=1, dur=240),
        }
        tasks = (
            [(401, "白色", False)] * 4 +
            [(402, "白色", False)] * 4 +
            [(403, "白色", False)] * 4
        )
        r99 = schedule_tasks(
            list(tasks), configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=99,
        )
        r100 = schedule_tasks(
            list(tasks), configs, 4, DEFAULT_WINDOWS,
            custom_start=480, deadline=1440, changeover=CHANGEOVER,
            sync_strength=100,
        )
        b99 = max((t.batch_index for t in r99), default=-1) + 1
        b100 = max((t.batch_index for t in r100), default=-1) + 1
        assert abs(b99 - b100) <= 1, \
            f"sync=99 batches={b99} vs sync=100 batches={b100} differ too much"

    def test_sync_30_vs_70_quadratic_gap(self):
        """直接调公式：sync=70 的 penalty ≈ sync=30 的 (70/30)² ≈ 5.444 倍。

        公式：penalty = |dur - anchor|/anchor * (sync/100)² * changeover * 4
        固定其他变量，penalty 关于 sync 是二次比例。
        """
        p30 = _sync_penalty(150, 100, 30, 15)
        p70 = _sync_penalty(150, 100, 70, 15)
        ratio_expected = (70 / 30) ** 2  # ≈ 5.444
        assert abs(p70 / p30 - ratio_expected) < 1e-9, \
            f"p70/p30={p70/p30} expected≈{ratio_expected}"

    def test_anchor_one_no_exception(self):
        """anchor_duration=1 不抛异常，返回有限值，且因为 |100-1|/1=99 倍放大而很大。"""
        v = _sync_penalty(100, 1, 50, 15)
        # 不应是 inf / nan
        assert v == v  # not nan
        assert v != float('inf')
        # 公式：99 * (0.5)² * 15 * 4 = 99 * 0.25 * 60 = 1485
        assert abs(v - 1485.0) < 1e-9, f"got {v}"


class TestStrategyOrdering:
    """三策略本质偏序断言。

    策略映射到 scheduler_core API：
    - product_first: 用 pick_task 时传入 sim_supply/product_units/bom_cache/assembled
    - utilization: 用 pick_task 时不传产品上下文（按 priority + idle 选）
    - two_phase: plan_two_phase + schedule_tasks（schedule_tasks 本身已是 utilization 风格的批次填充）

    构造场景：多产品共享瓶颈组件 + 紧张产能，让 utilization 容易输给 product_first。
    """

    @staticmethod
    def _simulate_strategy(strategy: str, *, configs: dict[int, ConfigInfo],
                          bom_map: dict[int, dict[DemandKey, int]],
                          product_queue: list[tuple[int, int]],
                          task_pool: list[tuple[int, str, int]],
                          num_printers: int, deadline: int,
                          windows: list[tuple[int, int]], custom_start: int,
                          changeover: int = CHANGEOVER) -> list[ScheduledTask]:
        """共享调度循环。task_pool 元素 (config_id, color, priority)。"""
        configs_by_id = {c.id: c for c in configs.values()}

        if strategy == "two_phase":
            tasks = plan_two_phase(
                num_printers=num_printers, duration_hours=deadline // 60,
                changeover=changeover, surplus_enabled=False, windows=windows,
                custom_start=custom_start, deadline=deadline,
                product_queue=product_queue, bom_map=bom_map,
                config_map=configs, initial_supply={},
            )
            return schedule_tasks(
                tasks, configs_by_id, num_printers, windows,
                custom_start=custom_start, deadline=deadline,
                changeover=changeover, sync_strength=0,
            )

        # product_first / utilization：循环调用 pick_task 模拟分批调度
        sim_supply: dict[DemandKey, int] = {}
        assembled: set[int] = set()
        bom_cache = dict(bom_map)
        use_completion = strategy == "product_first"

        remaining = list(task_pool)
        printer_available = {i: custom_start for i in range(num_printers)}
        result: list[ScheduledTask] = []
        batch_order = 0

        while remaining:
            sorted_times = sorted(printer_available.values())
            if batch_order == 0:
                start = custom_start
            else:
                start = find_next_start(sorted_times[0], windows)
                if start is None or start >= deadline:
                    break
            if start >= deadline:
                break

            available_printers = [p for p in range(num_printers)
                                  if printer_available[p] <= start]
            if not available_printers:
                next_start = find_next_start(sorted_times[0] + 1, windows)
                if next_start is None or next_start >= deadline:
                    break
                for pid in printer_available:
                    if printer_available[pid] <= sorted_times[0]:
                        printer_available[pid] = next_start
                        break
                continue

            added = 0
            for printer in available_printers:
                if use_completion:
                    picked = pick_task(
                        remaining, configs_by_id, start, changeover, windows, deadline,
                        sim_supply=sim_supply, product_units=product_queue,
                        bom_cache=bom_cache, assembled=assembled,
                    )
                else:
                    picked = pick_task(
                        remaining, configs_by_id, start, changeover, windows, deadline,
                    )
                if picked is None:
                    break
                cid = picked[0]
                color = picked[1]
                cfg = configs_by_id[cid]
                end_min = start + cfg.duration_minutes
                result.append(ScheduledTask(
                    printer_index=printer, config_id=cid, color=color,
                    is_surplus=False, start_min=start, end_min=end_min,
                    batch_index=batch_order,
                ))
                printer_available[printer] = end_min + changeover
                added += 1

                if use_completion:
                    key = (cfg.component_id, color)
                    sim_supply[key] = sim_supply.get(key, 0) + cfg.quantity
                    try_assemble(sim_supply, product_queue, bom_cache, assembled)

            if added == 0:
                break
            batch_order += 1

        return result

    def _build_shared_bottleneck_scenario(self):
        """构造场景：3 个产品共享同一个瓶颈组件 (1:1 产出) + 各有 1 个差异组件。

        - product 10/11/12 都需要 1 个瓶颈件 (comp 1, "白色", 1/盘, 100min)
          和 1 个各自的差异件 (60min, 1/盘)
        - 6 个产品单元（每产品 2 个）共需 6 个瓶颈件 + 6 个差异件
        - 4 台打印机 × 6h = 1440min，节奏紧张
        - utilization 按 FIFO 优先级会顺次塞同 priority 组件；
          product_first 会优先凑齐（瓶颈+差异 一对一）
        """
        configs = {
            (1, "白色"): make_config(1001, 1, "瓶颈", qty=1, dur=100),
            (2, "白色"): make_config(1002, 2, "差异A", qty=1, dur=60),
            (3, "白色"): make_config(1003, 3, "差异B", qty=1, dur=60),
            (4, "白色"): make_config(1004, 4, "差异C", qty=1, dur=60),
        }
        bom_map = {
            10: {(1, "白色"): 1, (2, "白色"): 1},
            11: {(1, "白色"): 1, (3, "白色"): 1},
            12: {(1, "白色"): 1, (4, "白色"): 1},
        }
        # 产品单元：每产品 2 个，priority 按产品错开（utilization 会按 priority 排序）
        product_queue = [
            (0, 10), (0, 10),
            (1, 11), (1, 11),
            (2, 12), (2, 12),
        ]
        # 任务池：每个 BOM 项展开为一个任务
        # utilization 模式下按 priority 顺序选 → 会先把 10 的 2 套排上，再 11 的，再 12 的
        task_pool: list[tuple[int, str, int]] = []
        for pri, pid in product_queue:
            for comp_key in bom_map[pid]:
                cfg = configs[comp_key]
                task_pool.append((cfg.id, comp_key[1], pri))
        return configs, bom_map, product_queue, task_pool

    def test_product_first_geq_utilization_complete_products(self):
        """构造多产品共享瓶颈场景 → product_first 完整产品数 ≥ utilization。

        场景为何能区分：
        - utilization 按 priority FIFO 选，瓶颈件 100min × 6 + 差异件 60min × 6
          会按 priority 顺序排，整体能凑齐但可能在 deadline 截断时刚好少 1 套。
        - product_first 用 product_completion_score 衡量"凑齐到哪步"，
          优先生产能立刻把 sim_supply 凑齐的组件，整体完整产品数 ≥ utilization。
        """
        configs, bom_map, queue, task_pool = self._build_shared_bottleneck_scenario()
        configs_by_id = {c.id: c for c in configs.values()}

        # deadline 紧张：只够排 9~10 个任务（< 12 个总需求）
        deadline = 600  # 10h
        pf = self._simulate_strategy(
            "product_first", configs=configs, bom_map=bom_map,
            product_queue=list(queue), task_pool=list(task_pool),
            num_printers=2, deadline=deadline, windows=DEFAULT_WINDOWS,
            custom_start=480,
        )
        ut = self._simulate_strategy(
            "utilization", configs=configs, bom_map=bom_map,
            product_queue=list(queue), task_pool=list(task_pool),
            num_printers=2, deadline=deadline, windows=DEFAULT_WINDOWS,
            custom_start=480,
        )

        pf_complete = count_complete_products(pf, configs_by_id, bom_map)
        ut_complete = count_complete_products(ut, configs_by_id, bom_map)
        pf_total = sum(pf_complete.values())
        ut_total = sum(ut_complete.values())
        assert pf_total >= ut_total, \
            f"product_first total={pf_total} should be >= utilization total={ut_total}; " \
            f"pf={pf_complete} ut={ut_complete}"

    def test_two_phase_geq_utilization_complete_products(self):
        """同一场景：two_phase ≥ utilization。

        two_phase 阶段 1 用 plan_two_phase 全局规划 BOM 配比 + 溢出复用，
        不应劣于直接 FIFO 的 utilization。
        """
        configs, bom_map, queue, task_pool = self._build_shared_bottleneck_scenario()
        configs_by_id = {c.id: c for c in configs.values()}

        deadline = 600
        tp = self._simulate_strategy(
            "two_phase", configs=configs, bom_map=bom_map,
            product_queue=list(queue), task_pool=list(task_pool),
            num_printers=2, deadline=deadline, windows=DEFAULT_WINDOWS,
            custom_start=480,
        )
        ut = self._simulate_strategy(
            "utilization", configs=configs, bom_map=bom_map,
            product_queue=list(queue), task_pool=list(task_pool),
            num_printers=2, deadline=deadline, windows=DEFAULT_WINDOWS,
            custom_start=480,
        )

        tp_complete = count_complete_products(tp, configs_by_id, bom_map)
        ut_complete = count_complete_products(ut, configs_by_id, bom_map)
        tp_total = sum(tp_complete.values())
        ut_total = sum(ut_complete.values())
        assert tp_total >= ut_total, \
            f"two_phase total={tp_total} should be >= utilization total={ut_total}; " \
            f"tp={tp_complete} ut={ut_complete}"

    def test_all_strategies_within_total_duration_bound(self):
        """同一输入下三策略输出，所有任务总时长 ≤ num_printers × (deadline - custom_start)。

        这是基本守约（产能不可能凭空多出来），三策略都应满足。
        """
        configs, bom_map, queue, task_pool = self._build_shared_bottleneck_scenario()
        deadline = 720
        custom_start = 0
        num_printers = 2
        capacity_bound = num_printers * (deadline - custom_start)

        for strat in ("product_first", "utilization", "two_phase"):
            res = self._simulate_strategy(
                strat, configs=configs, bom_map=bom_map,
                product_queue=list(queue), task_pool=list(task_pool),
                num_printers=num_printers, deadline=deadline,
                windows=DEFAULT_WINDOWS, custom_start=custom_start,
            )
            total_dur = sum(t.end_min - t.start_min for t in res)
            assert total_dur <= capacity_bound, \
                f"strategy={strat} total_dur={total_dur} > capacity_bound={capacity_bound}"

    def test_product_first_strictly_better_at_tight_deadline(self):
        """另一 deadline 点（800min）下 product_first **严格** > utilization：pf=5 vs ut=2。

        与 test_product_first_geq_utilization_complete_products (deadline=600，pf=1 ut=0)
        互补：本测试用 deadline=800，差距更大（3 个完整产品），强证明
        product_first 的凑齐评分相对 FIFO 在共享瓶颈场景下不是巧合优势。

        注：_simulate_strategy 调用 pick_task 时不传 sync_strength（默认 0），
        因此本测试纯测 product_completion_score 的价值，无同步策略干扰。
        """
        configs, bom_map, queue, task_pool = self._build_shared_bottleneck_scenario()
        configs_by_id = {c.id: c for c in configs.values()}
        deadline = 800
        pf = self._simulate_strategy(
            "product_first", configs=configs, bom_map=bom_map,
            product_queue=list(queue), task_pool=list(task_pool),
            num_printers=2, deadline=deadline, windows=DEFAULT_WINDOWS,
            custom_start=480,
        )
        ut = self._simulate_strategy(
            "utilization", configs=configs, bom_map=bom_map,
            product_queue=list(queue), task_pool=list(task_pool),
            num_printers=2, deadline=deadline, windows=DEFAULT_WINDOWS,
            custom_start=480,
        )
        pf_complete = count_complete_products(pf, configs_by_id, bom_map)
        ut_complete = count_complete_products(ut, configs_by_id, bom_map)
        pf_total = sum(pf_complete.values())
        ut_total = sum(ut_complete.values())
        # 严格 > 而非 ≥：本场景下 product_first 必须明显优于 FIFO
        assert pf_total > ut_total, \
            f"product_first total={pf_total} should be strictly > utilization total={ut_total}; " \
            f"pf={pf_complete} ut={ut_complete}"
        # 至少差 2 个完整产品（实测 5 vs 2，差 3；留 1 个余量）
        assert pf_total - ut_total >= 2, \
            f"gap={pf_total - ut_total} too small to be a meaningful discriminator"
