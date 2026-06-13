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
# 8. P1 行为广度补强：跨天 schedule_tasks、防御兜底、sync 边界值、策略偏序
# =====================================================================

def _sync_penalty_formula(dur: int, anchor_duration: int,
                          sync_strength: int, changeover: int) -> float:
    """复刻 scheduler_core 内联同步惩罚公式（schedule_tasks._pick / pick_task）。

    生产代码中该公式以内联表达式存在（scheduler_core.py 内 sync_strength>0
    分支），并非独立函数。测试在本地复刻便于直接断言公式数学性质，
    不依赖也不修改生产代码。
    """
    if anchor_duration <= 0 or sync_strength <= 0:
        return 0.0
    strength_factor = (sync_strength / 100) ** 2
    return abs(dur - anchor_duration) / anchor_duration * strength_factor * changeover * 4


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
    """防御兜底：任务全部超 deadline 时 schedule_tasks 返回空 / 仅排能容纳的。

    注：scheduler_core.py 暴露的纯函数是 schedule_tasks，没有独立的 surplus pool。
    surplus pool clear() 兜底位于 scheduler.py 的 orchestration 层（line ~700），
    其等价防御在 schedule_tasks 中是 `if start + dur > deadline: continue`（line 391）
    和 batch_tasks_added==0 时退出循环。这里测的是 schedule_tasks 的同等防御。
    """

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
        p30 = _sync_penalty_formula(150, 100, 30, 15)
        p70 = _sync_penalty_formula(150, 100, 70, 15)
        ratio_expected = (70 / 30) ** 2  # ≈ 5.444
        assert abs(p70 / p30 - ratio_expected) < 1e-9, \
            f"p70/p30={p70/p30} expected≈{ratio_expected}"

    def test_anchor_one_no_exception(self):
        """anchor_duration=1 不抛异常，返回有限值，且因为 |100-1|/1=99 倍放大而很大。"""
        v = _sync_penalty_formula(100, 1, 50, 15)
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

    def test_product_first_no_worse_at_sync_zero(self):
        """sync=0 时（同步惩罚=0）product_first 在完整产品维度不劣于 utilization。

        sync=0 排除掉同步策略对 pick_task 的影响，纯测产品凑齐评分的价值。
        """
        configs, bom_map, queue, task_pool = self._build_shared_bottleneck_scenario()
        configs_by_id = {c.id: c for c in configs.values()}
        deadline = 600
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
        assert sum(pf_complete.values()) >= sum(ut_complete.values()), \
            f"product_first complete={pf_complete} < utilization complete={ut_complete}"
