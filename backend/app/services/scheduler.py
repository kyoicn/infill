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
from .scheduler_core import (
    ConfigInfo, ScheduledTask, DemandKey,
    DEFAULT_CHANGEOVER_MINUTES,
    SURPLUS_TARGET_PRODUCTS,
    find_next_start as _find_next_start,
    idle_after as _idle_after,
    product_completion_score as _product_completion_score,
    try_assemble as _try_assemble,
    pick_task as _pick_task_core,
    compute_effective_capacity,
    plan_two_phase as _plan_two_phase_core,
    schedule_tasks as _schedule_tasks_core,
)


def _get_changeover_minutes(db: Session) -> int:
    cfg = db.query(SystemConfig).filter(SystemConfig.key == "changeover_minutes").first()
    return int(cfg.value) if cfg else DEFAULT_CHANGEOVER_MINUTES


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

# _find_next_start 和 _idle_after 已从 scheduler_core 导入



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


# _product_completion_score 和 _try_assemble 已从 scheduler_core 导入



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
    """两阶段法 — 阶段 1：生产规划（DB 包装层）。

    查询数据库获取数据，委托给 scheduler_core.plan_two_phase 执行纯算法。
    """
    from ..models import Product, OrderItem

    sh, sm = map(int, start_time.split(":"))
    custom_start = sh * 60 + sm
    deadline = custom_start + duration_hours * 60
    windows = _get_windows(db, target_date, custom_start, duration_hours)

    # -- 构建产品队列 --
    product_queue: list[tuple[int, int]] = []
    orders = db.query(Order).filter(Order.status == "pending").order_by(Order.created_at).all()
    for pri, order in enumerate(orders):
        for item in order.items:
            if target_product_ids and item.product_id not in target_product_ids:
                continue
            for _ in range(item.quantity):
                product_queue.append((pri, item.product_id))

    if target_product_ids:
        existing_pids = set(pid for _, pid in product_queue)
        for pid in target_product_ids:
            if pid not in existing_pids:
                for _ in range(SURPLUS_TARGET_PRODUCTS):
                    product_queue.append((999, pid))

    if surplus_enabled:
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

    # -- 加载 BOM --
    all_product_ids = set(pid for _, pid in product_queue)
    products = db.query(Product).filter(Product.id.in_(all_product_ids)).all()
    bom_map: dict[int, dict[DemandKey, int]] = {}
    for prod in products:
        bom = db.query(ProductComponent).filter(ProductComponent.product_id == prod.id).all()
        bom_map[prod.id] = {(b.component_id, b.color): b.quantity for b in bom}

    # -- 加载打印配置 → ConfigInfo --
    config_map: dict[DemandKey, ConfigInfo] = {}
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
                config_map[key] = ConfigInfo(
                    id=cfg.id,
                    component_id=cfg.component_id,
                    component_name=cfg.component.name,
                    quantity=cfg.quantity,
                    duration_minutes=cfg.duration_minutes,
                )

    # -- 初始库存 --
    initial_supply = _get_initial_supply(db, target_date)

    # -- 订单需求盘数（用于标记富余） --
    order_demand, _ = _calc_ordered_tasks(db, target_date, target_product_ids=target_product_ids)
    order_demand_counts: dict[tuple[int, str], int] = defaultdict(int)
    for item in order_demand:
        order_demand_counts[(item[0], item[1])] += 1

    # -- 委托给核心算法 --
    return _plan_two_phase_core(
        num_printers=num_printers,
        duration_hours=duration_hours,
        changeover=changeover,
        surplus_enabled=surplus_enabled,
        windows=windows,
        custom_start=custom_start,
        deadline=deadline,
        product_queue=product_queue,
        bom_map=bom_map,
        config_map=config_map,
        initial_supply=initial_supply,
        order_demand_counts=dict(order_demand_counts),
    )


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

        # 构建 ConfigInfo 映射供 core 使用
        config_info: dict[int, ConfigInfo] = {}
        for cid, cfg in config_cache.items():
            if cid not in config_info:
                config_info[cid] = ConfigInfo(
                    id=cfg.id,
                    component_id=cfg.component_id,
                    component_name=cfg.component.name,
                    quantity=cfg.quantity,
                    duration_minutes=cfg.duration_minutes,
                )

        # 阶段 2：委托给核心算法
        scheduled = _schedule_tasks_core(
            two_phase_tasks, config_info, len(printers), windows,
            custom_start, deadline, changeover, sync_strength,
        )

        # 创建排班表并持久化
        plan = PrintPlan(date=target_date, start_time=start_time, duration_hours=duration_hours, status="draft")
        db.add(plan)
        db.flush()

        # 将 ScheduledTask 转换为 DB 模型
        printer_list = list(printers)
        batches_by_idx: dict[int, PrintBatch] = {}
        for st in scheduled:
            if st.batch_index not in batches_by_idx:
                batch = PrintBatch(
                    plan_id=plan.id,
                    start_time=f"{st.start_min // 60:02d}:{st.start_min % 60:02d}",
                    batch_order=st.batch_index,
                )
                db.add(batch)
                db.flush()
                batches_by_idx[st.batch_index] = batch

            task = PrintTask(
                batch_id=batches_by_idx[st.batch_index].id,
                printer_id=printer_list[st.printer_index].id,
                print_config_id=st.config_id,
                color=st.color,
                is_surplus=st.is_surplus,
                start_time=f"{st.start_min // 60:02d}:{st.start_min % 60:02d}",
                end_time=f"{st.end_min // 60:02d}:{st.end_min % 60:02d}",
            )
            db.add(task)

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

