"""产品目录（GET 只读 + CRUD 编辑端点）

CRUD 端点通过 catalog_edit 服务走 5 阶段事务（备份 / 写盘 / reload / 失败回滚），
错误情况统一以 HTTP 200 + body.ok=false 返回（与 intake/merge 一致）。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import Component, PrintConfig, Product
from ..schemas import ComponentOut, PrintConfigOut, ProductOut
from ..schemas_catalog_edit import (
    ComponentCreate,
    ComponentUpdate,
    PlateCreate,
    PlateUpdate,
    ProductCreate,
    ProductUpdate,
)
from ..services import catalog_edit
from ..services.catalog import load_catalog

router = APIRouter(prefix="/api", tags=["目录"])


@router.get("/components", response_model=list[ComponentOut])
def list_components(db: Session = Depends(get_db)):
    return db.query(Component).all()


@router.get("/products", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db)):
    return db.query(Product).all()


@router.get("/components/configs/all", response_model=list[PrintConfigOut])
def list_all_configs(db: Session = Depends(get_db)):
    return db.query(PrintConfig).all()


@router.get("/components/{component_id}/configs", response_model=list[PrintConfigOut])
def list_component_configs(component_id: int, db: Session = Depends(get_db)):
    return db.query(PrintConfig).filter(PrintConfig.component_id == component_id).all()


@router.post("/catalog/reload")
def reload_catalog():
    """重新从 catalog.yaml 加载目录"""
    db = SessionLocal()
    try:
        stats = load_catalog(db)
        return {"ok": True, "stats": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


# ---------- 组件 CRUD ----------

@router.post("/catalog/components")
def create_component(data: ComponentCreate, db: Session = Depends(get_db)):
    return catalog_edit.add_component(db, data)


@router.put("/catalog/components/{name}")
def edit_component(name: str, data: ComponentUpdate, db: Session = Depends(get_db)):
    return catalog_edit.update_component(db, name, data)


@router.delete("/catalog/components/{name}")
def remove_component(name: str, db: Session = Depends(get_db)):
    return catalog_edit.delete_component(db, name)


# ---------- 打印盘 CRUD ----------

@router.post("/catalog/plates")
def create_plate(data: PlateCreate, db: Session = Depends(get_db)):
    return catalog_edit.add_plate(db, data)


@router.put("/catalog/plates/{plate_name}")
def edit_plate(plate_name: str, data: PlateUpdate, db: Session = Depends(get_db)):
    return catalog_edit.update_plate(db, plate_name, data)


@router.delete("/catalog/plates/{plate_name}")
def remove_plate(plate_name: str, db: Session = Depends(get_db)):
    return catalog_edit.delete_plate(db, plate_name)


# ---------- 产品 CRUD ----------

@router.post("/catalog/products")
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    return catalog_edit.add_product(db, data)


@router.put("/catalog/products/{name}")
def edit_product(name: str, data: ProductUpdate, db: Session = Depends(get_db)):
    return catalog_edit.update_product(db, name, data)


@router.delete("/catalog/products/{name}")
def remove_product(name: str, db: Session = Depends(get_db)):
    return catalog_edit.delete_product(db, name)
