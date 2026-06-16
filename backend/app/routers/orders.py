from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, OrderItem, Inventory, Product, ProductComponent
from ..schemas import OrderCreate, OrderOut, OrderUpdate

router = APIRouter(prefix="/api/orders", tags=["订单"])


@router.get("", response_model=list[OrderOut])
def list_orders(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Order)
    if status:
        q = q.filter(Order.status == status)
    return q.order_by(Order.created_at).all()


@router.post("", response_model=OrderOut)
def create_order(data: OrderCreate, db: Session = Depends(get_db)):
    order = Order(notes=data.notes or "")
    db.add(order)
    db.flush()
    for item in data.items:
        db.add(OrderItem(order_id=order.id, product_id=item.product_id, quantity=item.quantity))
    db.commit()
    db.refresh(order)
    return order


@router.put("/{order_id}", response_model=OrderOut)
def update_order(order_id: int, body: OrderUpdate, db: Session = Depends(get_db)):
    """编辑订单：可改 items / notes。

    - 不接受 created_at / shipped_at / status — 这些不变
    - 允许 status=shipped 时编辑（用于修复孤儿 FK 引用）
    """
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")

    if body.notes is not None:
        order.notes = body.notes

    if body.items is not None:
        # 校验所有 product_id 都存在
        for it in body.items:
            if not db.get(Product, it.product_id):
                raise HTTPException(400, f"产品 {it.product_id} 不存在")
        # 删旧 OrderItem，添新
        db.query(OrderItem).filter(OrderItem.order_id == order_id).delete()
        for it in body.items:
            db.add(OrderItem(order_id=order_id, product_id=it.product_id, quantity=it.quantity))

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

    # 计算需要扣减的组件（按 component + color）
    for item in order.items:
        bom = db.query(ProductComponent).filter(ProductComponent.product_id == item.product_id).all()
        for bom_item in bom:
            inv = db.query(Inventory).filter(
                Inventory.component_id == bom_item.component_id,
                Inventory.color == bom_item.color,
            ).first()
            if not inv:
                raise HTTPException(400, f"组件 {bom_item.component_id}（{bom_item.color or '无颜色'}）无库存记录")
            needed = bom_item.quantity * item.quantity
            if inv.quantity < needed:
                raise HTTPException(400, f"组件 {bom_item.component_id}（{bom_item.color or '无颜色'}）库存不足（需要 {needed}，当前 {inv.quantity}）")
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
