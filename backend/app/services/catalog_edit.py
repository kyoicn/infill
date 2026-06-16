"""目录 CRUD 服务层（v0.3.0：SKU-keyed）

每个 add / update / delete 函数走相同的 5 阶段事务：
1. 读 catalog.yaml → dict
2. 在 dict 上 mutate + 完整性校验（任何校验失败：不写盘，直接返回 error）
3. backup catalog.yaml
4. 写入新 dict
5. load_catalog(db)；失败 → rollback_from_backup

变化（vs v0.2.x）：
- update_* / delete_* 接受 `sku` 而非 `name`（路径稳定，name 可改）
- add_* 自动生成 SKU，返回时把 new_sku 放入 result
- 引用检查走 SKU：plate.组件编号 / product.BOM[].组件编号

复用 intake 的 backup_catalog / rollback_from_backup / yaml.safe_dump 参数。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from ..models import Order, OrderItem, Product, ProductComponent
from ..schemas_catalog_edit import (
    BOMItem,
    ComponentCreate,
    ComponentUpdate,
    PlateCreate,
    PlateUpdate,
    ProductCreate,
    ProductUpdate,
)
from . import catalog as _catalog_module
from .catalog import load_catalog, next_sku
from .intake import backup_catalog, rollback_from_backup


def _catalog_path() -> Path:
    """通过模块属性读，保证 monkeypatch(catalog_module, 'CATALOG_PATH', ...) 生效。"""
    return _catalog_module.CATALOG_PATH


# ---------- 内部工具 ----------

def _yaml_dump(data: dict) -> str:
    """与 intake.append_to_catalog 完全一致的 dump 参数。"""
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
        indent=2,
    )


def _load_yaml(catalog_path: Path) -> dict:
    """读 catalog.yaml → dict；空文件或不存在返回三段空列表。"""
    if catalog_path.exists():
        raw = catalog_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) if raw.strip() else {}
        if data is None:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("组件", [])
    data.setdefault("打印盘", [])
    data.setdefault("产品", [])
    return data


def _err(kind: str, msg: str) -> dict:
    return {"ok": False, "error_kind": kind, "error": msg}


def _commit_and_reload(
    db: Session,
    catalog_path: Path,
    new_data: dict,
    extra: dict | None = None,
) -> dict:
    """阶段 3-5：backup → write → reload；reload 失败回滚文件。

    `extra` 中的键（如 new_sku）会合并进成功响应。
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        backup_path = backup_catalog(catalog_path, timestamp)
    except OSError as exc:
        return _err("backup_failed", str(exc))

    try:
        catalog_path.write_text(_yaml_dump(new_data), encoding="utf-8")
    except (OSError, yaml.YAMLError) as exc:
        try:
            rollback_from_backup(catalog_path, backup_path)
            rolled_back = True
        except OSError:
            rolled_back = False
        return {
            **_err("write_failed", str(exc)),
            "rolled_back": rolled_back,
            "backup_path": str(backup_path),
        }

    try:
        stats = load_catalog(db)
    except Exception as exc:  # noqa: BLE001 — load_catalog 可能抛多种异常
        try:
            rollback_from_backup(catalog_path, backup_path)
            rolled_back = True
        except OSError:
            rolled_back = False
        if rolled_back:
            try:
                load_catalog(db)
            except Exception:
                pass
        return {
            **_err("load_failed", str(exc)),
            "rolled_back": rolled_back,
            "backup_path": str(backup_path),
        }

    result = {
        "ok": True,
        "stats": stats,
        "backup_path": str(backup_path),
    }
    if extra:
        result.update(extra)
    return result


def _find_index_by_sku(items: list[dict], sku: str) -> int:
    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get("编号") == sku:
            return i
    return -1


def _component_skus(data: dict) -> set[str]:
    return {
        item.get("编号")
        for item in data.get("组件", [])
        if isinstance(item, dict) and item.get("编号")
    }


def _bom_to_yaml(bom: list[BOMItem]) -> list[dict]:
    out: list[dict] = []
    for item in bom:
        entry: dict[str, Any] = {"组件编号": item.component_sku}
        if item.color:
            entry["颜色"] = item.color
        entry["数量"] = item.quantity
        out.append(entry)
    return out


# ---------- 组件 ----------

def add_component(db: Session, data: ComponentCreate) -> dict:
    if not data.name.strip():
        return _err("invalid_input", "组件名称不能为空")

    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)

    # 后端自动生成 SKU（用户不参与命名）
    new_sku = next_sku("组件", _component_skus(catalog))

    entry: dict[str, Any] = {"编号": new_sku, "名称": data.name}
    if data.description is not None and data.description != "":
        entry["描述"] = data.description
    if data.colors:
        entry["可选颜色"] = list(data.colors)
    catalog["组件"].append(entry)

    return _commit_and_reload(db, catalog_path, catalog, extra={"new_sku": new_sku})


def update_component(db: Session, sku: str, data: ComponentUpdate) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index_by_sku(catalog["组件"], sku)
    if idx < 0:
        return _err("not_found", f"组件编号 '{sku}' 不存在")

    entry = dict(catalog["组件"][idx])
    if data.name is not None:
        if not data.name.strip():
            return _err("invalid_input", "组件名称不能为空")
        entry["名称"] = data.name
    if data.description is not None:
        if data.description == "":
            entry.pop("描述", None)
        else:
            entry["描述"] = data.description
    if data.colors is not None:
        if data.colors:
            entry["可选颜色"] = list(data.colors)
        else:
            entry.pop("可选颜色", None)
    catalog["组件"][idx] = entry

    return _commit_and_reload(db, catalog_path, catalog)


def delete_component(db: Session, sku: str) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index_by_sku(catalog["组件"], sku)
    if idx < 0:
        return _err("not_found", f"组件编号 '{sku}' 不存在")

    entry = catalog["组件"][idx]
    comp_name = entry.get("名称", sku)

    # 引用检查：打印盘.组件编号
    for plate in catalog.get("打印盘", []):
        if isinstance(plate, dict) and plate.get("组件编号") == sku:
            return _err(
                "component_in_use",
                f"组件 '{comp_name}' ({sku}) 仍被打印盘 '{plate.get('盘号')}' 引用",
            )
    # 引用检查：产品.BOM[].组件编号
    for product in catalog.get("产品", []):
        if not isinstance(product, dict):
            continue
        for bom_item in product.get("BOM", []) or []:
            if isinstance(bom_item, dict) and bom_item.get("组件编号") == sku:
                return _err(
                    "component_in_use",
                    f"组件 '{comp_name}' ({sku}) 仍被产品 '{product.get('名称')}' 的 BOM 引用",
                )

    # 引用检查：DB 层 ProductComponent（多层防御）
    from ..models import Component as _C
    comp_row = db.query(_C).filter(_C.sku == sku).first()
    if comp_row is not None:
        pc_ref = db.query(ProductComponent).filter(ProductComponent.component_id == comp_row.id).first()
        if pc_ref is not None:
            return _err(
                "component_in_use",
                f"组件 '{comp_name}' ({sku}) 仍被产品 BOM 引用（DB 层）",
            )

    catalog["组件"].pop(idx)
    return _commit_and_reload(db, catalog_path, catalog)


# ---------- 打印盘 ----------

def add_plate(db: Session, data: PlateCreate) -> dict:
    if not data.plate_name.strip():
        return _err("invalid_input", "盘号不能为空")
    if data.quantity <= 0:
        return _err("invalid_input", "数量必须为正整数")
    if data.duration_minutes <= 0:
        return _err("invalid_input", "耗时分钟必须为正整数")

    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)

    if data.component_sku not in _component_skus(catalog):
        return _err(
            "component_not_found",
            f"组件编号 '{data.component_sku}' 不存在",
        )

    existing_plate_skus = {
        p.get("编号") for p in catalog["打印盘"] if isinstance(p, dict) and p.get("编号")
    }
    new_sku = next_sku("打印盘", existing_plate_skus)

    catalog["打印盘"].append({
        "编号": new_sku,
        "盘号": data.plate_name,
        "组件编号": data.component_sku,
        "数量": data.quantity,
        "耗时分钟": data.duration_minutes,
    })
    return _commit_and_reload(db, catalog_path, catalog, extra={"new_sku": new_sku})


def update_plate(db: Session, sku: str, data: PlateUpdate) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index_by_sku(catalog["打印盘"], sku)
    if idx < 0:
        return _err("not_found", f"打印盘编号 '{sku}' 不存在")

    if data.component_sku is not None and data.component_sku not in _component_skus(catalog):
        return _err(
            "component_not_found",
            f"组件编号 '{data.component_sku}' 不存在",
        )
    if data.quantity is not None and data.quantity <= 0:
        return _err("invalid_input", "数量必须为正整数")
    if data.duration_minutes is not None and data.duration_minutes <= 0:
        return _err("invalid_input", "耗时分钟必须为正整数")
    if data.plate_name is not None and not data.plate_name.strip():
        return _err("invalid_input", "盘号不能为空")

    entry = dict(catalog["打印盘"][idx])
    if data.plate_name is not None:
        entry["盘号"] = data.plate_name
    if data.component_sku is not None:
        entry["组件编号"] = data.component_sku
    if data.quantity is not None:
        entry["数量"] = data.quantity
    if data.duration_minutes is not None:
        entry["耗时分钟"] = data.duration_minutes
    catalog["打印盘"][idx] = entry

    return _commit_and_reload(db, catalog_path, catalog)


def delete_plate(db: Session, sku: str) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index_by_sku(catalog["打印盘"], sku)
    if idx < 0:
        return _err("not_found", f"打印盘编号 '{sku}' 不存在")

    catalog["打印盘"].pop(idx)
    return _commit_and_reload(db, catalog_path, catalog)


# ---------- 产品 ----------

def _validate_bom(catalog: dict, bom: list[BOMItem]) -> dict | None:
    if not bom:
        return _err("invalid_input", "BOM 不能为空")
    comp_skus = _component_skus(catalog)
    for item in bom:
        if not item.component_sku:
            return _err("invalid_input", "BOM 项的组件编号不能为空")
        if item.quantity <= 0:
            return _err("invalid_input", "BOM 项数量必须为正整数")
        if item.component_sku not in comp_skus:
            return _err(
                "component_not_found",
                f"BOM 引用的组件编号 '{item.component_sku}' 不存在",
            )
    return None


def add_product(db: Session, data: ProductCreate) -> dict:
    if not data.name.strip():
        return _err("invalid_input", "产品名称不能为空")

    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)

    err = _validate_bom(catalog, data.bom)
    if err:
        return err

    existing_prod_skus = {
        p.get("编号") for p in catalog["产品"] if isinstance(p, dict) and p.get("编号")
    }
    new_sku = next_sku("产品", existing_prod_skus)

    entry: dict[str, Any] = {"编号": new_sku, "名称": data.name}
    if data.description is not None and data.description != "":
        entry["描述"] = data.description
    entry["BOM"] = _bom_to_yaml(data.bom)
    catalog["产品"].append(entry)

    return _commit_and_reload(db, catalog_path, catalog, extra={"new_sku": new_sku})


def update_product(db: Session, sku: str, data: ProductUpdate) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index_by_sku(catalog["产品"], sku)
    if idx < 0:
        return _err("not_found", f"产品编号 '{sku}' 不存在")

    if data.bom is not None:
        err = _validate_bom(catalog, data.bom)
        if err:
            return err

    entry = dict(catalog["产品"][idx])
    if data.name is not None:
        if not data.name.strip():
            return _err("invalid_input", "产品名称不能为空")
        entry["名称"] = data.name
    if data.description is not None:
        if data.description == "":
            entry.pop("描述", None)
        else:
            entry["描述"] = data.description
    if data.bom is not None:
        entry["BOM"] = _bom_to_yaml(data.bom)
    catalog["产品"][idx] = entry

    return _commit_and_reload(db, catalog_path, catalog)


def delete_product(db: Session, sku: str) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index_by_sku(catalog["产品"], sku)
    if idx < 0:
        return _err("not_found", f"产品编号 '{sku}' 不存在")

    # 引用检查：DB 中 pending 订单的 OrderItem
    product_row = db.query(Product).filter(Product.sku == sku).first()
    if product_row is not None:
        ref = (
            db.query(OrderItem)
            .join(Order, OrderItem.order_id == Order.id)
            .filter(OrderItem.product_id == product_row.id)
            .filter(Order.status == "pending")
            .first()
        )
        if ref is not None:
            return _err(
                "product_in_use",
                f"产品 '{product_row.name}' ({sku}) 仍被未发货订单引用",
            )

    catalog["产品"].pop(idx)
    return _commit_and_reload(db, catalog_path, catalog)


# ---------- v0.3.0：手动触发 SKU backfill ----------

def migrate_to_sku(db: Session) -> dict:
    """手动触发一次 backfill + reload。

    - 用户手编 yaml 留下不一致（如新行没编号）时可用此端点清理
    - 内部：backfill_skus_if_needed → load_catalog
    - 幂等：已是 SKU-keyed 格式 → backfilled=False，照常 reload
    """
    from .catalog import backfill_skus_if_needed
    catalog_path = _catalog_path()
    try:
        backfilled = backfill_skus_if_needed(catalog_path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        return _err("backfill_failed", str(exc))

    try:
        stats = load_catalog(db)
    except Exception as exc:  # noqa: BLE001
        return _err("load_failed", str(exc))

    return {
        "ok": True,
        "backfilled": backfilled,
        "stats": stats,
    }
