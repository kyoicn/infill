"""
排班核心算法 — 纯函数层

所有函数只接受 plain Python 数据结构，不依赖数据库。
scheduler.py 负责从 DB 读取数据、调用本模块、将结果写入 DB。

详细规格见 docs/schedule_specs.md
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil


# 需求维度：(component_id, color)
DemandKey = tuple[int, str]

# 富余生产：目标额外完整产品数量上限
SURPLUS_TARGET_PRODUCTS = 20

# 同步惩罚相对换料时间的放大倍数
SYNC_PENALTY_CHANGEOVER_MULT = 4


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ConfigInfo:
    """打印配置（对应 PrintConfig 的纯数据表示）"""
    id: int
    component_id: int
    component_name: str
    quantity: int           # 每盘产出
    duration_minutes: int   # 打印时长


@dataclass
class ScheduledTask:
    """Phase 2 输出的已排程任务"""
    printer_index: int      # 打印机序号（0-based）
    config_id: int
    color: str
    is_surplus: bool
    start_min: int
    end_min: int
    batch_index: int


# ---------------------------------------------------------------------------
# 窗口 & 空闲计算（纯函数）
# ---------------------------------------------------------------------------

def find_next_start(current_min: int, windows: list[tuple[int, int]]) -> int | None:
    """找到 >= current_min 的最早可启动时间（必须在操作窗口内）。"""
    for ws, we in windows:
        if current_min <= we:
            return max(current_min, ws)
    return None


def idle_after(start: int, duration: int, changeover: int, windows: list[tuple[int, int]]) -> int:
    """计算任务结束后打印机的空闲等待时间（分钟）。"""
    available_at = start + duration + changeover
    for ws, we in windows:
        if ws <= available_at <= we:
            return 0
        if ws > available_at:
            return ws - available_at
    return 0


# ---------------------------------------------------------------------------
# 产品凑齐评分（纯函数）
# ---------------------------------------------------------------------------

def product_completion_score(
    comp_key: DemandKey,
    sim_supply: dict[DemandKey, int],
    product_units: list[tuple[int, int]],
    bom_cache: dict[int, dict[DemandKey, int]],
    assembled: set[int],
) -> tuple[float, float, float]:
    """计算生产某组件对凑齐产品的贡献分数（越小越优先）。"""
    best: tuple[float, float, float] = (float('inf'), 0.0, float('inf'))

    for i, (pu_pri, pu_pid) in enumerate(product_units):
        if i in assembled:
            continue
        bom = bom_cache.get(pu_pid, {})
        if comp_key not in bom:
            continue

        min_ratio = float('inf')
        for bom_key, bom_qty in bom.items():
            if bom_qty <= 0:
                continue
            ratio = sim_supply.get(bom_key, 0) / bom_qty
            min_ratio = min(min_ratio, ratio)

        comp_bom_qty = bom[comp_key]
        comp_ratio = sim_supply.get(comp_key, 0) / comp_bom_qty if comp_bom_qty > 0 else float('inf')

        prod_score = (pu_pri, -min_ratio, comp_ratio)
        if prod_score < best:
            best = prod_score

    return best


def try_assemble(
    sim_supply: dict[DemandKey, int],
    product_units: list[tuple[int, int]],
    bom_cache: dict[int, dict[DemandKey, int]],
    assembled: set[int],
) -> None:
    """尝试从模拟库存中组装产品单元，按优先级消费供给。"""
    changed = True
    while changed:
        changed = False
        for i, (_, pid) in enumerate(product_units):
            if i in assembled:
                continue
            bom = bom_cache.get(pid, {})
            if not bom:
                continue
            if all(sim_supply.get(k, 0) >= qty for k, qty in bom.items()):
                assembled.add(i)
                for k, qty in bom.items():
                    sim_supply[k] = sim_supply.get(k, 0) - qty
                changed = True
                break


# ---------------------------------------------------------------------------
# 同步惩罚（纯函数）
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 任务选择（纯函数，带同步强度）
# ---------------------------------------------------------------------------

def pick_task(
    remaining: list,
    configs: dict[int, ConfigInfo],
    start: int,
    changeover: int,
    windows: list[tuple[int, int]],
    deadline: int,
    sim_supply: dict[DemandKey, int] | None = None,
    product_units: list[tuple[int, int]] | None = None,
    bom_cache: dict[int, dict[DemandKey, int]] | None = None,
    assembled: set[int] | None = None,
    anchor_duration: int | None = None,
    sync_strength: int = 0,
) -> tuple | None:
    """选择最优任务。remaining 元素为 (config_id, color) 或 (config_id, color, priority)。

    anchor_duration / sync_strength：同步策略参数。当 anchor_duration 有值时，
    时长偏差惩罚作为评分前缀，sync_strength 控制惩罚权重。
    """
    if not remaining:
        return None

    use_completion = (
        sim_supply is not None
        and product_units is not None
        and bom_cache is not None
    )

    best_idx = -1
    best_score: tuple | None = None

    for i in range(len(remaining)):
        item = remaining[i]
        cid = item[0]
        color = item[1]
        cfg = configs[cid]
        dur = cfg.duration_minutes
        if start + dur > deadline:
            continue

        idle = idle_after(start, dur, changeover, windows)

        if use_completion:
            comp_key = (cfg.component_id, color)
            prod_score = product_completion_score(
                comp_key, sim_supply, product_units, bom_cache, assembled or set()
            )
        else:
            pri = item[2] if len(item) > 2 else 0
            prod_score = (float(pri), 0.0, 0.0)

        # 同步惩罚：转换为等效空闲分钟（additive，不阻止批次填满）
        sync_penalty = _sync_penalty(dur, anchor_duration, sync_strength, changeover)

        score = (prod_score, idle + sync_penalty, dur)

        if best_idx == -1 or score < best_score:
            best_idx = i
            best_score = score

    if best_idx == -1:
        return None
    return remaining.pop(best_idx)


# ---------------------------------------------------------------------------
# Phase 1：产能规划（纯函数）
# ---------------------------------------------------------------------------

def compute_effective_capacity(
    custom_start: int,
    deadline: int,
    windows: list[tuple[int, int]],
    num_printers: int,
    safety_margin: float = 0.9,
) -> int:
    """基于操作窗口计算有效产能（分钟）。"""
    total_span = deadline - custom_start

    sorted_wins = sorted([
        (max(ws, custom_start), min(we, deadline))
        for ws, we in windows
        if min(we, deadline) > max(ws, custom_start)
    ])

    gap_loss = 0.0
    for i in range(len(sorted_wins) - 1):
        gap = sorted_wins[i + 1][0] - sorted_wins[i][1]
        if gap > 0:
            gap_loss += gap * 0.5

    effective_per_printer = total_span - gap_loss
    return int(effective_per_printer * num_printers * safety_margin)


def plan_two_phase(
    *,
    num_printers: int,
    duration_hours: int,
    changeover: int,
    surplus_enabled: bool,
    windows: list[tuple[int, int]],
    custom_start: int,
    deadline: int,
    # 数据
    product_queue: list[tuple[int, int]],          # [(priority, product_id), ...]
    bom_map: dict[int, dict[DemandKey, int]],      # {product_id: {(comp_id,color): qty}}
    config_map: dict[DemandKey, ConfigInfo],        # {(comp_id,color): ConfigInfo}
    initial_supply: dict[DemandKey, int],
    order_demand_counts: dict[tuple[int, str], int] | None = None,  # 订单需求盘数
) -> list[tuple[int, str, bool]]:
    """两阶段法 — 阶段 1：生产规划。

    从总产能出发，按 BOM 精确计算各组件目标盘数，利用溢出复用减少浪费。

    Returns:
        [(config_id, color, is_surplus), ...] 展开的任务列表
    """
    # -- 1. 计算有效产能 --
    total_capacity = compute_effective_capacity(
        custom_start, deadline, windows, num_printers,
    )

    if not product_queue:
        return []

    # -- 2. 溢出池（含初始库存） --
    overflow: dict[DemandKey, int] = dict(initial_supply)

    # -- 3. 贪心分配产能 --
    plan_counts: dict[tuple[int, str], int] = defaultdict(int)
    remaining_capacity = total_capacity
    order_demand_boundary = len([1 for pri, _ in product_queue if pri < 999])

    for idx, (pri, pid) in enumerate(product_queue):
        bom = bom_map.get(pid)
        if not bom:
            continue

        # 计算此产品单元需要的各组件盘数（考虑溢出抵扣）
        plates_needed: dict[DemandKey, int] = {}
        time_needed = 0

        for comp_key, bom_qty in bom.items():
            cfg = config_map.get(comp_key)
            if not cfg:
                continue
            available = overflow.get(comp_key, 0)
            net_need = max(0, bom_qty - available)
            if net_need == 0:
                plates_needed[comp_key] = 0
            else:
                plates = ceil(net_need / cfg.quantity)
                plates_needed[comp_key] = plates
                time_needed += plates * (cfg.duration_minutes + changeover)

        # 检查产能是否足够
        if time_needed > remaining_capacity:
            bottleneck_items = []
            for comp_key, plates in plates_needed.items():
                if plates > 0:
                    cfg = config_map[comp_key]
                    plate_time = cfg.duration_minutes + changeover
                    bottleneck_items.append((comp_key, plates, plate_time))
            bottleneck_items.sort(key=lambda x: -x[2])

            for comp_key, plates, plate_time in bottleneck_items:
                cfg = config_map[comp_key]
                affordable = int(remaining_capacity // plate_time)
                actual = min(plates, affordable)
                if actual > 0:
                    plan_counts[(cfg.id, comp_key[1])] += actual
                    overflow[comp_key] = overflow.get(comp_key, 0) + actual * cfg.quantity - (bom.get(comp_key, 0) if actual >= plates else 0)
                    remaining_capacity -= actual * plate_time
            break

        # 产能足够：记录盘数，更新溢出池
        remaining_capacity -= time_needed
        for comp_key, plates in plates_needed.items():
            if plates > 0:
                cfg = config_map[comp_key]
                plan_counts[(cfg.id, comp_key[1])] += plates
                produced = plates * cfg.quantity
                consumed = bom[comp_key]
                available = overflow.get(comp_key, 0)
                if available >= bom[comp_key]:
                    overflow[comp_key] = available - consumed + produced
                else:
                    overflow[comp_key] = produced - (consumed - available)
            else:
                overflow[comp_key] = overflow.get(comp_key, 0) - bom.get(comp_key, 0)

    # -- 4. 展开为任务列表 --
    tasks: list[tuple[int, str, bool]] = []
    for (config_id, color), count in plan_counts.items():
        for _ in range(count):
            tasks.append((config_id, color, False))

    # 标记富余
    if order_demand_counts is not None:
        tasks_final: list[tuple[int, str, bool]] = []
        remaining_order: dict[tuple[int, str], int] = dict(order_demand_counts)
        for config_id, color, _ in tasks:
            key = (config_id, color)
            if remaining_order.get(key, 0) > 0:
                remaining_order[key] -= 1
                tasks_final.append((config_id, color, False))
            else:
                tasks_final.append((config_id, color, True))
        return tasks_final

    return tasks


# ---------------------------------------------------------------------------
# Phase 2：时间排程（纯函数）
# ---------------------------------------------------------------------------

def schedule_tasks(
    tasks: list[tuple[int, str, bool]],
    configs: dict[int, ConfigInfo],
    num_printers: int,
    windows: list[tuple[int, int]],
    custom_start: int,
    deadline: int,
    changeover: int,
    sync_strength: int = 50,
) -> list[ScheduledTask]:
    """Phase 2：将任务分配到打印机时间槽。

    Returns:
        排程后的任务列表
    """
    if not tasks:
        return []

    printer_available = {i: custom_start for i in range(num_printers)}
    remaining: list[tuple[int, str, bool]] = list(tasks)
    result: list[ScheduledTask] = []
    batch_order = 0

    def _pick(start: int, anchor_dur: int | None = None) -> tuple[int, str, bool] | None:
        best_idx = -1
        best_score: float | None = None
        for i, (cid, color, is_surplus) in enumerate(remaining):
            cfg = configs[cid]
            dur = cfg.duration_minutes
            if start + dur > deadline:
                continue
            idle = idle_after(start, dur, changeover, windows)
            sp = _sync_penalty(dur, anchor_dur, sync_strength, changeover)
            score = idle + sp
            if best_idx == -1 or score < best_score:
                best_idx = i
                best_score = score
        if best_idx == -1:
            return None
        return remaining.pop(best_idx)

    while remaining:
        sorted_times = sorted(printer_available.values())

        if batch_order == 0:
            start = custom_start
        else:
            # sync_strength 控制等多久才开新批次（连续插值）：
            # sync=0 → 用最早打印机的可用时间
            # sync=100 → 用最晚打印机的可用时间
            # sync=50 → 用中间值
            t_earliest = sorted_times[0]
            t_latest = sorted_times[-1]
            wait_time = t_earliest + (t_latest - t_earliest) * sync_strength / 100
            start = find_next_start(int(wait_time), windows)

            # 如果等待导致超时，回退到最早可用的打印机
            if start is None or start >= deadline:
                start = find_next_start(t_earliest, windows)

            if start is None:
                break

        if start >= deadline:
            break

        available_printers = [p for p in range(num_printers) if printer_available[p] <= start]
        if not available_printers:
            next_start = find_next_start(sorted_times[0] + 1, windows)
            if next_start is None or next_start >= deadline:
                break
            for pid in printer_available:
                if printer_available[pid] <= sorted_times[0]:
                    printer_available[pid] = next_start
                    break
            continue

        batch_tasks_added = 0
        batch_anchor: int | None = None
        for printer in available_printers:
            item = _pick(start, batch_anchor)
            if item is None:
                break
            config_id, color, is_surplus = item
            cfg = configs[config_id]
            end_min = start + cfg.duration_minutes
            if batch_anchor is None:
                batch_anchor = cfg.duration_minutes

            result.append(ScheduledTask(
                printer_index=printer,
                config_id=config_id,
                color=color,
                is_surplus=is_surplus,
                start_min=start,
                end_min=end_min,
                batch_index=batch_order,
            ))
            printer_available[printer] = end_min + changeover
            batch_tasks_added += 1

        if batch_tasks_added == 0:
            break

        batch_order += 1

    return result


# ---------------------------------------------------------------------------
# 贪心主循环（纯函数）— product_first / utilization 共用
# ---------------------------------------------------------------------------

def schedule_greedy(
    *,
    demand_tasks: list[tuple[int, str, int]],   # (config_id, color, order_priority)
    surplus_tasks: list[tuple[int, str]],        # 无富余传 []
    configs: dict[int, ConfigInfo],
    num_printers: int,
    windows: list[tuple[int, int]],
    custom_start: int,
    deadline: int,
    changeover: int,
    sync_strength: int,
    use_product_first: bool,
    sim_supply: dict[DemandKey, int] | None = None,
    product_units: list[tuple[int, int]] | None = None,
    bom_cache: dict[int, dict[DemandKey, int]] | None = None,
    assembled: set[int] | None = None,
) -> list[ScheduledTask]:
    """贪心分批调度：先排需求任务，再排富余任务。

    use_product_first=True 时用模拟库存的产品凑齐评分；否则按订单 FIFO 优先级。
    批次启动沿用现状：首批从 custom_start；后续批从 find_next_start(min(printer_available))。
    返回排程后的 ScheduledTask 列表（不触碰数据库）。
    """
    printer_available = {i: custom_start for i in range(num_printers)}
    remaining_tasks: list = list(demand_tasks)
    remaining_surplus: list = list(surplus_tasks)
    result: list[ScheduledTask] = []
    batch_order = 0

    def _next_task(start: int, batch_anchor: int | None = None) -> tuple[int, str, bool] | None:
        """优先从需求任务中取，取完后从富余池中取。返回 (config_id, color, is_surplus)"""
        if remaining_tasks:
            if use_product_first:
                picked = pick_task(
                    remaining_tasks, configs, start, changeover, windows, deadline,
                    sim_supply, product_units, bom_cache, assembled,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            else:
                picked = pick_task(
                    remaining_tasks, configs, start, changeover, windows, deadline,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            if picked:
                return (picked[0], picked[1], False)
            # 需求池里都放不下了（全部超出 deadline），清空以避免死循环
            if not any(start + configs[t[0]].duration_minutes <= deadline for t in remaining_tasks):
                remaining_tasks.clear()
        if remaining_surplus:
            if use_product_first:
                picked = pick_task(
                    remaining_surplus, configs, start, changeover, windows, deadline,
                    sim_supply, product_units, bom_cache, assembled,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            else:
                picked = pick_task(
                    remaining_surplus, configs, start, changeover, windows, deadline,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            if picked:
                return (picked[0], picked[1], True)
            if not any(start + configs[t[0]].duration_minutes <= deadline for t in remaining_surplus):
                remaining_surplus.clear()
        return None

    while True:
        if not remaining_tasks and not remaining_surplus:
            break

        earliest = min(printer_available.values())

        if batch_order == 0:
            start = custom_start
        else:
            start = find_next_start(earliest, windows)
            if start is None:
                break

        if start >= deadline:
            break

        available_printers = [p for p in range(num_printers) if printer_available[p] <= start]
        if not available_printers:
            # 安全兜底：防止死循环
            next_start = find_next_start(earliest + 1, windows)
            if next_start is None or next_start >= deadline:
                break
            for pid in printer_available:
                if printer_available[pid] <= earliest:
                    printer_available[pid] = next_start
                    break
            continue

        batch_tasks_added = 0
        batch_anchor: int | None = None  # 锚定时长
        for printer in available_printers:
            item = _next_task(start, batch_anchor)
            if item is None:
                break
            config_id, color, is_surplus = item
            cfg = configs[config_id]
            end_min = start + cfg.duration_minutes
            if batch_anchor is None:
                batch_anchor = cfg.duration_minutes  # 第一台设锚

            # product_first 策略：更新模拟库存，检查是否有产品可组装
            if use_product_first:
                comp_key = (cfg.component_id, color)
                sim_supply[comp_key] = sim_supply.get(comp_key, 0) + cfg.quantity
                try_assemble(sim_supply, product_units, bom_cache, assembled)

            result.append(ScheduledTask(
                printer_index=printer,
                config_id=config_id,
                color=color,
                is_surplus=is_surplus,
                start_min=start,
                end_min=end_min,
                batch_index=batch_order,
            ))
            printer_available[printer] = end_min + changeover
            batch_tasks_added += 1

        if batch_tasks_added == 0:
            break

        batch_order += 1

    return result


# ---------------------------------------------------------------------------
# 便利函数：完整产品产出统计
# ---------------------------------------------------------------------------

def count_complete_products(
    scheduled: list[ScheduledTask],
    configs: dict[int, ConfigInfo],
    bom_map: dict[int, dict[DemandKey, int]],
    initial_supply: dict[DemandKey, int] | None = None,
) -> dict[int, int]:
    """根据排程结果统计可组装的完整产品数。

    Returns:
        {product_id: count}
    """
    supply: dict[DemandKey, int] = dict(initial_supply or {})
    for task in scheduled:
        cfg = configs[task.config_id]
        key = (cfg.component_id, task.color)
        supply[key] = supply.get(key, 0) + cfg.quantity

    result: dict[int, int] = {}
    for pid, bom in bom_map.items():
        if not bom:
            result[pid] = 0
            continue
        min_count = float('inf')
        for key, qty in bom.items():
            if qty <= 0:
                continue
            min_count = min(min_count, supply.get(key, 0) // qty)
        result[pid] = int(min_count) if min_count != float('inf') else 0

    return result
