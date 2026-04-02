from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db, Base, engine, SessionLocal
from ..models import (
    ScheduleConfig, SystemConfig,
    Inventory, Order, OrderItem, Component,
    PrintPlan, PrintBatch, PrintTask, Printer,
)
from ..schemas import (
    ScheduleConfigCreate, ScheduleConfigOut,
    SystemConfigOut, SystemConfigUpdate,
)
from ..services.catalog import load_catalog

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


# ---- 重置数据库 ----

@router.post("/reset-db")
def reset_database(db: Session = Depends(get_db)):
    """
    重置数据库：保留库存、订单、打印机和系统配置，删除排班等非核心数据后重建表结构。
    目录数据从 YAML 重新加载。
    """
    # 1. 备份核心数据
    inventory_backup = [
        {"component_name": db.query(Component).get(inv.component_id).name, "color": inv.color, "quantity": inv.quantity}
        for inv in db.query(Inventory).all()
        if db.query(Component).get(inv.component_id)
    ]
    orders_backup = []
    for order in db.query(Order).all():
        items = []
        for item in order.items:
            from ..models import Product
            product = db.query(Product).get(item.product_id)
            if product:
                items.append({"product_name": product.name, "quantity": item.quantity})
        orders_backup.append({
            "status": order.status,
            "created_at": order.created_at.isoformat(),
            "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
            "items": items,
        })
    printers_backup = [{"name": p.name} for p in db.query(Printer).all()]
    schedule_configs_backup = [
        {"day_of_week": sc.day_of_week, "windows": sc.windows}
        for sc in db.query(ScheduleConfig).all()
    ]
    system_configs_backup = [
        {"key": sc.key, "value": sc.value}
        for sc in db.query(SystemConfig).all()
    ]

    db.close()

    # 2. 删除所有表并重建
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # 3. 用新 session 恢复数据
    new_db = SessionLocal()
    try:
        # 3a. 从 YAML 恢复目录（组件、打印盘、产品）
        load_catalog(new_db)

        # 3b. 恢复库存
        comp_map = {c.name: c.id for c in new_db.query(Component).all()}
        for item in inventory_backup:
            if item["component_name"] in comp_map:
                inv = new_db.query(Inventory).filter(
                    Inventory.component_id == comp_map[item["component_name"]],
                    Inventory.color == item.get("color", ""),
                ).first()
                if inv:
                    inv.quantity = item["quantity"]
        new_db.flush()

        # 3c. 恢复订单
        from ..models import Product
        prod_map = {p.name: p.id for p in new_db.query(Product).all()}
        from datetime import datetime
        for o_data in orders_backup:
            valid_items = [i for i in o_data["items"] if i["product_name"] in prod_map]
            if not valid_items:
                continue
            order = Order(
                status=o_data["status"],
                created_at=datetime.fromisoformat(o_data["created_at"]),
                shipped_at=datetime.fromisoformat(o_data["shipped_at"]) if o_data["shipped_at"] else None,
            )
            new_db.add(order)
            new_db.flush()
            for i_data in valid_items:
                new_db.add(OrderItem(
                    order_id=order.id,
                    product_id=prod_map[i_data["product_name"]],
                    quantity=i_data["quantity"],
                ))

        # 3d. 恢复打印机
        for p_data in printers_backup:
            new_db.add(Printer(name=p_data["name"]))

        # 3e. 恢复配置
        for sc_data in schedule_configs_backup:
            new_db.add(ScheduleConfig(**sc_data))
        for sc_data in system_configs_backup:
            new_db.add(SystemConfig(**sc_data))

        new_db.commit()
    finally:
        new_db.close()

    return {
        "ok": True,
        "restored": {
            "inventory": len(inventory_backup),
            "orders": len(orders_backup),
            "printers": len(printers_backup),
        },
    }
