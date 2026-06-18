"""
从 catalog.yaml 加载产品目录并同步到数据库（v0.3.0：SKU-keyed）。

设计要点：
- YAML 是唯一数据源，数据库只是运行时的镜像
- 三类实体（组件 / 打印盘 / 产品）有永久不变的 SKU 作为稳定标识：
  组件 C-NNNN / 打印盘 P-NNNN / 产品 PR-NNNN（NNNN 4 位 zero-padded）
- name / plate_name 是可改的展示字段
- 引用走 SKU：打印盘.组件编号 → 组件SKU；产品.BOM[].组件编号 → 组件SKU
- 旧格式 YAML（没有"编号"）会在 load 前被 backfill_skus_if_needed 自动补齐
"""

import os
import re
from pathlib import Path
from typing import Any, Iterable

import yaml
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..models import Component, PrintConfig, Product, ProductComponent, Inventory

_default_path = Path(__file__).resolve().parent.parent.parent.parent / "data/catalog.yaml"
CATALOG_PATH = Path(os.environ.get("CATALOG_PATH", str(_default_path)))


# ---------- SKU 工具 ----------

SKU_PREFIXES = {
    "组件": "C",
    "打印盘": "P",
    "产品": "PR",
}

_SKU_RE = re.compile(r"^([A-Z]+)-(\d{4,})$")


def _parse_sku_number(sku: str | None) -> int:
    """从 SKU 字串提取数字部分（不匹配返回 0）。"""
    if not isinstance(sku, str):
        return 0
    m = _SKU_RE.match(sku)
    if not m:
        return 0
    try:
        return int(m.group(2))
    except ValueError:
        return 0


def _format_sku(prefix: str, n: int) -> str:
    """格式化 SKU：4 位 zero-padded，超过 9999 自动用 5 位（向后兼容字典序排序）。"""
    width = max(4, len(str(n)))
    return f"{prefix}-{n:0{width}d}"


def next_sku(section: str, existing_skus: Iterable[str]) -> str:
    """对给定段（组件 / 打印盘 / 产品）算出下一个未使用的 SKU。

    策略：max(已用数字) + 1；从 0001 起步。
    existing_skus 可来自 YAML 或 DB —— 调用方负责合并两边来源。
    """
    if section not in SKU_PREFIXES:
        raise ValueError(f"未知段: {section}")
    prefix = SKU_PREFIXES[section]
    max_n = 0
    for sku in existing_skus:
        if not isinstance(sku, str):
            continue
        m = _SKU_RE.match(sku)
        if m and m.group(1) == prefix:
            try:
                max_n = max(max_n, int(m.group(2)))
            except ValueError:
                continue
    return _format_sku(prefix, max_n + 1)


# ---------- 启动迁移：保证 DB 有 sku 列 ----------

_SKU_COLUMN_TABLES = ("components", "print_configs", "products")


def ensure_sku_column_exists(engine: Engine) -> list[str]:
    """检查 components / print_configs / products 三表是否有 sku 列，没有就 ALTER TABLE 加列。

    SQLite-safe：ADD COLUMN 是允许的，但不能直接加 NOT NULL（无默认值）。
    所以这里加成 nullable VARCHAR(16)，由后续 load_catalog 写入实际 SKU。
    幂等：列已存在直接跳过。
    返回实际 ALTER 过的表名列表（用于日志/测试）。
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    altered: list[str] = []
    for table_name in _SKU_COLUMN_TABLES:
        if table_name not in existing_tables:
            # 整张表不存在 → create_all 会建（带 sku 列）
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
        if "sku" in existing_cols:
            continue
        sql = f"ALTER TABLE {table_name} ADD COLUMN sku VARCHAR(16)"
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
        altered.append(table_name)
    return altered


def ensure_order_notes_column_exists(engine: Engine) -> bool:
    """v0.3.1：确保 orders 表有 notes 列。

    幂等：列已存在 / 表不存在 → 返回 False（不 ALTER）。
    实际 ALTER 过返回 True。
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    if "orders" not in existing_tables:
        # 整张表不存在 → create_all 会建（带 notes 列）
        return False
    existing_cols = {col["name"] for col in inspector.get_columns("orders")}
    if "notes" in existing_cols:
        return False
    sql = "ALTER TABLE orders ADD COLUMN notes TEXT DEFAULT ''"
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    return True


# v0.4 auto-import: orders 表新增 4 列 + (platform, external_order_id) 部分唯一索引
_ORDER_AUTO_IMPORT_COLUMNS: dict[str, str] = {
    "platform": "VARCHAR(16)",
    "external_order_id": "VARCHAR(64)",
    "buyer_nickname": "VARCHAR(128)",
    "external_created_at": "DATETIME",
}
_ORDER_AUTO_IMPORT_INDEX = "uq_orders_platform_external"


def ensure_order_auto_import_schema_exists(engine: Engine) -> bool:
    """v0.4：确保 orders 表有 auto-import 所需的 4 列 + partial unique index。

    - 缺列：ALTER TABLE 加列（nullable, default NULL）
    - 缺索引：CREATE UNIQUE INDEX uq_orders_platform_external on (platform, external_order_id)
      WHERE 两者都非 NULL —— 手动单两字段都 NULL 不参与唯一约束
    幂等：所有列 + 索引都已存在 → 返回 False；任何一项被创建 → 返回 True。
    表不存在 → 返回 False（create_all 会建表 + 此函数下次调用时建索引）。
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    if "orders" not in existing_tables:
        return False

    changed = False
    existing_cols = {col["name"] for col in inspector.get_columns("orders")}
    for col_name, col_type in _ORDER_AUTO_IMPORT_COLUMNS.items():
        if col_name in existing_cols:
            continue
        sql = f"ALTER TABLE orders ADD COLUMN {col_name} {col_type} DEFAULT NULL"
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
        changed = True

    # 检查 partial unique index 是否存在
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("orders")}
    if _ORDER_AUTO_IMPORT_INDEX not in existing_indexes:
        sql = (
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_ORDER_AUTO_IMPORT_INDEX} "
            f"ON orders(platform, external_order_id) "
            f"WHERE platform IS NOT NULL AND external_order_id IS NOT NULL"
        )
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
        changed = True

    return changed


# ---------- YAML SKU backfill ----------

def _has_skus(data: dict) -> bool:
    """判断 YAML 是否已经是 SKU-keyed 格式（每段每条都有"编号"）。"""
    for section in ("组件", "打印盘", "产品"):
        items = data.get(section) or []
        for item in items:
            if not isinstance(item, dict):
                continue
            if not item.get("编号"):
                return False
    return True


def backfill_skus_if_needed(catalog_path: Path) -> bool:
    """检查 catalog.yaml，若任何条目缺"编号"则按段顺序补齐并重写文件。

    幂等：所有条目都已有编号 → 不动文件，返回 False。
    否则：
    - 按段顺序为缺编号条目分配 SKU（从已有最大数字 +1 起步）
    - 把打印盘的"组件: <name>" 改为 "组件编号: C-NNNN"
    - 把产品的 BOM[].组件 同样改为 BOM[].组件编号
    - 中文键保留、原顺序保留
    - 完成后返回 True
    """
    if not catalog_path.exists():
        return False
    raw = catalog_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) if raw.strip() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("组件", [])
    data.setdefault("打印盘", [])
    data.setdefault("产品", [])

    if _has_skus(data):
        return False

    # ---- 阶段 1：为组件分配 SKU，建立 name→sku 映射 ----
    existing_comp_skus = [
        item.get("编号") for item in data["组件"]
        if isinstance(item, dict) and item.get("编号")
    ]
    name_to_sku: dict[str, str] = {}
    for item in data["组件"]:
        if not isinstance(item, dict):
            continue
        if item.get("编号"):
            if item.get("名称"):
                name_to_sku[item["名称"]] = item["编号"]
            continue
        sku = next_sku("组件", existing_comp_skus)
        existing_comp_skus.append(sku)
        # 把"编号"插到字典最前（用重建保证 key 顺序）
        new_entry: dict[str, Any] = {"编号": sku}
        for k, v in item.items():
            new_entry[k] = v
        item.clear()
        item.update(new_entry)
        if item.get("名称"):
            name_to_sku[item["名称"]] = sku

    # ---- 阶段 2：为打印盘分配 SKU，并把"组件: <name>"改为"组件编号: <sku>" ----
    existing_plate_skus = [
        item.get("编号") for item in data["打印盘"]
        if isinstance(item, dict) and item.get("编号")
    ]
    for item in data["打印盘"]:
        if not isinstance(item, dict):
            continue
        if not item.get("编号"):
            sku = next_sku("打印盘", existing_plate_skus)
            existing_plate_skus.append(sku)
            new_entry = {"编号": sku}
            for k, v in item.items():
                new_entry[k] = v
            item.clear()
            item.update(new_entry)
        # 引用迁移：组件 → 组件编号
        if "组件编号" not in item and "组件" in item:
            comp_name = item.pop("组件")
            if comp_name in name_to_sku:
                # 在合理位置插入"组件编号"（编号之后、数量之前）
                rebuilt: dict[str, Any] = {}
                inserted = False
                for k, v in item.items():
                    rebuilt[k] = v
                    if k == "编号" and not inserted:
                        rebuilt["组件编号"] = name_to_sku[comp_name]
                        inserted = True
                if not inserted:
                    rebuilt["组件编号"] = name_to_sku[comp_name]
                item.clear()
                item.update(rebuilt)
            else:
                raise ValueError(
                    f"打印盘 '{item.get('盘号')}' 引用了不存在的组件名 '{comp_name}'，"
                    f"无法 backfill 组件编号"
                )

    # ---- 阶段 3：为产品分配 SKU，并把 BOM[].组件 → BOM[].组件编号 ----
    existing_prod_skus = [
        item.get("编号") for item in data["产品"]
        if isinstance(item, dict) and item.get("编号")
    ]
    for item in data["产品"]:
        if not isinstance(item, dict):
            continue
        if not item.get("编号"):
            sku = next_sku("产品", existing_prod_skus)
            existing_prod_skus.append(sku)
            new_entry = {"编号": sku}
            for k, v in item.items():
                new_entry[k] = v
            item.clear()
            item.update(new_entry)
        bom = item.get("BOM") or []
        for bom_item in bom:
            if not isinstance(bom_item, dict):
                continue
            if "组件编号" not in bom_item and "组件" in bom_item:
                comp_name = bom_item.pop("组件")
                if comp_name in name_to_sku:
                    rebuilt = {"组件编号": name_to_sku[comp_name]}
                    for k, v in bom_item.items():
                        rebuilt[k] = v
                    bom_item.clear()
                    bom_item.update(rebuilt)
                else:
                    raise ValueError(
                        f"产品 '{item.get('名称')}' 的 BOM 引用了不存在的组件名 '{comp_name}'，"
                        f"无法 backfill 组件编号"
                    )

    # ---- 写回 YAML（保留中文键 + 原序）----
    dumped = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
        indent=2,
    )
    catalog_path.write_text(dumped, encoding="utf-8")
    return True


# ---------- 主入口：load_catalog ----------

def load_catalog(db: Session) -> dict:
    """读取 YAML 并同步到数据库，返回加载统计。

    v0.3.0：按 SKU keyed 同步；如 YAML 旧格式（无"编号"）会先自动 backfill。
    """
    # 0. 旧格式自动 backfill（幂等）
    backfill_skus_if_needed(CATALOG_PATH)

    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    stats = {"组件": 0, "打印盘": 0, "产品": 0}

    # ---- 1. 同步组件（按 SKU keyed）----
    yaml_comp_skus: set[str] = set()
    sku_to_comp_id: dict[str, int] = {}
    for item in data.get("组件", []) or []:
        sku = item["编号"]
        yaml_comp_skus.add(sku)
        name = item["名称"]
        colors = item.get("可选颜色", []) or []
        comp = db.query(Component).filter(Component.sku == sku).first()
        if comp is None:
            # v0.3.0 defensive: 老 DB 行 sku=NULL，按 name fallback 找回原 id，避免外键悬空
            comp = db.query(Component).filter(
                Component.sku.is_(None),
                Component.name == name,
            ).first()
            if comp is not None:
                comp.sku = sku
        if comp:
            # name 现在可改：UPDATE 时覆盖
            comp.name = name
            comp.description = item.get("描述", "")
            comp.colors = colors
        else:
            comp = Component(sku=sku, name=name, description=item.get("描述", ""), colors=colors)
            db.add(comp)
            db.flush()
        sku_to_comp_id[sku] = comp.id

        color_list = colors if colors else [""]
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

    # 删除 YAML 中不存在的组件（按 SKU）
    for comp in db.query(Component).all():
        if comp.sku not in yaml_comp_skus:
            db.delete(comp)

    db.flush()

    # ---- 2. 同步打印盘（按 SKU keyed）----
    yaml_plate_skus: set[str] = set()
    for item in data.get("打印盘", []) or []:
        sku = item["编号"]
        yaml_plate_skus.add(sku)
        plate_name = item["盘号"]
        comp_sku = item["组件编号"]
        if comp_sku not in sku_to_comp_id:
            raise ValueError(
                f"打印盘 '{plate_name}' 引用了不存在的组件编号 '{comp_sku}'"
            )

        cfg = db.query(PrintConfig).filter(PrintConfig.sku == sku).first()
        if cfg is None:
            # v0.3.0 defensive: 老 DB 行 sku=NULL，按 plate_name fallback 找回原 id
            cfg = db.query(PrintConfig).filter(
                PrintConfig.sku.is_(None),
                PrintConfig.plate_name == plate_name,
            ).first()
            if cfg is not None:
                cfg.sku = sku
        if cfg:
            cfg.plate_name = plate_name
            cfg.component_id = sku_to_comp_id[comp_sku]
            cfg.quantity = item["数量"]
            cfg.duration_minutes = item["耗时分钟"]
        else:
            cfg = PrintConfig(
                sku=sku,
                plate_name=plate_name,
                component_id=sku_to_comp_id[comp_sku],
                quantity=item["数量"],
                duration_minutes=item["耗时分钟"],
            )
            db.add(cfg)
        stats["打印盘"] += 1

    for cfg in db.query(PrintConfig).all():
        if cfg.sku not in yaml_plate_skus:
            db.delete(cfg)

    db.flush()

    # ---- 3. 同步产品（按 SKU keyed）----
    yaml_prod_skus: set[str] = set()
    for item in data.get("产品", []) or []:
        sku = item["编号"]
        yaml_prod_skus.add(sku)
        name = item["名称"]
        product = db.query(Product).filter(Product.sku == sku).first()
        if product is None:
            # v0.3.0 defensive: 老 DB 行 sku=NULL，按 name fallback 找回原 id
            product = db.query(Product).filter(
                Product.sku.is_(None),
                Product.name == name,
            ).first()
            if product is not None:
                product.sku = sku
        if product:
            product.name = name
            product.description = item.get("描述", "")
            db.query(ProductComponent).filter(ProductComponent.product_id == product.id).delete()
        else:
            product = Product(sku=sku, name=name, description=item.get("描述", ""))
            db.add(product)
            db.flush()

        for bom_item in item.get("BOM", []) or []:
            comp_sku = bom_item["组件编号"]
            if comp_sku not in sku_to_comp_id:
                raise ValueError(
                    f"产品 '{name}' 的 BOM 引用了不存在的组件编号 '{comp_sku}'"
                )
            db.add(ProductComponent(
                product_id=product.id,
                component_id=sku_to_comp_id[comp_sku],
                color=bom_item.get("颜色", "") or "",
                quantity=bom_item["数量"],
            ))
        stats["产品"] += 1

    for product in db.query(Product).all():
        if product.sku not in yaml_prod_skus:
            db.delete(product)

    db.commit()
    return stats
