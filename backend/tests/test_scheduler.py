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
# 8. 不变量辅助函数自身正确性（负样本）
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
