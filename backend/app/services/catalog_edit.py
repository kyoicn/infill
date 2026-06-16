"""目录 CRUD 服务层

每个 add / update / delete 函数走相同的 5 阶段事务：
1. 读 catalog.yaml → dict
2. 在 dict 上 mutate + 完整性校验（任何校验失败：不写盘，直接返回 error）
3. backup catalog.yaml
4. 写入新 dict
5. load_catalog(db)；失败 → rollback_from_backup

复用 intake 的 backup_catalog / rollback_from_backup / yaml.safe_dump 参数。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from ..models import Order, OrderItem, Product
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
from .catalog import load_catalog
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
) -> dict:
    """阶段 3-5：backup → write → reload；reload 失败回滚文件。"""
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
        # rollback 后让 DB 也尝试回到一致状态（best-effort）
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

    return {
        "ok": True,
        "stats": stats,
        "backup_path": str(backup_path),
    }


def _find_index(items: list[dict], key: str, value: str) -> int:
    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get(key) == value:
            return i
    return -1


def _component_names(data: dict) -> set[str]:
    return {
        item.get("名称")
        for item in data.get("组件", [])
        if isinstance(item, dict) and item.get("名称")
    }


def _bom_to_yaml(bom: list[BOMItem]) -> list[dict]:
    out: list[dict] = []
    for item in bom:
        entry: dict[str, Any] = {"组件": item.component_name}
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

    if data.name in _component_names(catalog):
        return _err("name_conflict", f"组件 '{data.name}' 已存在")

    entry: dict[str, Any] = {"名称": data.name}
    if data.description is not None and data.description != "":
        entry["描述"] = data.description
    if data.colors:
        entry["可选颜色"] = list(data.colors)
    catalog["组件"].append(entry)

    return _commit_and_reload(db, catalog_path, catalog)


def update_component(db: Session, name: str, data: ComponentUpdate) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index(catalog["组件"], "名称", name)
    if idx < 0:
        return _err("not_found", f"组件 '{name}' 不存在")

    entry = dict(catalog["组件"][idx])  # 拷贝以保持稳定
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


def delete_component(db: Session, name: str) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index(catalog["组件"], "名称", name)
    if idx < 0:
        return _err("not_found", f"组件 '{name}' 不存在")

    # 引用检查：打印盘
    for plate in catalog.get("打印盘", []):
        if isinstance(plate, dict) and plate.get("组件") == name:
            return _err(
                "component_in_use",
                f"组件 '{name}' 仍被打印盘 '{plate.get('盘号')}' 引用",
            )
    # 引用检查：产品 BOM
    for product in catalog.get("产品", []):
        if not isinstance(product, dict):
            continue
        for bom_item in product.get("BOM", []) or []:
            if isinstance(bom_item, dict) and bom_item.get("组件") == name:
                return _err(
                    "component_in_use",
                    f"组件 '{name}' 仍被产品 '{product.get('名称')}' 的 BOM 引用",
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

    plate_names = {
        p.get("盘号") for p in catalog["打印盘"] if isinstance(p, dict)
    }
    if data.plate_name in plate_names:
        return _err("name_conflict", f"打印盘 '{data.plate_name}' 已存在")

    if data.component_name not in _component_names(catalog):
        return _err(
            "component_not_found",
            f"组件 '{data.component_name}' 不存在",
        )

    catalog["打印盘"].append({
        "盘号": data.plate_name,
        "组件": data.component_name,
        "数量": data.quantity,
        "耗时分钟": data.duration_minutes,
    })
    return _commit_and_reload(db, catalog_path, catalog)


def update_plate(db: Session, plate_name: str, data: PlateUpdate) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index(catalog["打印盘"], "盘号", plate_name)
    if idx < 0:
        return _err("not_found", f"打印盘 '{plate_name}' 不存在")

    if data.component_name is not None and data.component_name not in _component_names(catalog):
        return _err(
            "component_not_found",
            f"组件 '{data.component_name}' 不存在",
        )
    if data.quantity is not None and data.quantity <= 0:
        return _err("invalid_input", "数量必须为正整数")
    if data.duration_minutes is not None and data.duration_minutes <= 0:
        return _err("invalid_input", "耗时分钟必须为正整数")

    entry = dict(catalog["打印盘"][idx])
    if data.component_name is not None:
        entry["组件"] = data.component_name
    if data.quantity is not None:
        entry["数量"] = data.quantity
    if data.duration_minutes is not None:
        entry["耗时分钟"] = data.duration_minutes
    catalog["打印盘"][idx] = entry

    return _commit_and_reload(db, catalog_path, catalog)


def delete_plate(db: Session, plate_name: str) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index(catalog["打印盘"], "盘号", plate_name)
    if idx < 0:
        return _err("not_found", f"打印盘 '{plate_name}' 不存在")

    catalog["打印盘"].pop(idx)
    return _commit_and_reload(db, catalog_path, catalog)


# ---------- 产品 ----------

def _validate_bom(catalog: dict, bom: list[BOMItem]) -> dict | None:
    if not bom:
        return _err("invalid_input", "BOM 不能为空")
    comp_names = _component_names(catalog)
    for item in bom:
        if not item.component_name:
            return _err("invalid_input", "BOM 项的组件名不能为空")
        if item.quantity <= 0:
            return _err("invalid_input", "BOM 项数量必须为正整数")
        if item.component_name not in comp_names:
            return _err(
                "component_not_found",
                f"BOM 引用的组件 '{item.component_name}' 不存在",
            )
    return None


def add_product(db: Session, data: ProductCreate) -> dict:
    if not data.name.strip():
        return _err("invalid_input", "产品名称不能为空")

    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)

    prod_names = {
        p.get("名称") for p in catalog["产品"] if isinstance(p, dict)
    }
    if data.name in prod_names:
        return _err("name_conflict", f"产品 '{data.name}' 已存在")

    err = _validate_bom(catalog, data.bom)
    if err:
        return err

    entry: dict[str, Any] = {"名称": data.name}
    if data.description is not None and data.description != "":
        entry["描述"] = data.description
    entry["BOM"] = _bom_to_yaml(data.bom)
    catalog["产品"].append(entry)

    return _commit_and_reload(db, catalog_path, catalog)


def update_product(db: Session, name: str, data: ProductUpdate) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index(catalog["产品"], "名称", name)
    if idx < 0:
        return _err("not_found", f"产品 '{name}' 不存在")

    if data.bom is not None:
        err = _validate_bom(catalog, data.bom)
        if err:
            return err

    entry = dict(catalog["产品"][idx])
    if data.description is not None:
        if data.description == "":
            entry.pop("描述", None)
        else:
            entry["描述"] = data.description
    if data.bom is not None:
        entry["BOM"] = _bom_to_yaml(data.bom)
    catalog["产品"][idx] = entry

    return _commit_and_reload(db, catalog_path, catalog)


def delete_product(db: Session, name: str) -> dict:
    catalog_path = _catalog_path()
    catalog = _load_yaml(catalog_path)
    idx = _find_index(catalog["产品"], "名称", name)
    if idx < 0:
        return _err("not_found", f"产品 '{name}' 不存在")

    # 引用检查：DB 中 pending 订单的 OrderItem
    product_row = db.query(Product).filter(Product.name == name).first()
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
                f"产品 '{name}' 仍被未发货订单引用",
            )

    catalog["产品"].pop(idx)
    return _commit_and_reload(db, catalog_path, catalog)
