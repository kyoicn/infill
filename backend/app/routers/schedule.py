from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PrintPlan, PrintBatch, PrintTask
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
    """生成指定日期的排班表（每个日期只能有一个）"""
    existing = db.query(PrintPlan).filter(PrintPlan.date == req.date).first()
    if existing:
        raise HTTPException(400, f"{req.date} 已有排班，请先删除后再重新生成")
    try:
        plan = generate_plan(db, req.date, req.surplus_enabled)
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
