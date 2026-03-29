"""产品目录（只读，数据源为 catalog.yaml）"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import Component, PrintConfig, Product
from ..schemas import ComponentOut, PrintConfigOut, ProductOut
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
