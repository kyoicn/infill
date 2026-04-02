"""
从 catalog.yaml 加载产品目录并同步到数据库。
YAML 是唯一数据源，数据库只是运行时的镜像。
"""

import os
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..models import Component, PrintConfig, Product, ProductComponent, Inventory

_default_path = Path(__file__).resolve().parent.parent.parent.parent / "data/catalog.yaml"
CATALOG_PATH = Path(os.environ.get("CATALOG_PATH", str(_default_path)))


def load_catalog(db: Session) -> dict:
    """读取 YAML 并同步到数据库，返回加载统计"""
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    stats = {"组件": 0, "打印盘": 0, "产品": 0}

    # ---- 1. 同步组件 ----
    yaml_comp_names = set()
    for item in data.get("组件", []):
        name = item["名称"]
        yaml_comp_names.add(name)
        colors = item.get("可选颜色", [])
        comp = db.query(Component).filter(Component.name == name).first()
        if comp:
            comp.description = item.get("描述", "")
            comp.colors = colors
        else:
            comp = Component(name=name, description=item.get("描述", ""), colors=colors)
            db.add(comp)
            db.flush()

        # 为每种颜色创建库存记录（如果不存在）
        color_list = colors if colors else [""]  # 无颜色的组件用空字符串
        for color in color_list:
            existing_inv = db.query(Inventory).filter(
                Inventory.component_id == comp.id,
                Inventory.color == color,
            ).first()
            if not existing_inv:
                db.add(Inventory(component_id=comp.id, color=color, quantity=0))

        # 删除 YAML 中已移除的颜色对应的库存（仅删除数量为 0 的）
        for inv in db.query(Inventory).filter(Inventory.component_id == comp.id).all():
            if inv.color not in color_list:
                if inv.quantity == 0:
                    db.delete(inv)

        stats["组件"] += 1

    # 删除 YAML 中不存在的组件
    for comp in db.query(Component).all():
        if comp.name not in yaml_comp_names:
            db.delete(comp)

    db.flush()

    # 建立名称→ID 映射
    comp_map = {c.name: c.id for c in db.query(Component).all()}

    # ---- 2. 同步打印盘 ----
    yaml_plate_names = set()
    for item in data.get("打印盘", []):
        plate_name = item["盘号"]
        yaml_plate_names.add(plate_name)
        comp_name = item["组件"]
        if comp_name not in comp_map:
            raise ValueError(f"打印盘 '{plate_name}' 引用了不存在的组件 '{comp_name}'")

        cfg = db.query(PrintConfig).filter(PrintConfig.plate_name == plate_name).first()
        if cfg:
            cfg.component_id = comp_map[comp_name]
            cfg.quantity = item["数量"]
            cfg.duration_minutes = item["耗时分钟"]
        else:
            cfg = PrintConfig(
                plate_name=plate_name,
                component_id=comp_map[comp_name],
                quantity=item["数量"],
                duration_minutes=item["耗时分钟"],
            )
            db.add(cfg)
        stats["打印盘"] += 1

    # 删除 YAML 中不存在的打印盘
    for cfg in db.query(PrintConfig).all():
        if cfg.plate_name not in yaml_plate_names:
            db.delete(cfg)

    db.flush()

    # ---- 3. 同步产品 ----
    yaml_prod_names = set()
    for item in data.get("产品", []):
        name = item["名称"]
        yaml_prod_names.add(name)
        product = db.query(Product).filter(Product.name == name).first()
        if product:
            product.description = item.get("描述", "")
            # 重建 BOM
            db.query(ProductComponent).filter(ProductComponent.product_id == product.id).delete()
        else:
            product = Product(name=name, description=item.get("描述", ""))
            db.add(product)
            db.flush()

        for bom_item in item.get("BOM", []):
            comp_name = bom_item["组件"]
            if comp_name not in comp_map:
                raise ValueError(f"产品 '{name}' 的 BOM 引用了不存在的组件 '{comp_name}'")
            db.add(ProductComponent(
                product_id=product.id,
                component_id=comp_map[comp_name],
                color=bom_item.get("颜色", ""),
                quantity=bom_item["数量"],
            ))
        stats["产品"] += 1

    # 删除 YAML 中不存在的产品
    for product in db.query(Product).all():
        if product.name not in yaml_prod_names:
            db.delete(product)

    db.commit()
    return stats
