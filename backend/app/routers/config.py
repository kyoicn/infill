from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ScheduleConfig, SystemConfig
from ..schemas import (
    ScheduleConfigCreate, ScheduleConfigOut,
    SystemConfigOut, SystemConfigUpdate,
)

router = APIRouter(prefix="/api/config", tags=["配置"])


# ---- 操作时间窗口 ----

@router.get("/schedule", response_model=list[ScheduleConfigOut])
def list_schedule_configs(db: Session = Depends(get_db)):
    return db.query(ScheduleConfig).order_by(ScheduleConfig.day_of_week).all()


@router.put("/schedule/{day_of_week}", response_model=ScheduleConfigOut)
def upsert_schedule_config(day_of_week: int, data: ScheduleConfigCreate, db: Session = Depends(get_db)):
    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(400, "day_of_week 必须在 0~6 之间")
    cfg = db.query(ScheduleConfig).filter(ScheduleConfig.day_of_week == day_of_week).first()
    windows_data = [w.model_dump() for w in data.windows]
    if cfg:
        cfg.windows = windows_data
    else:
        cfg = ScheduleConfig(day_of_week=day_of_week, windows=windows_data)
        db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


# ---- 系统配置 ----

@router.get("/system", response_model=list[SystemConfigOut])
def list_system_configs(db: Session = Depends(get_db)):
    return db.query(SystemConfig).all()


@router.put("/system/{key}", response_model=SystemConfigOut)
def upsert_system_config(key: str, data: SystemConfigUpdate, db: Session = Depends(get_db)):
    cfg = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if cfg:
        cfg.value = data.value
    else:
        cfg = SystemConfig(key=key, value=data.value)
        db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg
