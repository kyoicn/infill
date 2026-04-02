"""
排班算法服务

算法步骤：
1. 计算组件需求（按 component_id + color 维度）
2. 选择打印配置组合覆盖需求
3. 智能分批调度：
   - 临近操作窗口长间隔（如过夜）时，优先安排耗时长的任务，使打印机在无人值守期持续工作
   - 窗口间隔短时，优先安排耗时短的任务，保留长任务给后续长间隔使用
"""

from datetime import date, timedelta
from collections import defaultdict

from sqlalchemy.orm import Session

from ..models import (
    Order, ProductComponent, Inventory,
    PrintConfig, Printer, ScheduleConfig, SystemConfig,
    PrintPlan, PrintBatch, PrintTask,
)

# 需求维度：(component_id, color)
DemandKey = tuple[int, str]


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


def _calc_component_demand(db: Session, target_date: date, surplus_enabled: bool) -> dict[DemandKey, int]:
    """计算各组件+颜色的需求量 = 订单需求 - 库存 - 已排班的产出"""
    # 订单需求（按 component_id + color）
    demand: dict[DemandKey, int] = defaultdict(int)
    pending_orders = db.query(Order).filter(Order.status == "pending").all()
    for order in pending_orders:
        for item in order.items:
            bom = db.query(ProductComponent).filter(ProductComponent.product_id == item.product_id).all()
            for b in bom:
                demand[(b.component_id, b.color)] += b.quantity * item.quantity

    # 当前库存（按 component_id + color）
    inventories = db.query(Inventory).all()
    supply: dict[DemandKey, int] = {(inv.component_id, inv.color): inv.quantity for inv in inventories}

    # 加上早于 target_date 的已有排班的产出
    earlier_plans = db.query(PrintPlan).filter(PrintPlan.date < target_date).all()
    for plan in earlier_plans:
        for batch in plan.batches:
            for task in batch.tasks:
                cfg = db.get(PrintConfig, task.print_config_id)
                if cfg:
                    key = (cfg.component_id, task.color)
                    supply[key] = supply.get(key, 0) + cfg.quantity

    net_demand: dict[DemandKey, int] = {}
    for key, qty in demand.items():
        net = qty - supply.get(key, 0)
        if net > 0:
            net_demand[key] = net

    return net_demand


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


def _find_next_start(current_min: int, windows: list[tuple[int, int]], changeover: int) -> int | None:
    """找到 >= current_min 的最早可启动时间（必须在操作窗口内）"""
    for ws, we in windows:
        if current_min + changeover <= we and current_min <= we:
            return max(current_min, ws)
    return None


def _gap_after(start: int, windows: list[tuple[int, int]]) -> int:
    """计算从 start 所在窗口结束后，到下一个窗口开始的间隔（分钟）。
    间隔越大说明即将进入长时间无人值守期（如过夜），应安排耗时长的任务。"""
    current_end = None
    for ws, we in windows:
        if ws <= start <= we:
            current_end = we
            break
    if current_end is None:
        return 0
    for ws, _we in windows:
        if ws > current_end:
            return ws - current_end
    return 0


def _pick_task(remaining: list[tuple[int, str]], config_cache: dict[int, PrintConfig],
               prefer_long: bool) -> tuple[int, str]:
    """从剩余任务中选择一个：prefer_long=True 选最长的，否则选最短的。"""
    best_idx = 0
    best_dur = config_cache[remaining[0][0]].duration_minutes
    for i in range(1, len(remaining)):
        dur = config_cache[remaining[i][0]].duration_minutes
        if prefer_long and dur > best_dur:
            best_idx, best_dur = i, dur
        elif not prefer_long and dur < best_dur:
            best_idx, best_dur = i, dur
    return remaining.pop(best_idx)


def _build_surplus_tasks(db: Session, projected_stock: dict[DemandKey, int]) -> list[tuple[int, str]]:
    """以"能多组装完整产品"为目标构建富余任务池。

    算法：不断找出当前库存的瓶颈组件（限制产品组装数量最多的），
    安排打印该组件，更新模拟库存，循环直到生成足够多的任务。
    """
    from ..models import Product
    products = db.query(Product).all()
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

    pool: list[tuple[int, str]] = []
    max_rounds = 200  # 安全上限，调度循环会按时间截断

    for _ in range(max_rounds):
        key = _bottleneck()
        if key is None:
            break
        cfg = config_map.get(key)
        if cfg is None:
            break
        pool.append((cfg.id, key[1]))
        sim_stock[key] = sim_stock.get(key, 0) + cfg.quantity

    return pool


def generate_plan(db: Session, target_date: date, surplus_enabled: bool, start_time: str = "08:00", duration_hours: int = 24) -> PrintPlan:
    """生成排班表"""
    changeover = _get_changeover_minutes(db)
    printers = db.query(Printer).all()
    if not printers:
        raise ValueError("没有可用的打印机")

    sh, sm = map(int, start_time.split(":"))
    custom_start = sh * 60 + sm
    deadline = custom_start + duration_hours * 60

    windows = _get_windows(db, target_date, custom_start, duration_hours)

    # 1. 计算需求并选择打印配置
    demand = _calc_component_demand(db, target_date, surplus_enabled)
    task_items = _select_configs(db, demand)  # [(config_id, color), ...]

    config_cache: dict[int, PrintConfig] = {}
    for cid, _ in task_items:
        if cid not in config_cache:
            config_cache[cid] = db.get(PrintConfig, cid)

    # 富余任务：满足订单需求后，继续用剩余产能打印
    # 计算需求任务排完后的预计库存，作为富余生产的起点
    surplus_tasks: list[tuple[int, str]] = []
    if surplus_enabled:
        # 当前库存
        inventories = db.query(Inventory).all()
        projected: dict[DemandKey, int] = {(inv.component_id, inv.color): inv.quantity for inv in inventories}
        # 加上需求任务的产出
        for cid, color in task_items:
            cfg = config_cache[cid]
            key = (cfg.component_id, color)
            projected[key] = projected.get(key, 0) + cfg.quantity
        surplus_tasks = _build_surplus_tasks(db, projected)
        for cid, _ in surplus_tasks:
            if cid not in config_cache:
                config_cache[cid] = db.get(PrintConfig, cid)

    # 2. 创建排班表
    plan = PrintPlan(date=target_date, start_time=start_time, duration_hours=duration_hours, status="draft")
    db.add(plan)
    db.flush()

    # 3. 分批调度 — 智能分配：临近长间隔时安排长任务，间隔短时安排短任务
    batch_order = 0
    printer_available = {p.id: custom_start for p in printers}
    remaining_tasks = list(task_items)
    surplus_idx = 0  # 富余任务轮询指针

    def _next_task(prefer_long: bool) -> tuple[int, str, bool] | None:
        """优先从需求任务中取，取完后从富余池中轮询。返回 (config_id, color, is_surplus)"""
        nonlocal surplus_idx
        if remaining_tasks:
            cid, color = _pick_task(remaining_tasks, config_cache, prefer_long)
            return (cid, color, False)
        if surplus_enabled and surplus_tasks and surplus_idx < len(surplus_tasks):
            cid, color = surplus_tasks[surplus_idx]
            surplus_idx += 1
            return (cid, color, True)
        return None

    while True:
        if not remaining_tasks and (not surplus_enabled or surplus_idx >= len(surplus_tasks)):
            break

        earliest = min(printer_available.values())

        if batch_order == 0:
            start = custom_start
        else:
            start = _find_next_start(earliest, windows, changeover)
            if start is None:
                last_window_end = windows[-1][1] if windows else 1380
                start = last_window_end - changeover
                if start < earliest:
                    break

        if start >= deadline:
            break

        available_printers = [p for p in printers if printer_available[p.id] <= start + changeover]
        if not available_printers:
            available_printers = printers[:1]

        # 判断当前窗口后的间隔：> 120 分钟视为长间隔，应安排长任务以跨越空闲期
        gap = _gap_after(start, windows)
        prefer_long = gap > 120

        batch = PrintBatch(plan_id=plan.id, start_time=f"{start // 60:02d}:{start % 60:02d}", batch_order=batch_order)
        db.add(batch)
        db.flush()

        batch_tasks_added = 0
        for printer in available_printers:
            item = _next_task(prefer_long)
            if item is None:
                break
            config_id, color, is_surplus = item
            cfg = config_cache[config_id]
            end_min = start + cfg.duration_minutes
            if end_min > deadline:
                break
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
