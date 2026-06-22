"""PRD-007 T3.1：打印机状态 router。

- `GET /api/printers/status/snapshot`：REST 快照，前端 mount 时 + WS 断线重连后调用。
  对每台 Printer：三凭证有任一为空 → state="unconfigured"、空 timeline；齐全则调
  `compute_today_snapshot_for_printer` 算出实时利用率 + 时间轴。

- `WS /api/ws/printers/status`：增量推送。后端 broadcaster 把 `state_change` 事件扇出
  给所有已连接客户端；25 秒无业务消息时发服务端 `ping` 防代理超时。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Printer
from ..schemas_printer_status import PrinterStatusOut
from ..services.printer_utilization import compute_today_snapshot_for_printer


router = APIRouter(prefix="/api/printers", tags=["printer-status"])
ws_router = APIRouter(prefix="/api/ws", tags=["printer-status"])


WS_PING_INTERVAL_SEC = 25


@router.get("/status/snapshot", response_model=list[PrinterStatusOut])
def get_status_snapshot(db: Session = Depends(get_db)) -> list[PrinterStatusOut]:
    """每台打印机一条 PrinterStatusOut，按 printer.id 升序。

    - 三凭证（ip/serial/access_code）任一为空 → state="unconfigured"，timeline 空。
    - 凭证齐全 → 实时聚合今日样本得 (state, working_minutes, last_state_change_ts, timeline)。
    """
    now = datetime.now()
    printers = db.query(Printer).order_by(Printer.id.asc()).all()

    out: list[PrinterStatusOut] = []
    for p in printers:
        if p.ip is None or p.serial is None or p.access_code is None:
            out.append(
                PrinterStatusOut(
                    printer_id=p.id,
                    name=p.name,
                    state="unconfigured",
                    today_working_minutes=0,
                    today_total_minutes=1440,
                    last_state_change_ts=None,
                    timeline=[],
                )
            )
            continue

        state, working_minutes, last_change_ts, timeline = (
            compute_today_snapshot_for_printer(db, p.id, now)
        )
        out.append(
            PrinterStatusOut(
                printer_id=p.id,
                name=p.name,
                state=state,
                today_working_minutes=working_minutes,
                today_total_minutes=1440,
                last_state_change_ts=last_change_ts,
                timeline=timeline,
            )
        )
    return out


@ws_router.websocket("/printers/status")
async def printer_status_ws(ws: WebSocket) -> None:
    """状态增量事件 WebSocket。

    协议：
    - server → client：`{type:"state_change", printer_id, state, ts}` 或服务端 ping
      `{type:"ping", ts}`；25 秒无业务消息发一次 ping 防代理超时。
    - client → server：`{type:"ping"}` 时回 `{type:"pong"}`；其余消息忽略。
    """
    await ws.accept()
    broadcaster = ws.app.state.printer_status_broadcaster
    queue: asyncio.Queue = await broadcaster.register()

    async def _send_loop() -> None:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=WS_PING_INTERVAL_SEC)
            except asyncio.TimeoutError:
                await ws.send_json(
                    {"type": "ping", "ts": datetime.now().isoformat()}
                )
            else:
                await ws.send_json(event)

    async def _recv_loop() -> None:
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_json(
                    {"type": "pong", "ts": datetime.now().isoformat()}
                )

    send_task = asyncio.create_task(_send_loop())
    recv_task = asyncio.create_task(_recv_loop())
    try:
        done, pending = await asyncio.wait(
            [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(queue)
