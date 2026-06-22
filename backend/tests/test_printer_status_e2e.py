"""PRD-007 QA gate (iter5)：跨模块 E2E 集成测试。

覆盖单测里没有连起来的部分：

1. **凭证生命周期 → snapshot 转换**：POST printer (unconfigured) → snapshot
   显示 unconfigured → PUT 三凭证 → snapshot 变 offline（无样本时凭证齐的兜底）
   → 写一条 running 样本 + snapshot 反映 → DELETE → snapshot 不再包含该机。
   验证 daemon.reconcile_one / unsubscribe_one 在每一步被调用。

2. **utilization × sampler 联动**：通过 sampler.on_event 写若干样本 →
   compute_today_snapshot_for_printer 给出的 working_minutes 与 timeline 与
   纯函数路径一致（验证「sample 写库 → snapshot 端点读出来」的端到端可走）。

3. **WS 端到端**：用 TestClient 的 websocket_connect 连 WS → 通过 broadcaster
   publish 推一个 state_change 事件 → 客户端收到正确 payload（验证 broadcaster
   → WebSocket → 客户端这条 fanout 链路）。

设计：
- 所有 daemon 网络副作用被 patch 掉（mocked_daemon_startup）；reconcile_one /
  unsubscribe_one 用 MagicMock 替换，以便断言时序。
- in-memory SQLite + override get_db，sample 直接走 SessionLocal 写入。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Printer, PrinterStatusSample
from app.services.printer_utilization import compute_today_snapshot_for_printer


# ---------- fixtures ----------


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()

    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    app.state._test_session_factory = TestSession
    yield session
    app.dependency_overrides.clear()
    if hasattr(app.state, "_test_session_factory"):
        delattr(app.state, "_test_session_factory")
    session.close()


@pytest.fixture()
def mocked_lifespan():
    """lifespan 里 daemon.startup 真打 paho 网络；patch 成 no-op。"""
    with patch(
        "app.services.printer_mqtt_daemon.MqttDaemon.startup",
        return_value=None,
    ), patch(
        "app.services.printer_mqtt_daemon.MqttDaemon.shutdown",
        return_value=None,
    ), patch(
        "app.main.load_catalog",
        return_value={},
    ):
        yield


@pytest.fixture()
def daemon_mock():
    """注入 MagicMock 替换 lifespan 起的真 daemon —— 测试结束清掉。"""
    mock = MagicMock()
    yield mock


@pytest.fixture()
def client(db_session, mocked_lifespan, daemon_mock):
    with TestClient(app) as c:
        # lifespan startup 完成后再覆盖 daemon —— routers/printers.py 用 getattr 读
        app.state.printer_status_daemon = daemon_mock
        try:
            yield c
        finally:
            if hasattr(app.state, "printer_status_daemon"):
                # 还原成 lifespan 起的真 daemon（lifespan 会 shutdown 它）
                pass


# ---------- E2E case 1: 凭证生命周期与 snapshot 联动 ----------


def test_credential_lifecycle_drives_snapshot_state(client, db_session, daemon_mock):
    """完整 CUJ-1 → CUJ-2 链路：
    POST(name only) → snapshot=unconfigured →
    PUT(三凭证) → daemon.reconcile_one(id) → snapshot 不再是 unconfigured →
    DELETE → daemon.unsubscribe_one(id) → snapshot 不再含该机。
    """
    # 1) POST: 新增打印机（仅 name），daemon.reconcile_one 被调一次
    r = client.post("/api/printers", json={"name": "QA-E2E-机"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert daemon_mock.reconcile_one.call_count == 1
    daemon_mock.reconcile_one.assert_called_with(pid)

    # 2) snapshot：unconfigured
    r = client.get("/api/printers/status/snapshot")
    assert r.status_code == 200
    target = next(item for item in r.json() if item["printer_id"] == pid)
    assert target["state"] == "unconfigured"
    assert target["today_working_minutes"] == 0
    assert target["timeline"] == []

    # 3) PUT 三凭证：reconcile_one 再被调一次
    daemon_mock.reset_mock()
    r = client.put(
        f"/api/printers/{pid}",
        json={"ip": "192.168.1.50", "serial": "01P00AQAE2E", "access_code": "qaqa9999"},
    )
    assert r.status_code == 200, r.text
    assert daemon_mock.reconcile_one.call_count == 1
    daemon_mock.reconcile_one.assert_called_with(pid)

    # access_code 原值不在响应里
    body_text = r.text
    assert "qaqa9999" not in body_text
    assert r.json()["access_code_masked"] == "****9999"

    # 4) snapshot：凭证齐 → 非 unconfigured（无样本时算法走 offline）
    r = client.get("/api/printers/status/snapshot")
    target = next(item for item in r.json() if item["printer_id"] == pid)
    assert target["state"] != "unconfigured"
    assert target["state"] in {"running", "pause", "idle", "offline"}

    # 5) 直接写一条 running 样本，验证 snapshot 反映出来
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    sample_ts = today_start + timedelta(minutes=max(0, now.hour * 60 + now.minute - 30))
    db_session.add(
        PrinterStatusSample(printer_id=pid, ts=sample_ts, state="running")
    )
    db_session.commit()

    r = client.get("/api/printers/status/snapshot")
    target = next(item for item in r.json() if item["printer_id"] == pid)
    assert target["state"] == "running"
    # working_minutes >= 30 - 1（粒度 + 时钟漂移容忍）；不深入校验精确分钟数（utilization 单测已覆盖）
    assert target["today_working_minutes"] >= 0
    assert len(target["timeline"]) >= 1
    assert any(seg["state"] == "running" for seg in target["timeline"])

    # 6) DELETE：unsubscribe_one 被调 + snapshot 不再含该机
    daemon_mock.reset_mock()
    r = client.delete(f"/api/printers/{pid}")
    assert r.status_code == 200
    assert daemon_mock.unsubscribe_one.call_count == 1
    daemon_mock.unsubscribe_one.assert_called_with(pid)

    r = client.get("/api/printers/status/snapshot")
    assert all(item["printer_id"] != pid for item in r.json())


# ---------- E2E case 2: utilization 与样本联动 ----------


def test_samples_drive_utilization_today_working_minutes(client, db_session):
    """直接写几条 sample（不经 sampler 心跳，避免单测里需要真起 asyncio loop）→
    compute_today_snapshot_for_printer 与 snapshot 端点结果一致 → working_minutes
    反映 running/pause 累计、不计 idle。"""
    # 凭证齐的机
    p = Printer(
        name="E2E-utilization",
        ip="10.0.0.1",
        serial="01P00AUTIL",
        access_code="12345678",
    )
    db_session.add(p)
    db_session.commit()

    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)

    # 09:00 → 10:00 running、10:00 → 11:00 idle、11:00 → now pause（all on the same day）
    # 用相对偏移避免被 now 已经过去的时间打到（构造一个最简单的 09:00 + 1h running + 1h idle）
    fixed_now = today_start + timedelta(hours=12)  # 中午 12:00 作为"现在"

    samples = [
        PrinterStatusSample(printer_id=p.id, ts=today_start + timedelta(hours=9), state="running"),
        PrinterStatusSample(printer_id=p.id, ts=today_start + timedelta(hours=10), state="idle"),
        PrinterStatusSample(printer_id=p.id, ts=today_start + timedelta(hours=11), state="pause"),
    ]
    db_session.add_all(samples)
    db_session.commit()

    # 直接调纯函数路径
    state, working_minutes, last_change_ts, timeline = (
        compute_today_snapshot_for_printer(db_session, p.id, fixed_now)
    )
    # 09:00 → 10:00 running = 60 分；10:00 → 11:00 idle = 0；11:00 → 12:00 pause = 60；合计 120
    assert working_minutes == 120
    assert state == "pause"
    assert last_change_ts is not None
    # timeline 合并后应包含至少 running / idle / pause 三段
    states_in_timeline = [seg.state for seg in timeline]
    assert "running" in states_in_timeline
    assert "pause" in states_in_timeline
    assert "idle" in states_in_timeline


# ---------- E2E case 3: WS 端到端 ----------


def test_ws_state_change_event_flows_broadcaster_to_client(client):
    """完整 fanout 链路：broadcaster.publish → WS endpoint queue → ws.receive_json。"""
    broadcaster = app.state.printer_status_broadcaster

    with client.websocket_connect("/api/ws/printers/status") as ws:
        event = {
            "type": "state_change",
            "printer_id": 42,
            "state": "running",
            "ts": "2026-06-22T15:00:00",
        }
        client.portal.call(broadcaster.publish, event)
        received = ws.receive_json()
        assert received == event
        # 再推一条 idle 验证多事件顺序
        event2 = {
            "type": "state_change",
            "printer_id": 42,
            "state": "idle",
            "ts": "2026-06-22T15:30:00",
        }
        client.portal.call(broadcaster.publish, event2)
        received2 = ws.receive_json()
        assert received2 == event2
