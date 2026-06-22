"""PRD-007 T1.2 schemas 单测。

覆盖：
- PrinterOut access_code 掩码逻辑（含 4 位边界 → 全 `*`）
- PrinterUpdate exclude_unset + extra="forbid"
- PrinterStatusOut round-trip（含 / 不含 timeline）
- PrinterStatusEvent model_dump + model_dump_json 形态
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import PrinterOut, PrinterUpdate, _mask_access_code
from app.schemas_printer_status import (
    PrinterStatusEvent,
    PrinterStatusOut,
    TimelineSegment,
)


# ---------- _mask_access_code / PrinterOut 掩码 ----------

class _PrinterORMStub:
    """模拟 SQLAlchemy ORM 行：用 attribute 而非 dict。"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_mask_access_code_eight_digits():
    assert _mask_access_code("12345678") == "****5678"


def test_mask_access_code_none():
    assert _mask_access_code(None) is None


def test_mask_access_code_shorter_than_four():
    # 不足 4 位 → 全 mask、长度等于原长
    assert _mask_access_code("abc") == "***"
    assert _mask_access_code("a") == "*"
    assert _mask_access_code("") == ""


def test_mask_access_code_exactly_four():
    # 恰 4 位 → "****" 全掩码（避免无 `*` 时看不出来是掩码）
    assert _mask_access_code("1234") == "****"


def test_printer_out_from_orm_masks_access_code():
    orm = _PrinterORMStub(id=1, name="P1", ip="192.168.1.10", serial="SN-001", access_code="12345678")
    out = PrinterOut.model_validate(orm)
    assert out.id == 1
    assert out.name == "P1"
    assert out.ip == "192.168.1.10"
    assert out.serial == "SN-001"
    assert out.access_code_masked == "****5678"
    # 防回归：序列化里不能漏 access_code 原值
    dumped = out.model_dump()
    assert "access_code" not in dumped
    assert dumped["access_code_masked"] == "****5678"


def test_printer_out_from_orm_none_access_code():
    orm = _PrinterORMStub(id=2, name="P2", ip=None, serial=None, access_code=None)
    out = PrinterOut.model_validate(orm)
    assert out.access_code_masked is None


def test_printer_out_from_dict_masks_access_code():
    out = PrinterOut.model_validate({"id": 3, "name": "P3", "access_code": "abcd1234"})
    assert out.access_code_masked == "****1234"


# ---------- PrinterUpdate ----------

def test_printer_update_exclude_unset_only_includes_provided_fields():
    upd = PrinterUpdate(name="test")
    assert upd.model_dump(exclude_unset=True) == {"name": "test"}


def test_printer_update_allows_all_fields():
    upd = PrinterUpdate(name="x", ip="1.2.3.4", serial="SN", access_code="ac")
    assert upd.model_dump(exclude_unset=True) == {
        "name": "x",
        "ip": "1.2.3.4",
        "serial": "SN",
        "access_code": "ac",
    }


def test_printer_update_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PrinterUpdate(name="x", garbage="y")


# ---------- PrinterStatusOut ----------

def test_printer_status_out_empty_timeline_round_trip():
    snapshot = PrinterStatusOut(
        printer_id=1,
        name="P1",
        state="idle",
        today_working_minutes=0,
    )
    assert snapshot.today_total_minutes == 1440
    assert snapshot.last_state_change_ts is None
    assert snapshot.timeline == []
    # round-trip
    again = PrinterStatusOut.model_validate(snapshot.model_dump())
    assert again == snapshot


def test_printer_status_out_with_timeline_round_trip():
    ts = datetime(2026, 6, 22, 8, 30, tzinfo=timezone.utc)
    snapshot = PrinterStatusOut(
        printer_id=2,
        name="P2",
        state="running",
        today_working_minutes=120,
        today_total_minutes=1440,
        last_state_change_ts=ts,
        timeline=[
            TimelineSegment(start_minute=0, end_minute=60, state="offline"),
            TimelineSegment(start_minute=60, end_minute=180, state="running"),
            TimelineSegment(start_minute=180, end_minute=240, state="pause"),
        ],
    )
    again = PrinterStatusOut.model_validate(snapshot.model_dump())
    assert again == snapshot
    assert len(again.timeline) == 3
    assert again.timeline[1].state == "running"


def test_printer_status_out_state_unconfigured_allowed():
    snapshot = PrinterStatusOut(
        printer_id=3,
        name="P3",
        state="unconfigured",
        today_working_minutes=0,
    )
    assert snapshot.state == "unconfigured"


def test_printer_status_out_rejects_invalid_state():
    with pytest.raises(ValidationError):
        PrinterStatusOut(printer_id=1, name="P1", state="bogus", today_working_minutes=0)


# ---------- PrinterStatusEvent ----------

def test_printer_status_event_model_dump_shape():
    ts = datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc)
    ev = PrinterStatusEvent(printer_id=7, state="running", ts=ts)
    dumped = ev.model_dump()
    assert dumped["type"] == "state_change"
    assert dumped["printer_id"] == 7
    assert dumped["state"] == "running"
    assert dumped["ts"] == ts


def test_printer_status_event_model_dump_json_is_valid_json():
    ts = datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc)
    ev = PrinterStatusEvent(printer_id=7, state="pause", ts=ts)
    payload = ev.model_dump_json()
    parsed = json.loads(payload)
    assert parsed["type"] == "state_change"
    assert parsed["printer_id"] == 7
    assert parsed["state"] == "pause"
    assert isinstance(parsed["ts"], str)


def test_printer_status_event_rejects_non_state_change_type():
    with pytest.raises(ValidationError):
        PrinterStatusEvent(
            type="something_else",  # type: ignore[arg-type]
            printer_id=1,
            state="idle",
            ts=datetime.now(timezone.utc),
        )


def test_printer_status_event_rejects_unconfigured_state():
    # event 只在 running/pause/idle/offline 之间切；unconfigured 不是真实状态变更
    with pytest.raises(ValidationError):
        PrinterStatusEvent(
            printer_id=1,
            state="unconfigured",  # type: ignore[arg-type]
            ts=datetime.now(timezone.utc),
        )
