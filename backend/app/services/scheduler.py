"""
排班算法服务

算法步骤：
1. 按订单 FIFO 顺序逐个计算组件需求，生成任务池
2. 选择打印配置组合覆盖需求
3. 产品凑齐优先调度：
   - 维护模拟库存，动态评估每个候选任务对凑齐完整产品的贡献
   - 优先安排能让某个产品最快组装完成的瓶颈组件
   - 产品优先顺序按订单 FIFO；同一订单内接近完成的产品优先
   - 空闲时间和任务时长作为末级 tiebreaker
   - 自适应操作窗口结构，无硬编码"白天/夜间"概念

详细规格见 docs/schedule_specs.md
"""

from datetime import date, timedelta
from collections import defaultdict

from sqlalchemy.orm import Session

from ..models import (
    Order, OrderItem, ProductComponent, Inventory,
    PrintConfig, Printer, ScheduleConfig, SystemConfig,
    PrintPlan, PrintBatch, PrintTask,
)

# 需求维度：(component_id, color)
DemandKey = tuple[int, str]

# 富余生产：目标额外完整产品数量上限
SURPLUS_TARGET_PRODUCTS = 20


def _get_changeover_minutes(db: Session) -> int:
    cfg = db.query(SystemConfig).filter(SystemConfig.key == "changeover_minutes").first()
    return int(cfg.value) if cfg else 15


def _get_day_windows(db: Session, d: date) -> list[tuple[int, int]]:
    """获取某一天的操作时间窗口（分钟，0~1440）"""
    dow = d.weekday()
    cfg = db.query(ScheduleConfig).filter(ScheduleConfig.day_of_week == dow).first()
    if not cfg:
        return [(480, 720), (750, 1080), (1110, 1380)]  # 8-12, 12:30-18, 18:30-23
    windows = []
    for w in cfg.windows:
        sh, sm = map(int, w["start"].split(":"))
        eh, em = map(int, w["end"].split(":"))
        windows.append((sh * 60 + sm, eh * 60 + em))
    return sorted(windows)


def _get_windows(db: Session, target_date: date, start_min: int = 480, duration_hours: int = 24) -> list[tuple[int, int]]:
    """获取排班周期内的操作时间窗口，跨天时拼接多天的窗口并偏移。"""
    end_min = start_min + duration_hours * 60
    days_needed = (end_min + 1439) // 1440
    windows = []
    for day_offset in range(days_needed):
        d = target_date + timedelta(days=day_offset)
        day_windows = _get_day_windows(db, d)
        offset = day_offset * 1440
        for ws, we in day_windows:
            windows.append((ws + offset, we + offset))
    return windows


def _get_initial_supply(db: Session, target_date: date) -> dict[DemandKey, int]:
    """获取初始供给 = 当前库存 + 早于 target_date 的已排班产出"""
    inventories = db.query(Inventory).all()
    supply: dict[DemandKey, int] = {(inv.component_id, inv.color): inv.quantity for inv in inventories}

    earlier_plans = db.query(PrintPlan).filter(PrintPlan.date < target_date).all()
    for plan in earlier_plans:
        for batch in plan.batches:
            for task in batch.tasks:
                cfg = db.get(PrintConfig, task.print_config_id)
                if cfg:
                    key = (cfg.component_id, task.color)
                    supply[key] = supply.get(key, 0) + cfg.quantity

    return supply


def _select_configs(db: Session, demand: dict[DemandKey, int]) -> list[tuple[int, str]]:
    """为每个 (component_id, color) 选择打印配置，返回 (config_id, color) 列表"""
    tasks: list[tuple[int, str]] = []

    for (comp_id, color), needed in demand.items():
        configs = (
            db.query(PrintConfig)
            .filter(PrintConfig.component_id == comp_id)
            .order_by(PrintConfig.quantity.desc())
            .all()
        )
        if not configs:
            continue

        remaining = needed
        while remaining > 0:
            best = None
            for cfg in configs:
                if cfg.quantity <= remaining:
                    best = cfg
                    break
            if best is None:
                best = configs[-1]
            tasks.append((best.id, color))
            remaining -= best.quantity

    return tasks


def _calc_ordered_tasks(
    db: Session, target_date: date, target_product_ids: set[int] | None = None,
) -> tuple[list[tuple[int, str, int]], dict[DemandKey, int]]:
    """按订单 FIFO 顺序计算任务，返回 (带优先级的任务列表, 预计库存)。

    每个任务为 (config_id, color, priority)，priority 越小越优先。
    库存已满足的订单会被跳过。前序订单的打印溢出量会顺延给后续订单使用。
    如果指定了 target_product_ids，则只计算匹配产品的订单项需求。
    """
    supply = _get_initial_supply(db, target_date)
    orders = db.query(Order).filter(Order.status == "pending").order_by(Order.created_at).all()

    all_tasks: list[tuple[int, str, int]] = []  # (config_id, color, priority)

    for priority, order in enumerate(orders):
        # 计算此订单的组件需求（如有产品过滤则只考虑匹配产品）
        order_demand: dict[DemandKey, int] = defaultdict(int)
        for item in order.items:
            if target_product_ids and item.product_id not in target_product_ids:
                continue
            bom = db.query(ProductComponent).filter(ProductComponent.product_id == item.product_id).all()
            for b in bom:
                order_demand[(b.component_id, b.color)] += b.quantity * item.quantity

        # 从供给中扣除，计算净需求
        net_demand: dict[DemandKey, int] = {}
        for key, qty in order_demand.items():
            available = supply.get(key, 0)
            if qty > available:
                net_demand[key] = qty - available
                supply[key] = 0
            else:
                supply[key] = available - qty

        if not net_demand:
            continue  # 库存已满足此订单

        # 为净需求选择打印配置
        order_tasks = _select_configs(db, net_demand)

        # 计算任务产出，将溢出量加回供给（供后续订单使用）
        task_output: dict[DemandKey, int] = defaultdict(int)
        for cid, color in order_tasks:
            cfg = db.get(PrintConfig, cid)
            task_output[(cfg.component_id, color)] += cfg.quantity
            all_tasks.append((cid, color, priority))

        for key, needed in net_demand.items():
            excess = task_output.get(key, 0) - needed
            if excess > 0:
                supply[key] = supply.get(key, 0) + excess

    return all_tasks, supply


def _find_next_start(current_min: int, windows: list[tuple[int, int]]) -> int | None:
    """找到 >= current_min 的最早可启动时间（必须在操作窗口内）。
    注意：current_min 来自 printer_available，已包含换料时间。"""
    for ws, we in windows:
        if current_min <= we:
            return max(current_min, ws)
    return None


def _idle_after(start: int, duration: int, changeover: int, windows: list[tuple[int, int]]) -> int:
    """计算任务结束后打印机的空闲等待时间（分钟）。
    空闲时间 = 下一个操作窗口开始时间 - 打印机可用时间。
    值越小说明利用率越高——任务刚好在操作窗口内或窗口开始前结束。"""
    available_at = start + duration + changeover
    for ws, we in windows:
        if ws <= available_at <= we:
            return 0  # 在操作窗口内，无空闲
        if ws > available_at:
            return ws - available_at  # 等到下一个窗口
    return 0  # 排班周期结束，无需等待


def _build_product_context(
    db: Session, orders: list, target_product_ids: set[int] | None = None,
) -> tuple[list[tuple[int, int]], dict[int, dict[DemandKey, int]]]:
    """构建产品单元队列和 BOM 缓存，供凑齐产品优先调度使用。

    返回:
        product_units: [(order_priority, product_id), ...] 按订单顺序展开
        bom_cache: {product_id: {(comp_id, color): qty}}
    如果指定了 target_product_ids，则只包含匹配产品。
    """
    product_units: list[tuple[int, int]] = []
    bom_cache: dict[int, dict[DemandKey, int]] = {}

    for order_pri, order in enumerate(orders):
        for item in order.items:
            pid = item.product_id
            if target_product_ids and pid not in target_product_ids:
                continue
            for _ in range(item.quantity):
                product_units.append((order_pri, pid))
            if pid not in bom_cache:
                bom = db.query(ProductComponent).filter(ProductComponent.product_id == pid).all()
                bom_cache[pid] = {(b.component_id, b.color): b.quantity for b in bom}

    return product_units, bom_cache


def _product_completion_score(
    comp_key: DemandKey,
    sim_supply: dict[DemandKey, int],
    product_units: list[tuple[int, int]],
    bom_cache: dict[int, dict[DemandKey, int]],
    assembled: set[int],
) -> tuple[float, float, float]:
    """计算生产某组件对凑齐产品的贡献分数（越小越优先）。

    返回 (order_priority, -completion_ratio, bottleneck_ratio):
    - order_priority: 该组件对应的最高优先级订单（小=早=优先）
    - -completion_ratio: 产品完成度的负数（越接近完成越优先）
    - bottleneck_ratio: 该组件的供给比例（越低=越是瓶颈=越优先）
    """
    best: tuple[float, float, float] = (float('inf'), 0.0, float('inf'))

    for i, (pu_pri, pu_pid) in enumerate(product_units):
        if i in assembled:
            continue
        bom = bom_cache.get(pu_pid, {})
        if comp_key not in bom:
            continue

        # 产品完成度 = 最短板组件的供给比例
        min_ratio = float('inf')
        for bom_key, bom_qty in bom.items():
            if bom_qty <= 0:
                continue
            ratio = sim_supply.get(bom_key, 0) / bom_qty
            min_ratio = min(min_ratio, ratio)

        # 该组件自身的供给比例
        comp_bom_qty = bom[comp_key]
        comp_ratio = sim_supply.get(comp_key, 0) / comp_bom_qty if comp_bom_qty > 0 else float('inf')

        prod_score = (pu_pri, -min_ratio, comp_ratio)
        if prod_score < best:
            best = prod_score

    return best


def _try_assemble(
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
                break  # 从头重新检查以确保优先级顺序


def _pick_task(
    remaining: list,
    config_cache: dict[int, PrintConfig],
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

    当提供产品凑齐上下文时，选择优先级（从高到低）：
    1. 产品凑齐优先：
       - 订单优先级：更早订单的产品优先
       - 完成度：接近凑齐的产品优先
       - 瓶颈：该组件是产品最短板时优先
    2. 空闲时间：idle 小的优先
    3. 时长：短任务优先（为长间隔保留长任务）

    未提供产品上下文时回退到静态订单 FIFO 优先级。

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
        cfg = config_cache[cid]
        dur = cfg.duration_minutes
        if start + dur > deadline:
            continue

        idle = _idle_after(start, dur, changeover, windows)

        if use_completion:
            comp_key = (cfg.component_id, color)
            prod_score = _product_completion_score(
                comp_key, sim_supply, product_units, bom_cache, assembled or set()
            )
        else:
            pri = item[2] if len(item) > 2 else 0
            prod_score = (float(pri), 0.0, 0.0)

        # 同步惩罚：anchor_duration 有值时，时长偏差越大惩罚越高
        if anchor_duration is not None and anchor_duration > 0 and sync_strength > 0:
            sync_penalty = abs(dur - anchor_duration) / anchor_duration * (sync_strength / 100)
        else:
            sync_penalty = 0.0

        score = (sync_penalty, prod_score, idle, dur)

        if best_idx == -1 or score < best_score:
            best_idx = i
            best_score = score

    if best_idx == -1:
        return None
    return remaining.pop(best_idx)


def _build_surplus_tasks(
    db: Session, projected_stock: dict[DemandKey, int], target_product_ids: set[int] | None = None,
) -> list[tuple[int, str]]:
    """以"能多组装完整产品"为目标构建富余任务池。

    默认只针对有待处理订单的产品生成富余任务。
    如果指定了 target_product_ids，则直接使用指定的产品（即使无订单也生产）。
    算法：不断找出当前库存的瓶颈组件（限制产品组装数量最多的），
    安排打印该组件，更新模拟库存，循环直到生成足够多的任务。
    """
    from ..models import Product, OrderItem
    # 确定目标产品范围
    ordered_product_ids = set(
        pid for (pid,) in db.query(OrderItem.product_id)
        .join(Order, OrderItem.order_id == Order.id)
        .filter(Order.status == "pending")
        .distinct()
        .all()
    )
    if target_product_ids:
        # 指定产品过滤时，即使没有订单也允许富余生产（specs 11.4）
        candidate_ids = target_product_ids
    else:
        candidate_ids = ordered_product_ids
    products = db.query(Product).filter(Product.id.in_(candidate_ids)).all() if candidate_ids else []
    if not products:
        return []

    # 加载所有产品 BOM: [(product_id, component_id, color, qty), ...]
    bom_list: list[tuple[int, int, str, int]] = []
    for prod in products:
        bom = db.query(ProductComponent).filter(ProductComponent.product_id == prod.id).all()
        for b in bom:
            bom_list.append((prod.id, b.component_id, b.color, b.quantity))

    if not bom_list:
        return []

    # 为每个 (component_id, color) 找到最佳打印配置
    config_map: dict[DemandKey, PrintConfig] = {}
    seen_comps: set[DemandKey] = set()
    for _, comp_id, color, _ in bom_list:
        key = (comp_id, color)
        if key in seen_comps:
            continue
        seen_comps.add(key)
        cfg = (
            db.query(PrintConfig)
            .filter(PrintConfig.component_id == comp_id)
            .order_by(PrintConfig.quantity.desc())
            .first()
        )
        if cfg:
            config_map[key] = cfg

    if not config_map:
        return []

    # 模拟库存：在已有库存 + 需求任务产出的基础上
    sim_stock: dict[DemandKey, int] = dict(projected_stock)

    def _bottleneck() -> DemandKey | None:
        """找出限制产品组装最严重的瓶颈组件。
        对每个产品，找出能组装的数量（由最少的组件决定），
        然后找出该产品中缺口最大的组件。"""
        worst_key: DemandKey | None = None
        worst_score = float('inf')  # 越低越是瓶颈

        for prod in products:
            bom = [(c, co, q) for (pid, c, co, q) in bom_list if pid == prod.id]
            if not bom:
                continue
            # 各组件能支撑的产品数
            for comp_id, color, qty in bom:
                key = (comp_id, color)
                if key not in config_map:
                    continue
                can_make = sim_stock.get(key, 0) / qty if qty > 0 else float('inf')
                if can_make < worst_score:
                    worst_score = can_make
                    worst_key = key

        return worst_key

    def _min_assemblable() -> int:
        """当前模拟库存能组装的最少完整产品数（所有产品中的最小值）。"""
        min_count = float('inf')
        for prod in products:
            bom = [(c, co, q) for (pid, c, co, q) in bom_list if pid == prod.id]
            if not bom:
                continue
            prod_count = float('inf')
            for comp_id, color, qty in bom:
                if qty <= 0:
                    continue
                prod_count = min(prod_count, sim_stock.get((comp_id, color), 0) // qty)
            if prod_count < min_count:
                min_count = prod_count
        return int(min_count) if min_count != float('inf') else 0

    base_assemblable = _min_assemblable()
    target = base_assemblable + SURPLUS_TARGET_PRODUCTS

    pool: list[tuple[int, str]] = []
    max_rounds = 500  # 安全上限

    for _ in range(max_rounds):
        if _min_assemblable() >= target:
            break  # 已达到富余目标
        key = _bottleneck()
        if key is None:
            break
        cfg = config_map.get(key)
        if cfg is None:
            break
        pool.append((cfg.id, key[1]))
        sim_stock[key] = sim_stock.get(key, 0) + cfg.quantity

    return pool

def _plan_two_phase(
    db: Session,
    target_date: date,
    num_printers: int,
    duration_hours: int,
    changeover: int,
    surplus_enabled: bool,
    target_product_ids: set[int] | None = None,
    start_time: str = "00:00",
) -> list[tuple[int, str, bool]]:
    """两阶段法 — 阶段 1：生产规划。

    从总产能出发，按 BOM 精确计算各组件目标盘数，利用溢出复用减少浪费。

    Returns:
        [(config_id, color, is_surplus), ...] 展开的任务列表
    """
    from ..models import Product, OrderItem
    from math import ceil

    # -- 1. 计算有效产能（基于操作窗口） --
    sh, sm = map(int, start_time.split(":"))
    custom_start = sh * 60 + sm
    deadline = custom_start + duration_hours * 60
    windows = _get_windows(db, target_date, custom_start, duration_hours)

    # 有效产能估算：
    # 任务可以跨窗口运行，所以"有效时间"不仅是窗口内的时间。
    # 关键约束：任务只能在操作窗口内启动（batch_0 除外，可以在 custom_start 启动）。
    # 一旦启动，任务运行到结束（可以跨越窗口间隔）。
    # 但如果打印机在窗口间隔中空闲（上一个任务已完成），它必须等到下一个窗口才能启动新任务。
    #
    # 实际可用时间 ≈ 总排班时长 - 估计的窗口间空闲时间
    # 窗口间的空闲 = 完成一个任务后需要等到下一个窗口才能启动新任务的等待时间
    #
    # 简化估算：使用 (deadline - custom_start) 减去窗口间隔的估算损耗
    total_span = deadline - custom_start  # 排班总跨度

    # 计算窗口间隔（打印机可能在这些间隔中空闲等待）
    all_starts = [custom_start]  # batch_0 可以在 custom_start 启动
    for ws, we in windows:
        if ws >= custom_start and ws < deadline:
            all_starts.append(ws)
    all_starts = sorted(set(all_starts))

    # 每个窗口的结束时间
    window_ends = []
    for ws, we in windows:
        w_end = min(we, deadline)
        if w_end > custom_start:
            window_ends.append(w_end)

    # 估算间隔损耗：各窗口间的空隙（打印机在此期间可能空闲）
    # 但因为长任务可以跨越间隔，实际损耗取决于任务时长分布
    # 保守估算：每个窗口间隔的一半会是空闲（长任务跨越，短任务不跨越）
    gap_loss = 0
    sorted_windows = sorted([(max(ws, custom_start), min(we, deadline)) for ws, we in windows if min(we, deadline) > max(ws, custom_start)])
    for i in range(len(sorted_windows) - 1):
        gap = sorted_windows[i + 1][0] - sorted_windows[i][1]
        if gap > 0:
            gap_loss += gap * 0.5  # 50% 的间隔时间被浪费

    effective_per_printer = total_span - gap_loss
    # 安全余量：Phase 2 的实际吞吐量低于估算（deadline边界、窗口间隙对齐、换料损耗）
    # 扣除 10% 避免 Phase 1 过度规划导致任务排不进去
    total_capacity = int(effective_per_printer * num_printers * 0.9)

    print(f"[two_phase P1] total_span={total_span}, gap_loss={gap_loss}, effective/printer={effective_per_printer}, total_capacity={total_capacity}, windows={len(windows)}")

    # -- 2. 确定目标产品集合及优先级 --
    # 构建产品单元队列：(priority, product_id)
    product_queue: list[tuple[int, int]] = []

    # 从订单中获取需求（受产品过滤影响）
    orders = db.query(Order).filter(Order.status == "pending").order_by(Order.created_at).all()
    for pri, order in enumerate(orders):
        for item in order.items:
            if target_product_ids and item.product_id not in target_product_ids:
                continue
            for _ in range(item.quantity):
                product_queue.append((pri, item.product_id))

    # 如果指定了产品但无订单，或开启富余，添加富余目标
    if target_product_ids:
        existing_pids = set(pid for _, pid in product_queue)
        for pid in target_product_ids:
            if pid not in existing_pids:
                # 无订单的指定产品 → 全部作为富余
                for _ in range(SURPLUS_TARGET_PRODUCTS):
                    product_queue.append((999, pid))

    if surplus_enabled:
        # 收集需要富余生产的产品 ID
        if target_product_ids:
            surplus_pids = target_product_ids
        else:
            surplus_pids = set(
                pid for (pid,) in db.query(OrderItem.product_id)
                .join(Order, OrderItem.order_id == Order.id)
                .filter(Order.status == "pending")
                .distinct().all()
            )
        for pid in surplus_pids:
            for _ in range(SURPLUS_TARGET_PRODUCTS):
                product_queue.append((1000, pid))

    if not product_queue:
        return []

    # -- 3. 加载 BOM 和打印配置 --
    all_product_ids = set(pid for _, pid in product_queue)
    products = db.query(Product).filter(Product.id.in_(all_product_ids)).all()

    # BOM: {product_id: {(comp_id, color): qty_per_product}}
    bom_map: dict[int, dict[DemandKey, int]] = {}
    for prod in products:
        bom = db.query(ProductComponent).filter(ProductComponent.product_id == prod.id).all()
        bom_map[prod.id] = {(b.component_id, b.color): b.quantity for b in bom}

    # 为每种组件选最佳打印配置（产出最多的）
    config_map: dict[DemandKey, PrintConfig] = {}
    for pid, bom in bom_map.items():
        for key in bom:
            if key in config_map:
                continue
            cfg = (
                db.query(PrintConfig)
                .filter(PrintConfig.component_id == key[0])
                .order_by(PrintConfig.quantity.desc())
                .first()
            )
            if cfg:
                config_map[key] = cfg

    # -- 4. 获取初始库存 --
    initial_supply = _get_initial_supply(db, target_date)
    overflow: dict[DemandKey, int] = dict(initial_supply)  # 溢出池（含初始库存）

    # -- 5. 贪心分配产能 --
    plan_counts: dict[tuple[int, str], int] = defaultdict(int)  # {(config_id, color): plate_count}
    remaining_capacity = total_capacity
    order_demand_boundary = len([1 for pri, _ in product_queue if pri < 999])

    for idx, (pri, pid) in enumerate(product_queue):
        bom = bom_map.get(pid)
        if not bom:
            continue

        is_surplus = idx >= order_demand_boundary

        # 计算此产品单元需要的各组件盘数（考虑溢出抵扣）
        plates_needed: dict[DemandKey, int] = {}
        time_needed = 0

        for comp_key, bom_qty in bom.items():
            cfg = config_map.get(comp_key)
            if not cfg:
                continue

            # 溢出抵扣：已有库存/溢出可以覆盖多少
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
            # 产能不足，尝试用剩余产能生产最缺的瓶颈组件
            # 按"缺口比例"排序，优先生产最缺的
            bottleneck_items = []
            for comp_key, plates in plates_needed.items():
                if plates > 0:
                    cfg = config_map[comp_key]
                    plate_time = cfg.duration_minutes + changeover
                    bottleneck_items.append((comp_key, plates, plate_time))
            # 按单盘耗时从长到短排（长的更紧急），作为 tiebreaker
            bottleneck_items.sort(key=lambda x: -x[2])

            for comp_key, plates, plate_time in bottleneck_items:
                cfg = config_map[comp_key]
                affordable = int(remaining_capacity // plate_time)
                actual = min(plates, affordable)
                if actual > 0:
                    plan_counts[(cfg.id, comp_key[1])] += actual
                    overflow[comp_key] = overflow.get(comp_key, 0) + actual * cfg.quantity - (bom.get(comp_key, 0) if actual >= plates else 0)
                    remaining_capacity -= actual * plate_time
            break  # 产能耗尽

        # 产能足够：记录盘数，更新溢出池
        remaining_capacity -= time_needed
        for comp_key, plates in plates_needed.items():
            if plates > 0:
                cfg = config_map[comp_key]
                plan_counts[(cfg.id, comp_key[1])] += plates
                produced = plates * cfg.quantity
                consumed = bom[comp_key]
                # 溢出 = 已有溢出 - 消耗 + 新产出（抵扣后的净溢出）
                available = overflow.get(comp_key, 0)
                if available >= bom[comp_key]:
                    # 全部由溢出满足，新产出全部成为溢出
                    overflow[comp_key] = available - consumed + produced
                else:
                    # 部分由溢出满足，新产出覆盖剩余需求后的溢出
                    overflow[comp_key] = produced - (consumed - available)
            else:
                # 不需要额外打印，仅消耗溢出
                overflow[comp_key] = overflow.get(comp_key, 0) - bom.get(comp_key, 0)

    # -- 6. 展开为任务列表 --
    # 区分订单任务和富余任务：前 order_demand_boundary 个产品单元是订单需求
    # 简化处理：统一标记。因为两阶段法中订单需求和富余是混合规划的，
    # 用初始供给来判断哪些是"额外的"
    tasks: list[tuple[int, str, bool]] = []
    for (config_id, color), count in plan_counts.items():
        for _ in range(count):
            tasks.append((config_id, color, False))

    # 标记富余：如果产能主要用在订单之外，做一个近似标记
    # 实际上两阶段法模糊了订单/富余边界，这里用一个启发式：
    # 计算订单需求的总盘数，超出部分标记为富余
    order_demand, _ = _calc_ordered_tasks(db, target_date, target_product_ids=target_product_ids)
    order_plate_keys: dict[tuple[int, str], int] = defaultdict(int)
    for item in order_demand:
        order_plate_keys[(item[0], item[1])] += 1

    tasks_final: list[tuple[int, str, bool]] = []
    remaining_order: dict[tuple[int, str], int] = dict(order_plate_keys)
    for config_id, color, _ in tasks:
        key = (config_id, color)
        if remaining_order.get(key, 0) > 0:
            remaining_order[key] -= 1
            tasks_final.append((config_id, color, False))
        else:
            tasks_final.append((config_id, color, True))

    return tasks_final


def generate_plan(
    db: Session, target_date: date, surplus_enabled: bool,
    start_time: str = "00:00", duration_hours: int = 24,
    strategy: str = "product_first", target_product_ids: list[int] | None = None,
    sync_strength: int = 50,
) -> PrintPlan:
    """生成排班表

    Args:
        strategy: "product_first"（优先凑齐发货）、"utilization"（最大化利用率）
                  或 "two_phase"（智能规划）
        target_product_ids: 指定产品过滤，None 表示不过滤
        sync_strength: 0~100，同步强度
    """
    changeover = _get_changeover_minutes(db)
    printers = db.query(Printer).all()
    if not printers:
        raise ValueError("没有可用的打印机")

    sh, sm = map(int, start_time.split(":"))
    custom_start = sh * 60 + sm
    deadline = custom_start + duration_hours * 60

    windows = _get_windows(db, target_date, custom_start, duration_hours)

    # 产品过滤集合
    product_filter = set(target_product_ids) if target_product_ids else None

    # ── 两阶段法：独立路径 ──
    if strategy == "two_phase":
        # 阶段 1：生产规划
        two_phase_tasks = _plan_two_phase(
            db, target_date, len(printers), duration_hours, changeover,
            surplus_enabled, product_filter, start_time=start_time,
        )

        config_cache: dict[int, PrintConfig] = {}
        for cid, _, _ in two_phase_tasks:
            if cid not in config_cache:
                config_cache[cid] = db.get(PrintConfig, cid)

        # 创建排班表
        plan = PrintPlan(date=target_date, start_time=start_time, duration_hours=duration_hours, status="draft")
        db.add(plan)
        db.flush()

        # 阶段 2：时间排程（简化贪心：idle + duration desc）
        batch_order = 0
        printer_available = {p.id: custom_start for p in printers}
        remaining: list[tuple[int, str, bool]] = list(two_phase_tasks)

        def _pick_two_phase(start: int, anchor_dur: int | None = None) -> tuple[int, str, bool] | None:
            """Phase 2 task picker: minimize idle, prefer longer tasks, with sync penalty."""
            best_idx = -1
            best_score: tuple | None = None
            for i, (cid, color, is_surplus) in enumerate(remaining):
                cfg = config_cache[cid]
                dur = cfg.duration_minutes
                if start + dur > deadline:
                    continue
                idle = _idle_after(start, dur, changeover, windows)
                # 同步惩罚
                if anchor_dur is not None and anchor_dur > 0 and sync_strength > 0:
                    sp = abs(dur - anchor_dur) / anchor_dur * (sync_strength / 100)
                else:
                    sp = 0.0
                score = (sp, idle)  # sync penalty → idle（不偏好长短，保持Phase1的组件平衡）
                if best_idx == -1 or score < best_score:
                    best_idx = i
                    best_score = score
            if best_idx == -1:
                return None
            return remaining.pop(best_idx)

        while remaining:
            earliest = min(printer_available.values())

            if batch_order == 0:
                start = custom_start
            else:
                start = _find_next_start(earliest, windows)
                if start is None:
                    break

            if start >= deadline:
                break

            available_printers = [p for p in printers if printer_available[p.id] <= start]
            if not available_printers:
                next_start = _find_next_start(earliest + 1, windows)
                if next_start is None or next_start >= deadline:
                    break
                for pid_key in printer_available:
                    if printer_available[pid_key] <= earliest:
                        printer_available[pid_key] = next_start
                        break
                continue

            batch = PrintBatch(plan_id=plan.id, start_time=f"{start // 60:02d}:{start % 60:02d}", batch_order=batch_order)
            db.add(batch)
            db.flush()

            batch_tasks_added = 0
            batch_anchor: int | None = None  # 锚定时长
            for printer in available_printers:
                item = _pick_two_phase(start, batch_anchor)
                if item is None:
                    break
                config_id, color, is_surplus = item
                cfg = config_cache[config_id]
                end_min = start + cfg.duration_minutes
                if batch_anchor is None:
                    batch_anchor = cfg.duration_minutes  # 第一台设锚

                task = PrintTask(
                    batch_id=batch.id,
                    printer_id=printer.id,
                    print_config_id=config_id,
                    color=color,
                    is_surplus=is_surplus,
                    start_time=f"{start // 60:02d}:{start % 60:02d}",
                    end_time=f"{end_min // 60:02d}:{end_min % 60:02d}",
                )
                db.add(task)
                printer_available[printer.id] = end_min + changeover
                batch_tasks_added += 1

            if batch_tasks_added == 0:
                db.delete(batch)
                print(f"[two_phase P2] no tasks fit at start={start}, remaining={len(remaining)}, deadline={deadline}")
                break

            batch_order += 1

        # 详细日志：组件分布
        from collections import Counter
        planned_comps = Counter()
        for cid, color, _ in two_phase_tasks:
            cfg = config_cache[cid]
            planned_comps[f"{cfg.component.name}({color}) {cfg.duration_minutes}min×{cfg.quantity}"] += 1

        scheduled_comps = Counter()
        for b in plan.batches:
            for t in b.tasks:
                cfg = config_cache[t.print_config_id]
                scheduled_comps[f"{cfg.component.name}({t.color}) {cfg.duration_minutes}min×{cfg.quantity}"] += 1

        print(f"\n[two_phase] phase1_tasks={len(two_phase_tasks)}, scheduled={batch_order} batches, unscheduled={len(remaining)}")
        print(f"[two_phase] PLANNED per component:")
        for k, v in planned_comps.most_common():
            print(f"  {k}: {v} plates")
        print(f"[two_phase] SCHEDULED per component:")
        for k, v in scheduled_comps.most_common():
            sched_pct = ""
            if planned_comps[k] > 0:
                sched_pct = f" ({v}/{planned_comps[k]})"
            print(f"  {k}: {v} plates{sched_pct}")
        print()
        db.commit()
        db.refresh(plan)
        return plan

    # ── 原有策略：product_first / utilization ──

    # 1. 按订单 FIFO 顺序计算需求任务
    task_items, projected_supply = _calc_ordered_tasks(db, target_date, product_filter)

    config_cache: dict[int, PrintConfig] = {}
    for item in task_items:
        cid = item[0]
        if cid not in config_cache:
            config_cache[cid] = db.get(PrintConfig, cid)

    # 2. 根据策略初始化上下文
    use_product_first = strategy == "product_first"

    sim_supply: dict[DemandKey, int] = {}
    product_units: list[tuple[int, int]] = []
    bom_cache: dict[int, dict[DemandKey, int]] = {}
    assembled: set[int] = set()

    if use_product_first:
        orders = db.query(Order).filter(Order.status == "pending").order_by(Order.created_at).all()
        product_units, bom_cache = _build_product_context(db, orders, product_filter)
        # 指定产品过滤时，即使无订单也构建产品上下文（用于富余任务的凑齐评分）
        if product_filter:
            existing_pids = set(pid for _, pid in product_units)
            for pid in product_filter:
                if pid not in existing_pids:
                    for _ in range(SURPLUS_TARGET_PRODUCTS):
                        product_units.append((999, pid))  # 低优先级合成单元
                    if pid not in bom_cache:
                        bom = db.query(ProductComponent).filter(ProductComponent.product_id == pid).all()
                        bom_cache[pid] = {(b.component_id, b.color): b.quantity for b in bom}
        sim_supply = _get_initial_supply(db, target_date)
        _try_assemble(sim_supply, product_units, bom_cache, assembled)

    # 3. 富余任务：满足订单需求后，继续用剩余产能打印
    surplus_tasks: list[tuple[int, str]] = []
    if surplus_enabled:
        surplus_tasks = _build_surplus_tasks(db, projected_supply, product_filter)
        for cid, _ in surplus_tasks:
            if cid not in config_cache:
                config_cache[cid] = db.get(PrintConfig, cid)

    # 4. 创建排班表
    plan = PrintPlan(date=target_date, start_time=start_time, duration_hours=duration_hours, status="draft")
    db.add(plan)
    db.flush()

    # 5. 分批调度
    batch_order = 0
    printer_available = {p.id: custom_start for p in printers}
    remaining_tasks: list = list(task_items)
    remaining_surplus: list = list(surplus_tasks) if surplus_enabled else []

    def _next_task(start: int, batch_anchor: int | None = None) -> tuple[int, str, bool] | None:
        """优先从需求任务中取，取完后从富余池中取。返回 (config_id, color, is_surplus)"""
        if remaining_tasks:
            if use_product_first:
                result = _pick_task(
                    remaining_tasks, config_cache, start, changeover, windows, deadline,
                    sim_supply, product_units, bom_cache, assembled,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            else:
                result = _pick_task(
                    remaining_tasks, config_cache, start, changeover, windows, deadline,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            if result:
                return (result[0], result[1], False)
            # 需求池里都放不下了（全部超出 deadline），清空以避免死循环
            if not any(start + config_cache[t[0]].duration_minutes <= deadline for t in remaining_tasks):
                remaining_tasks.clear()
        if remaining_surplus:
            if use_product_first:
                result = _pick_task(
                    remaining_surplus, config_cache, start, changeover, windows, deadline,
                    sim_supply, product_units, bom_cache, assembled,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            else:
                result = _pick_task(
                    remaining_surplus, config_cache, start, changeover, windows, deadline,
                    anchor_duration=batch_anchor, sync_strength=sync_strength,
                )
            if result:
                return (result[0], result[1], True)
            if not any(start + config_cache[t[0]].duration_minutes <= deadline for t in remaining_surplus):
                remaining_surplus.clear()
        return None

    while True:
        if not remaining_tasks and not remaining_surplus:
            break

        earliest = min(printer_available.values())

        if batch_order == 0:
            start = custom_start
        else:
            start = _find_next_start(earliest, windows)
            if start is None:
                break

        if start >= deadline:
            break

        available_printers = [p for p in printers if printer_available[p.id] <= start]
        if not available_printers:
            # 安全兜底：防止死循环
            next_start = _find_next_start(earliest + 1, windows)
            if next_start is None or next_start >= deadline:
                break
            for pid in printer_available:
                if printer_available[pid] <= earliest:
                    printer_available[pid] = next_start
                    break
            continue

        batch = PrintBatch(plan_id=plan.id, start_time=f"{start // 60:02d}:{start % 60:02d}", batch_order=batch_order)
        db.add(batch)
        db.flush()

        batch_tasks_added = 0
        batch_anchor: int | None = None  # 锚定时长
        for printer in available_printers:
            item = _next_task(start, batch_anchor)
            if item is None:
                break
            config_id, color, is_surplus = item
            cfg = config_cache[config_id]
            end_min = start + cfg.duration_minutes
            if batch_anchor is None:
                batch_anchor = cfg.duration_minutes  # 第一台设锚

            # product_first 策略：更新模拟库存，检查是否有产品可组装
            if use_product_first:
                comp_key = (cfg.component_id, color)
                sim_supply[comp_key] = sim_supply.get(comp_key, 0) + cfg.quantity
                _try_assemble(sim_supply, product_units, bom_cache, assembled)

            task = PrintTask(
                batch_id=batch.id,
                printer_id=printer.id,
                print_config_id=config_id,
                color=color,
                is_surplus=is_surplus,
                start_time=f"{start // 60:02d}:{start % 60:02d}",
                end_time=f"{end_min // 60:02d}:{end_min % 60:02d}",
            )
            db.add(task)
            printer_available[printer.id] = end_min + changeover
            batch_tasks_added += 1

        if batch_tasks_added == 0:
            db.delete(batch)
            break

        batch_order += 1

    db.commit()
    db.refresh(plan)
    return plan

