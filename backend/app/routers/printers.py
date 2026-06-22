from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Printer
from ..schemas import PrinterCreate, PrinterUpdate, PrinterOut

router = APIRouter(prefix="/api/printers", tags=["打印机"])


@router.get("", response_model=list[PrinterOut])
def list_printers(db: Session = Depends(get_db)):
    return db.query(Printer).all()


@router.post("", response_model=PrinterOut)
def create_printer(printer_in: PrinterCreate, request: Request, db: Session = Depends(get_db)):
    printer = Printer(**printer_in.model_dump())
    db.add(printer)
    db.commit()
    db.refresh(printer)
    daemon = getattr(request.app.state, "printer_status_daemon", None)
    if daemon is not None:
        daemon.reconcile_one(printer.id)
    return printer


@router.put("/{printer_id}", response_model=PrinterOut)
def update_printer(
    printer_id: int,
    data: PrinterUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    printer = db.query(Printer).filter(Printer.id == printer_id).first()
    if not printer:
        raise HTTPException(404, "打印机不存在")
    partial = data.model_dump(exclude_unset=True)
    for k, v in partial.items():
        if v == "":
            setattr(printer, k, None)
        else:
            setattr(printer, k, v)
    db.commit()
    db.refresh(printer)
    daemon = getattr(request.app.state, "printer_status_daemon", None)
    if daemon is not None:
        daemon.reconcile_one(printer_id)
    return printer


@router.delete("/{printer_id}")
def delete_printer(printer_id: int, request: Request, db: Session = Depends(get_db)):
    printer = db.query(Printer).filter(Printer.id == printer_id).first()
    if not printer:
        raise HTTPException(404, "打印机不存在")
    daemon = getattr(request.app.state, "printer_status_daemon", None)
    if daemon is not None:
        # daemon 异常（如 paho disconnect 失败）不该阻止 DB 删除；
        # FK CASCADE 仍会清掉 PrinterStatusSample。
        try:
            daemon.unsubscribe_one(printer_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "printer_status: unsubscribe_one failed for printer_id=%s", printer_id
            )
    db.delete(printer)
    db.commit()
    return {"ok": True}
