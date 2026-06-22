"""PRD-007 T2.3：打印机利用率纯函数 + DB 便利接口测试。

覆盖：
- 纯函数 8 类 case：纯 IDLE / 全天 RUNNING / 跨午夜 / 末段延展 /
  offline 不计入 / pause 计入 / 相邻同 state 合并 / 1440 截断
- 便利接口 1 个 case：in-memory SQLite round-trip
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Printer, PrinterStatusSample
from app.services.printer_utilization import (
    compute_today_snapshot,
    compute_today_snapshot_for_printer,
)


@dataclass
class _Sample:
    """轻量 sample stub —— 纯函数只读 .ts / .state，不依赖 ORM。"""

    ts: datetime
    state: str


# 固定 today_start，避免时区干扰
_TODAY_START = datetime(2026, 6, 22, 0, 0, 0)
_YESTERDAY_START = _TODAY_START - timedelta(days=1)


def _at(hour: int, minute: int = 0) -> datetime:
    return _TODAY_START + timedelta(hours=hour, minutes=minute)


# ---------- case 1: 纯 IDLE 全天 ----------

def test_case1_pure_idle_all_day():
    samples = [
        _Sample(ts=_TODAY_START, state="idle"),
        _Sample(ts=_TODAY_START + timedelta(hours=24), state="idle"),
    ]
    now = _TODAY_START + timedelta(hours=23, minutes=59, seconds=59)
    working, timeline = compute_today_snapshot(samples, now, _TODAY_START)
    assert working == 0
    assert len(timeline) == 1
    assert timeline[0].state == "idle"
    assert timeline[0].start_minute == 0


# ---------- case 2: 全天 RUNNING ----------

def test_case2_full_day_running():
    samples = [_Sample(ts=_TODAY_START, state="running")]
    now = _TODAY_START + timedelta(hours=24)
    working, timeline = compute_today_snapshot(samples, now, _TODAY_START)
    # 锁定：end_minute=1440（clamp）、working_minutes=1440
    assert working == 1440
    assert len(timeline) == 1
    assert timeline[0].state == "running"
    assert timeline[0].start_minute == 0
    assert timeline[0].end_minute == 1440


# ---------- case 3: 跨午夜（pre-sample 推断起点） ----------

def test_case3_cross_midnight_pre_sample_extends():
    # 昨天 23:00 running，今天没有任何样本，now = 今天 12:00
    pre_sample = _Sample(ts=_YESTERDAY_START + timedelta(hours=23), state="running")
    now = _at(12)
    working, timeline = compute_today_snapshot([pre_sample], now, _TODAY_START)
    # 跨午夜：从 00:00 延展到 12:00 都是 running → 720 分钟
    assert working == 720
    assert len(timeline) == 1
    assert timeline[0].state == "running"
    assert timeline[0].start_minute == 0
    assert timeline[0].end_minute == 720


# ---------- case 4: 末段从最后样本延展到 now ----------

def test_case4_last_segment_extends_to_now():
    # 09:00 running，无后续样本，now = 10:00
    samples = [_Sample(ts=_at(9), state="running")]
    now = _at(10)
    working, timeline = compute_today_snapshot(samples, now, _TODAY_START)
    # 关键验证：末段 running 延展到 now → running 段结束于分钟 600
    assert working == 60
    running_segs = [seg for seg in timeline if seg.state == "running"]
    assert len(running_segs) == 1
    assert running_segs[0].start_minute == 540
    assert running_segs[0].end_minute == 600


# ---------- case 5: offline 不计入 working ----------

def test_case5_offline_not_counted():
    # 09:00 running、10:00 offline，now = 12:00
    samples = [
        _Sample(ts=_at(9), state="running"),
        _Sample(ts=_at(10), state="offline"),
    ]
    now = _at(12)
    working, timeline = compute_today_snapshot(samples, now, _TODAY_START)
    assert working == 60
    states_in_tl = [seg.state for seg in timeline]
    assert "running" in states_in_tl
    assert "offline" in states_in_tl
    # 段长度核查
    running_seg = next(seg for seg in timeline if seg.state == "running")
    assert running_seg.end_minute - running_seg.start_minute == 60
    # offline 段（最后一段）延展到 now（12:00 = 720 分钟）
    last_seg = timeline[-1]
    assert last_seg.state == "offline"
    assert last_seg.end_minute == 720


# ---------- case 6: pause 计入 working ----------

def test_case6_pause_counted():
    # 09:00 pause、10:00 idle，now = 12:00
    samples = [
        _Sample(ts=_at(9), state="pause"),
        _Sample(ts=_at(10), state="idle"),
    ]
    now = _at(12)
    working, timeline = compute_today_snapshot(samples, now, _TODAY_START)
    assert working == 60
    pause_seg = next(seg for seg in timeline if seg.state == "pause")
    assert pause_seg.end_minute - pause_seg.start_minute == 60
    last_seg = timeline[-1]
    assert last_seg.state == "idle"
    assert last_seg.end_minute == 720


# ---------- case 7: 相邻同 state 段合并 ----------

def test_case7_adjacent_same_state_merged():
    # 09:00 / 09:30 / 10:00 全是 running，now = 10:30
    samples = [
        _Sample(ts=_at(9), state="running"),
        _Sample(ts=_at(9, 30), state="running"),
        _Sample(ts=_at(10), state="running"),
    ]
    now = _at(10, 30)
    working, timeline = compute_today_snapshot(samples, now, _TODAY_START)
    # 关键验证：三条 running 样本应合并为单段 running
    running_segs = [seg for seg in timeline if seg.state == "running"]
    assert len(running_segs) == 1
    assert running_segs[0].start_minute == 540  # 09:00
    assert running_segs[0].end_minute == 630   # 10:30
    assert working == 90


# ---------- case 8: 累计超过 1440 → 截断 ----------

def test_case8_truncated_to_1440():
    # pre-sample running + now 远超 24h → 算法应 clamp 到 1440
    pre_sample = _Sample(ts=_YESTERDAY_START + timedelta(hours=12), state="running")
    now = _TODAY_START + timedelta(hours=25)
    working, timeline = compute_today_snapshot([pre_sample], now, _TODAY_START)
    assert working == 1440
    assert len(timeline) == 1
    assert timeline[0].state == "running"
    assert timeline[0].start_minute == 0
    assert timeline[0].end_minute == 1440


# ---------- 空 samples 兜底（task spec 中"空 samples"分支） ----------

def test_empty_samples_returns_full_offline():
    now = _at(2)
    working, timeline = compute_today_snapshot([], now, _TODAY_START)
    assert working == 0
    assert len(timeline) == 1
    assert timeline[0].state == "offline"
    assert timeline[0].start_minute == 0
    assert timeline[0].end_minute == 120  # 2 小时


# ---------- case 9: DB round-trip ----------

def _new_engine_with_schema():
    """完全空 in-memory SQLite + FK ON + 建好所有 schema。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine


def test_case9_db_round_trip_matches_pure_function():
    engine = _new_engine_with_schema()
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()
    try:
        p = Printer(name="P1", ip="192.168.1.10", serial="SN-0001", access_code="abcd1234")
        session.add(p)
        session.commit()

        # 5 条样本：09:00 running → 10:00 pause → 11:00 idle → 12:00 running → 13:00 offline
        sample_specs = [
            (_at(9), "running"),
            (_at(10), "pause"),
            (_at(11), "idle"),
            (_at(12), "running"),
            (_at(13), "offline"),
        ]
        for ts, state in sample_specs:
            session.add(PrinterStatusSample(printer_id=p.id, ts=ts, state=state))
        session.commit()

        now = _at(14)

        current_state, working_minutes, last_change_ts, timeline = (
            compute_today_snapshot_for_printer(session, p.id, now)
        )

        # 形态验证
        assert current_state == "offline"  # 最后一条样本是 offline
        # 末段 offline 延展到 now → last_state_change_ts = 那条 13:00 offline 的 ts
        assert last_change_ts == _at(13)

        # 与纯函数对照
        pure_samples = [_Sample(ts=ts, state=state) for ts, state in sample_specs]
        pure_working, pure_timeline = compute_today_snapshot(pure_samples, now, _TODAY_START)
        assert working_minutes == pure_working
        assert [(s.start_minute, s.end_minute, s.state) for s in timeline] == [
            (s.start_minute, s.end_minute, s.state) for s in pure_timeline
        ]

        # working_minutes：09-10 running (60) + 10-11 pause (60) + 12-13 running (60) = 180
        assert working_minutes == 180
    finally:
        session.close()
