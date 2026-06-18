"""Auto-import services — ADB capture, LLM-backed SKU matching, 闲鱼 parser.

Section banners are intentional — Tasks 2.1 / 2.2 / 2.3 of prd-006 land here
from parallel worktrees, each owning a banner block to keep merges trivial.
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Product, ProductComponent


# ==== ADB & Config (Task 2.1) ====


# ==== LLM SKU Match + SKU Search (Task 2.2) ====


def search_skus(db: Session, q: str, limit: int = 10) -> list[dict]:
    """SKU 搜索：name 或 sku 子串命中（不区分大小写），返回前 limit 条。

    每条 hit 形如 `{"sku": str, "name": str, "color": Optional[str]}`，
    color 取该 product 的 product_components.color 去重串接，无 BOM 时省略。
    空 query 返回空列表。
    """
    q = (q or "").strip()
    if not q:
        return []

    like = f"%{q}%"
    rows = (
        db.query(Product)
        .filter(or_(Product.name.ilike(like), Product.sku.ilike(like)))
        .order_by(Product.sku.asc())
        .limit(limit)
        .all()
    )

    hits: list[dict] = []
    for product in rows:
        colors = (
            db.query(ProductComponent.color)
            .filter(ProductComponent.product_id == product.id)
            .all()
        )
        unique_colors = sorted({c[0] for c in colors if c[0]})
        hit: dict = {"sku": product.sku, "name": product.name}
        if unique_colors:
            hit["color"] = ",".join(unique_colors)
        else:
            hit["color"] = None
        hits.append(hit)
    return hits


# ==== Order Persistence (Task 2.3) ====
