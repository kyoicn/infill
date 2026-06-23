"""PRD-007 打印机状态 schemas。

- snapshot：GET /api/printers/{id}/status 返回体
- event：WebSocket 推送的状态变更事件
- timeline：当日 0:00 ~ now 的分钟级状态段
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class TimelineSegment(BaseModel):
    start_minute: int
    end_minute: int
    state: Literal["running", "pause", "idle", "offline"]


class PrinterStatusOut(BaseModel):
    printer_id: int
    name: str
    state: Literal["running", "pause", "idle", "offline", "unconfigured"]
    today_working_minutes: int
    today_total_minutes: int = 1440
    last_state_change_ts: Optional[datetime] = None
    timeline: list[TimelineSegment] = []


class PrinterStatusEvent(BaseModel):
    type: Literal["state_change"] = "state_change"
    printer_id: int
    state: Literal["running", "pause", "idle", "offline"]
    ts: datetime


class HistoryDay(BaseModel):
    date: str  # "YYYY-MM-DD"
    working_minutes: int


class PrinterHistoryOut(BaseModel):
    printer_id: int
    name: str
    history: list[HistoryDay]  # 升序，最旧在前、最新（今日）在后
