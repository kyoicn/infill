"""
排班算法服务

算法步骤：
1. 计算组件需求（按 component_id + color 维度）
2. 选择打印配置组合覆盖需求
3. 按批次调度，尽量同步结束
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

    # 按耗时排序（降序），方便分批
    config_cache: dict[int, PrintConfig] = {}
    for cid, _ in task_items:
        if cid not in config_cache:
            config_cache[cid] = db.get(PrintConfig, cid)

    task_items.sort(key=lambda item: config_cache[item[0]].duration_minutes, reverse=True)

    # 2. 创建排班表
    plan = PrintPlan(date=target_date, start_time=start_time, duration_hours=duration_hours, status="draft")
    db.add(plan)
    db.flush()

    # 3. 分批调度
    batch_order = 0
    printer_available = {p.id: custom_start for p in printers}
    remaining_tasks = list(task_items)

    while remaining_tasks:
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

        batch = PrintBatch(plan_id=plan.id, start_time=f"{start // 60:02d}:{start % 60:02d}", batch_order=batch_order)
        db.add(batch)
        db.flush()

        batch_tasks_added = 0
        for printer in available_printers:
            if not remaining_tasks:
                break
            config_id, color = remaining_tasks.pop(0)
            cfg = config_cache[config_id]
            end_min = start + cfg.duration_minutes
            task = PrintTask(
                batch_id=batch.id,
                printer_id=printer.id,
                print_config_id=config_id,
                color=color,
                start_time=f"{start // 60:02d}:{start % 60:02d}",
                end_time=f"{end_min // 60:02d}:{end_min % 60:02d}",
            )
            db.add(task)
            printer_available[printer.id] = end_min + changeover
            batch_tasks_added += 1

        if batch_tasks_added == 0:
            break

        batch_order += 1

    db.commit()
    db.refresh(plan)
    return plan
