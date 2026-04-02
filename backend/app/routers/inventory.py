from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Inventory, Component, Order, OrderItem, ProductComponent
from ..schemas import InventoryOut, InventoryAdjust

router = APIRouter(prefix="/api/inventory", tags=["库存"])


@router.get("", response_model=list[InventoryOut])
def list_inventory(db: Session = Depends(get_db)):
    return db.query(Inventory).all()


@router.post("/adjust", response_model=InventoryOut)
def adjust_inventory(data: InventoryAdjust, db: Session = Depends(get_db)):
    """手动调整库存，quantity 为正数增加，负数减少"""
    inv = db.query(Inventory).filter(
        Inventory.component_id == data.component_id,
        Inventory.color == data.color,
    ).first()
    if not inv:
        raise HTTPException(404, "库存记录不存在")
    inv.quantity += data.quantity
    if inv.quantity < 0:
        inv.quantity = 0
    db.commit()
    db.refresh(inv)
    return inv


@router.put("/{inventory_id}", response_model=InventoryOut)
def set_inventory(inventory_id: int, data: InventoryAdjust, db: Session = Depends(get_db)):
    """直接设置库存数量"""
    inv = db.get(Inventory, inventory_id)
    if not inv:
        raise HTTPException(404, "库存记录不存在")
    inv.quantity = max(0, data.quantity)
    db.commit()
    db.refresh(inv)
    return inv


@router.get("/surplus")
def get_surplus_info(db: Session = Depends(get_db)):
    """计算当前库存相对于待处理订单的富余情况（按 component + color 维度）"""
    # 1. 计算待处理订单的总组件需求
    pending_orders = db.query(Order).filter(Order.status == "pending").all()
    component_demand: dict[tuple[int, str], int] = {}
    for order in pending_orders:
        for item in order.items:
            bom = db.query(ProductComponent).filter(ProductComponent.product_id == item.product_id).all()
            for bom_item in bom:
                key = (bom_item.component_id, bom_item.color)
                component_demand[key] = component_demand.get(key, 0) + bom_item.quantity * item.quantity

    # 2. 获取当前库存
    inventories = db.query(Inventory).all()
    inventory_map = {(inv.component_id, inv.color): inv.quantity for inv in inventories}

    # 3. 计算各组件+颜色的富余量
    # 收集所有需要展示的 key
    all_keys = set(component_demand.keys())
    for inv in inventories:
        all_keys.add((inv.component_id, inv.color))

    components = db.query(Component).all()
    comp_name_map = {c.id: c.name for c in components}

    result = []
    for comp_id, color in sorted(all_keys):
        stock = inventory_map.get((comp_id, color), 0)
        demand = component_demand.get((comp_id, color), 0)
        result.append({
            "component_id": comp_id,
            "component_name": comp_name_map.get(comp_id, "?"),
            "color": color,
            "stock": stock,
            "demand": demand,
            "surplus": stock - demand,
        })

    return result
