"""PRD-007 T3.1 单测：snapshot router + WS endpoint + lifespan 守护进程接线。

测试约束：
- lifespan 真起 `MqttDaemon.startup()` → 真去 DB 查 Printer 凭证 → 真发 paho 网络请求；
  本测试统一把 `MqttDaemon.startup` patch 成 no-op，避免真打网络。
- in-memory SQLite + 显式 `override get_db`；同一 session 给 app 与测试共用。
- WS 事件注入路径：用 `client.portal.call(broadcaster.publish, event)` 把 publish 协程
  在 TestClient 的事件循环里调度 —— 这是 starlette TestClient 唯一干净的跨线程入口。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Printer, PrinterStatusSample


# ---------- fixtures ----------


@pytest.fixture()
def db_session():
    """共用 in-memory SQLite。lifespan 期间 daemon.startup 被 patch 掉，不查 DB。"""
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
    yield session
    app.dependency_overrides.clear()
    session.close()


@pytest.fixture()
def mocked_daemon_startup():
    """lifespan 里：
    - daemon.startup() 真要起 paho client 连 Bambu —— patch 成 no-op。
    - load_catalog() 依赖磁盘 YAML —— 测试里 patch 掉避免 FileNotFoundError。
    """
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
def client(db_session, mocked_daemon_startup):
    """进入 TestClient 上下文 → 触发 lifespan startup（daemon mock 了不真起）。"""
    with TestClient(app) as c:
        yield c


# ---------- case 1: snapshot 结构 ----------


def test_snapshot_returns_configured_and_unconfigured_printers(client, db_session):
    """两台机：一台凭证齐全 + 有样本，一台只有 name；snapshot 各返回正确 state。"""
    configured = Printer(
        name="1号机", ip="192.168.1.10", serial="01P00ATEST", access_code="12345678"
    )
    unconfigured = Printer(name="2号机")
    db_session.add_all([configured, unconfigured])
    db_session.commit()

    # 给配置过的机插两条今日样本
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    db_session.add_all(
        [
            PrinterStatusSample(
                printer_id=configured.id,
                ts=today_start + timedelta(hours=1),
                state="running",
            ),
            PrinterStatusSample(
                printer_id=configured.id,
                ts=today_start + timedelta(hours=2),
                state="idle",
            ),
        ]
    )
    db_session.commit()

    r = client.get("/api/printers/status/snapshot")
    assert r.status_code == 200, r.text
    body = r.json()

    assert len(body) == 2
    # 按 id 升序
    assert body[0]["printer_id"] == configured.id
    assert body[1]["printer_id"] == unconfigured.id

    first = body[0]
    assert first["name"] == "1号机"
    assert first["state"] in {"running", "pause", "idle", "offline"}
    assert first["today_total_minutes"] == 1440
    assert first["today_working_minutes"] >= 0
    assert isinstance(first["timeline"], list)

    second = body[1]
    assert second["name"] == "2号机"
    assert second["state"] == "unconfigured"
    assert second["today_working_minutes"] == 0
    assert second["today_total_minutes"] == 1440
    assert second["last_state_change_ts"] is None
    assert second["timeline"] == []


# ---------- case 2: WS 推送 ----------


def test_ws_receives_state_change_event_from_broadcaster(client):
    """连 WS → 用 client.portal 在 app 的事件循环里调 broadcaster.publish → 客户端收到。"""
    broadcaster = app.state.printer_status_broadcaster

    with client.websocket_connect("/api/ws/printers/status") as ws:
        event = {
            "type": "state_change",
            "printer_id": 1,
            "state": "running",
            "ts": "2026-06-22T10:00:00",
        }
        # 在 TestClient 的事件循环里调度 publish（broadcaster.publish 是 async）
        client.portal.call(broadcaster.publish, event)

        received = ws.receive_json()
        assert received == event


# ---------- case 3: WS 注销 ----------


def test_ws_disconnect_unregisters_queue(client):
    """连一次 WS、断开 → broadcaster 的 queue 集合长度回到原值。"""
    broadcaster = app.state.printer_status_broadcaster
    before = len(broadcaster._queues)

    with client.websocket_connect("/api/ws/printers/status") as ws:
        # 推一条事件确认 queue 已注册（避免 race：consumer 协程已经 register 完）
        event = {"type": "state_change", "printer_id": 1, "state": "idle", "ts": "x"}
        client.portal.call(broadcaster.publish, event)
        ws.receive_json()
        during = len(broadcaster._queues)

    # 给 server 端 finally 块一点时间跑 unregister
    import time

    for _ in range(20):
        if len(broadcaster._queues) == before:
            break
        time.sleep(0.05)
    after = len(broadcaster._queues)

    assert during == before + 1
    assert after == before


# ---------- case 4: 降级（daemon 没启起来，snapshot 仍能读 DB 算）----------


def test_snapshot_works_even_without_running_daemon(client, db_session):
    """daemon.startup 已被 patch 成 no-op；DB 里仍有样本时 snapshot 端点能正常算。"""
    p = Printer(
        name="降级测试机",
        ip="192.168.1.99",
        serial="01P00ADOWN",
        access_code="87654321",
    )
    db_session.add(p)
    db_session.commit()

    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    db_session.add(
        PrinterStatusSample(
            printer_id=p.id,
            ts=today_start + timedelta(minutes=30),
            state="running",
        )
    )
    db_session.commit()

    r = client.get("/api/printers/status/snapshot")
    assert r.status_code == 200, r.text
    body = r.json()

    target = next(item for item in body if item["printer_id"] == p.id)
    # 凭证齐 → 不是 unconfigured；有样本 → 算出来的 state 是 4 种实样本之一
    assert target["state"] in {"running", "pause", "idle", "offline"}
    assert target["today_working_minutes"] >= 0
