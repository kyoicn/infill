from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, OrderItem, Inventory, Product, ProductComponent
from ..schemas import OrderCreate, OrderOut

router = APIRouter(prefix="/api/orders", tags=["订单"])


@router.get("", response_model=list[OrderOut])
def list_orders(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Order)
    if status:
        q = q.filter(Order.status == status)
    return q.order_by(Order.created_at).all()


@router.post("", response_model=OrderOut)
def create_order(data: OrderCreate, db: Session = Depends(get_db)):
    order = Order()
    db.add(order)
    db.flush()
    for item in data.items:
        db.add(OrderItem(order_id=order.id, product_id=item.product_id, quantity=item.quantity))
    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    return order


@router.post("/{order_id}/ship")
def ship_order(order_id: int, db: Session = Depends(get_db)):
    """标记订单为已发货，自动扣减库存"""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status == "shipped":
        raise HTTPException(400, "订单已发货")

    # 计算需要扣减的组件
    for item in order.items:
        bom = db.query(ProductComponent).filter(ProductComponent.product_id == item.product_id).all()
        for bom_item in bom:
            inv = db.query(Inventory).filter(Inventory.component_id == bom_item.component_id).first()
            if not inv:
                raise HTTPException(400, f"组件 {bom_item.component_id} 无库存记录")
            needed = bom_item.quantity * item.quantity
            if inv.quantity < needed:
                raise HTTPException(400, f"组件 {bom_item.component_id} 库存不足（需要 {needed}，当前 {inv.quantity}）")
            inv.quantity -= needed

    order.status = "shipped"
    order.shipped_at = datetime.now()
    db.commit()
    return {"ok": True}


@router.delete("/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    db.delete(order)
    db.commit()
    return {"ok": True}
