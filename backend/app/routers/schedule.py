from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PrintPlan, PrintBatch, PrintTask
from pydantic import BaseModel
from ..schemas import PrintPlanOut, GeneratePlanRequest
from ..services.scheduler import generate_plan

router = APIRouter(prefix="/api/schedule", tags=["排班"])


@router.get("/plans", response_model=list[PrintPlanOut])
def list_plans(db: Session = Depends(get_db)):
    return db.query(PrintPlan).order_by(PrintPlan.date.desc()).all()


@router.get("/plans/{plan_id}", response_model=PrintPlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(PrintPlan, plan_id)
    if not plan:
        raise HTTPException(404, "排班表不存在")
    return plan


@router.post("/generate", response_model=PrintPlanOut)
def generate_schedule(req: GeneratePlanRequest, db: Session = Depends(get_db)):
    """生成排班表，检查时间段是否与已有排班重叠"""
    from datetime import datetime, timedelta
    # 新排班的绝对起止时间
    sh, sm = map(int, req.start_time.split(":"))
    new_start = datetime.combine(req.date, datetime.min.time()) + timedelta(hours=sh, minutes=sm)
    new_end = new_start + timedelta(hours=req.duration_hours)

    # 检查与已有排班是否重叠
    for plan in db.query(PrintPlan).all():
        psh, psm = map(int, plan.start_time.split(":"))
        p_start = datetime.combine(plan.date, datetime.min.time()) + timedelta(hours=psh, minutes=psm)
        p_end = p_start + timedelta(hours=plan.duration_hours)
        if new_start < p_end and new_end > p_start:
            raise HTTPException(400, f"与已有排班（{plan.date} {plan.start_time}，{plan.duration_hours}h）时间重叠")
    try:
        plan = generate_plan(
            db, req.date, req.surplus_enabled, req.start_time, req.duration_hours,
            strategy=req.strategy, target_product_ids=req.target_product_ids,
            sync_strength=req.sync_strength,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return plan


@router.post("/plans/{plan_id}/confirm")
def confirm_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(PrintPlan, plan_id)
    if not plan:
        raise HTTPException(404, "排班表不存在")
    plan.status = "confirmed"
    db.commit()
    return {"ok": True}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    """删除排班，同时删除所有晚于该日期的排班"""
    plan = db.get(PrintPlan, plan_id)
    if not plan:
        raise HTTPException(404, "排班表不存在")
    # 找出所有 >= 该日期的排班（含自身）
    later_plans = db.query(PrintPlan).filter(PrintPlan.date >= plan.date).order_by(PrintPlan.date).all()
    deleted_dates = [p.date.isoformat() for p in later_plans]
    for p in later_plans:
        db.delete(p)
    db.commit()
    return {"ok": True, "deleted_dates": deleted_dates}


# ---- 批次/任务编辑 ----

@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(PrintTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.put("/tasks/{task_id}/config/{new_config_id}")
def replace_task_config(task_id: int, new_config_id: int, db: Session = Depends(get_db)):
    task = db.get(PrintTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    from ..models import PrintConfig
    cfg = db.get(PrintConfig, new_config_id)
    if not cfg:
        raise HTTPException(404, "打印配置不存在")
    task.print_config_id = new_config_id
    # 更新结束时间
    start_h, start_m = map(int, task.start_time.split(":"))
    total_min = start_h * 60 + start_m + cfg.duration_minutes
    end_h, end_m = divmod(total_min, 60)
    task.end_time = f"{end_h:02d}:{end_m:02d}"
    db.commit()
    return {"ok": True}


@router.delete("/batches/{batch_id}")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(PrintBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    db.delete(batch)
    db.commit()
    return {"ok": True}


# ---- 执行控制 ----

class StartBatchRequest(BaseModel):
    actual_time: str  # "HH:MM"


@router.post("/batches/{batch_id}/start")
def start_batch(batch_id: int, req: StartBatchRequest, db: Session = Depends(get_db)):
    """标记批次已开始，并根据实际开始时间调整后续批次"""
    from ..models import PrintConfig

    batch = db.get(PrintBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")

    plan = db.get(PrintPlan, batch.plan_id)
    if not plan:
        raise HTTPException(404, "排班表不存在")

    # 解析实际开始时间
    ah, am = map(int, req.actual_time.split(":"))
    actual_start = ah * 60 + am

    # 计算当前批次的时间偏差
    oh, om = map(int, batch.start_time.split(":"))
    original_start = oh * 60 + om
    delta = actual_start - original_start

    # 获取换版时间
    from ..models import SystemConfig, PrintConfig
    co_cfg = db.query(SystemConfig).filter(SystemConfig.key == "changeover_minutes").first()
    changeover = int(co_cfg.value) if co_cfg else 15

    def fmt(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    # 按打印机跟踪可用时间（和原始排班算法一致）
    printer_available: dict[int, int] = {}

    # 更新当前批次
    batch.status = "started"
    batch.start_time = req.actual_time
    for task in batch.tasks:
        cfg = db.get(PrintConfig, task.print_config_id)
        duration = cfg.duration_minutes if cfg else 0
        task.start_time = fmt(actual_start)
        task.end_time = fmt(actual_start + duration)
        printer_available[task.printer_id] = actual_start + duration

    # 调整后续所有 pending 批次
    later_batches = (
        db.query(PrintBatch)
        .filter(PrintBatch.plan_id == plan.id, PrintBatch.batch_order > batch.batch_order, PrintBatch.status == "pending")
        .order_by(PrintBatch.batch_order)
        .all()
    )

    for later in later_batches:
        # 该批次用到的打印机中，最晚空闲的时间 + 换版 = 批次开始时间
        batch_printer_ids = [t.printer_id for t in later.tasks]
        batch_start = max(printer_available.get(pid, 0) for pid in batch_printer_ids) + changeover

        later.start_time = fmt(batch_start)
        for task in later.tasks:
            cfg = db.get(PrintConfig, task.print_config_id)
            duration = cfg.duration_minutes if cfg else 0
            task.start_time = fmt(batch_start)
            task.end_time = fmt(batch_start + duration)
            printer_available[task.printer_id] = batch_start + duration

    db.commit()
    return {"ok": True, "delta_minutes": delta}


def _finish_task(task_id: int, new_status: str, db: Session):
    """通用的任务结束逻辑，completed 入库，cancelled/failed 不入库"""
    from ..models import PrintConfig, Inventory
    task = db.get(PrintTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status in ("completed", "cancelled", "failed"):
        raise HTTPException(400, f"任务已{task.status}")

    task.status = new_status

    added_component_id = None
    added_quantity = 0

    # 仅完成时入库
    if new_status == "completed":
        cfg = db.get(PrintConfig, task.print_config_id)
        if cfg:
            inv = db.query(Inventory).filter(
                Inventory.component_id == cfg.component_id,
                Inventory.color == task.color,
            ).first()
            if inv:
                inv.quantity += cfg.quantity
            added_component_id = cfg.component_id
            added_quantity = cfg.quantity

    # 如果批次内所有任务都已结束，自动标记批次为完成
    batch = db.get(PrintBatch, task.batch_id)
    if batch and all(t.status in ("completed", "cancelled", "failed") for t in batch.tasks):
        batch.status = "completed"

    db.commit()
    return {"ok": True, "status": new_status, "added_component_id": added_component_id, "added_quantity": added_quantity}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, db: Session = Depends(get_db)):
    """标记任务已完成，自动增加库存"""
    return _finish_task(task_id, "completed", db)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    """取消任务，不入库"""
    return _finish_task(task_id, "cancelled", db)


@router.post("/tasks/{task_id}/fail")
def fail_task(task_id: int, db: Session = Depends(get_db)):
    """标记任务失败，不入库"""
    return _finish_task(task_id, "failed", db)
