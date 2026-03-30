"""
排班算法服务

算法步骤：
1. 计算组件需求（待处理订单 + 富余 - 库存）
2. 选择打印配置组合覆盖需求
3. 按批次调度，尽量同步结束
"""

from datetime import date
from collections import defaultdict

from sqlalchemy.orm import Session

from ..models import (
    Order, OrderItem, ProductComponent, Inventory,
    PrintConfig, Printer, ScheduleConfig, SystemConfig,
    PrintPlan, PrintBatch, PrintTask, Component,
)


def _get_changeover_minutes(db: Session) -> int:
    cfg = db.query(SystemConfig).filter(SystemConfig.key == "changeover_minutes").first()
    return int(cfg.value) if cfg else 15


def _get_windows(db: Session, target_date: date) -> list[tuple[int, int]]:
    """获取目标日期的操作时间窗口，返回 [(start_min, end_min), ...]"""
    dow = target_date.weekday()  # 0=周一
    cfg = db.query(ScheduleConfig).filter(ScheduleConfig.day_of_week == dow).first()
    if not cfg:
        # 默认窗口
        return [(480, 720), (750, 1080), (1110, 1380)]  # 8-12, 12:30-18, 18:30-23
    windows = []
    for w in cfg.windows:
        sh, sm = map(int, w["start"].split(":"))
        eh, em = map(int, w["end"].split(":"))
        windows.append((sh * 60 + sm, eh * 60 + em))
    return sorted(windows)


def _calc_component_demand(db: Session, target_date: date, surplus_enabled: bool) -> dict[int, int]:
    """计算各组件需求量 = 订单需求 - 库存 - 已排班的产出"""
    # 订单需求
    demand: dict[int, int] = defaultdict(int)
    pending_orders = db.query(Order).filter(Order.status == "pending").all()
    for order in pending_orders:
        for item in order.items:
            bom = db.query(ProductComponent).filter(ProductComponent.product_id == item.product_id).all()
            for b in bom:
                demand[b.component_id] += b.quantity * item.quantity

    # 当前库存
    inventories = db.query(Inventory).all()
    supply: dict[int, int] = {inv.component_id: inv.quantity for inv in inventories}

    # 加上日期早于 target_date 的已有排班的产出
    earlier_plans = db.query(PrintPlan).filter(PrintPlan.date < target_date).all()
    for plan in earlier_plans:
        for batch in plan.batches:
            for task in batch.tasks:
                cfg = db.get(PrintConfig, task.print_config_id)
                if cfg:
                    supply[cfg.component_id] = supply.get(cfg.component_id, 0) + cfg.quantity

    net_demand: dict[int, int] = {}
    for comp_id, qty in demand.items():
        net = qty - supply.get(comp_id, 0)
        if net > 0:
            net_demand[comp_id] = net

    return net_demand


def _select_configs(db: Session, demand: dict[int, int]) -> list[int]:
    """为每个组件选择打印配置组合，返回 print_config_id 列表（可重复）"""
    tasks: list[int] = []

    for comp_id, needed in demand.items():
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
            # 贪心：优先用大配置
            best = None
            for cfg in configs:
                if cfg.quantity <= remaining:
                    best = cfg
                    break
            if best is None:
                # 所有配置都超过剩余需求，用最小配置
                best = configs[-1]
            tasks.append(best.id)
            remaining -= best.quantity

    return tasks


def _find_next_start(current_min: int, windows: list[tuple[int, int]], changeover: int) -> int | None:
    """找到 >= current_min 的最早可启动时间（必须在操作窗口内）"""
    for ws, we in windows:
        if current_min + changeover <= we and current_min <= we:
            return max(current_min, ws)
    return None  # 今天窗口内无法启动


def generate_plan(db: Session, target_date: date, surplus_enabled: bool, start_time: str = "08:00") -> PrintPlan:
    """生成排班表"""
    changeover = _get_changeover_minutes(db)
    windows = _get_windows(db, target_date)
    printers = db.query(Printer).all()
    num_printers = len(printers)

    if num_printers == 0:
        raise ValueError("没有可用的打印机")

    # 解析自定义开始时间
    sh, sm = map(int, start_time.split(":"))
    custom_start = sh * 60 + sm

    # 1. 计算需求并选择打印配置
    demand = _calc_component_demand(db, target_date, surplus_enabled)
    task_config_ids = _select_configs(db, demand)

    # 按耗时排序（降序），方便分批
    config_cache: dict[int, PrintConfig] = {}
    for cid in task_config_ids:
        if cid not in config_cache:
            config_cache[cid] = db.get(PrintConfig, cid)

    task_config_ids.sort(key=lambda cid: config_cache[cid].duration_minutes, reverse=True)

    # 2. 创建排班表
    plan = PrintPlan(date=target_date, status="draft")
    db.add(plan)
    db.flush()

    # 3. 分批调度
    batch_order = 0
    # 首批使用自定义开始时间（可在操作窗口外）
    printer_available = {p.id: custom_start for p in printers}
    remaining_tasks = list(task_config_ids)

    while remaining_tasks:
        # 找出最早可开始的时间
        earliest = min(printer_available.values())

        if batch_order == 0:
            # 首批：直接使用自定义开始时间，不受操作窗口限制
            start = custom_start
        else:
            # 后续批次：需要人去收菜换版，必须在操作窗口内
            start = _find_next_start(earliest, windows, changeover)

            if start is None:
                # 今天窗口都排满了，最后一批安排过夜任务
                last_window_end = windows[-1][1] if windows else 1380
                start = last_window_end - changeover
                if start < earliest:
                    break  # 实在排不下了

        # 找出此时可用的打印机
        available_printers = [p for p in printers if printer_available[p.id] <= start + changeover]
        if not available_printers:
            available_printers = printers[:1]  # fallback

        batch = PrintBatch(plan_id=plan.id, start_time=f"{start // 60:02d}:{start % 60:02d}", batch_order=batch_order)
        db.add(batch)
        db.flush()

        batch_tasks_added = 0
        for printer in available_printers:
            if not remaining_tasks:
                break
            config_id = remaining_tasks.pop(0)
            cfg = config_cache[config_id]
            end_min = start + cfg.duration_minutes
            task = PrintTask(
                batch_id=batch.id,
                printer_id=printer.id,
                print_config_id=config_id,
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
