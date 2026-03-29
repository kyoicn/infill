from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Printer
from ..schemas import PrinterCreate, PrinterOut

router = APIRouter(prefix="/api/printers", tags=["打印机"])


@router.get("", response_model=list[PrinterOut])
def list_printers(db: Session = Depends(get_db)):
    return db.query(Printer).all()


@router.post("", response_model=PrinterOut)
def create_printer(data: PrinterCreate, db: Session = Depends(get_db)):
    printer = Printer(**data.model_dump())
    db.add(printer)
    db.commit()
    db.refresh(printer)
    return printer


@router.put("/{printer_id}", response_model=PrinterOut)
def update_printer(printer_id: int, data: PrinterCreate, db: Session = Depends(get_db)):
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "打印机不存在")
    printer.name = data.name
    db.commit()
    db.refresh(printer)
    return printer


@router.delete("/{printer_id}")
def delete_printer(printer_id: int, db: Session = Depends(get_db)):
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "打印机不存在")
    db.delete(printer)
    db.commit()
    return {"ok": True}
