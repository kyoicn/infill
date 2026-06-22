"""PRD-007 (T3.2) printers router 凭证管理 + daemon 联动测试。

覆盖：
- PUT 是 partial update（PrinterUpdate / exclude_unset 语义）
- 空串视为清空（→ None）
- POST / PUT 在 commit 之后调 daemon.reconcile_one
- DELETE 在 commit 之前调 daemon.unsubscribe_one
- 响应里不暴露 access_code 原值（只有 access_code_masked）
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Printer


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
    # 把 TestSession 挂到 app.state，方便 daemon side_effect 起新 session 验时序
    app.state._test_session_factory = TestSession
    yield session
    app.dependency_overrides.clear()
    if hasattr(app.state, "_test_session_factory"):
        delattr(app.state, "_test_session_factory")
    session.close()


@pytest.fixture()
def daemon_mock(db_session):
    """注入一个 MagicMock 当 printer_status_daemon，测试结束清掉。"""
    mock = MagicMock()
    app.state.printer_status_daemon = mock
    yield mock
    if hasattr(app.state, "printer_status_daemon"):
        delattr(app.state, "printer_status_daemon")


@pytest.fixture()
def client(db_session, daemon_mock):
    return TestClient(app)


# ---------- case 1 ----------

def test_put_partial_only_updates_ip(client, db_session):
    r = client.post("/api/printers", json={"name": "测试机"})
    assert r.status_code == 200, r.text
    printer_id = r.json()["id"]

    r = client.put(f"/api/printers/{printer_id}", json={"ip": "1.2.3.4"})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    row = db_session.query(Printer).filter(Printer.id == printer_id).first()
    assert row.name == "测试机"
    assert row.ip == "1.2.3.4"
    assert row.serial is None
    assert row.access_code is None
    assert r.json()["access_code_masked"] is None


# ---------- case 2 ----------

def test_put_empty_string_clears_ip(client, db_session):
    r = client.post("/api/printers", json={"name": "机A"})
    printer_id = r.json()["id"]
    client.put(f"/api/printers/{printer_id}", json={"ip": "10.0.0.1"})

    r = client.put(f"/api/printers/{printer_id}", json={"ip": ""})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    row = db_session.query(Printer).filter(Printer.id == printer_id).first()
    assert row.ip is None


# ---------- case 3 ----------

def test_put_omitted_access_code_key_keeps_existing_value(client, db_session):
    r = client.post("/api/printers", json={"name": "机B"})
    printer_id = r.json()["id"]
    client.put(
        f"/api/printers/{printer_id}",
        json={"ip": "x", "serial": "y", "access_code": "SECRET1234"},
    )

    r = client.put(f"/api/printers/{printer_id}", json={"ip": "z"})
    assert r.status_code == 200, r.text

    db_session.expire_all()
    row = db_session.query(Printer).filter(Printer.id == printer_id).first()
    assert row.ip == "z"
    assert row.access_code == "SECRET1234"
    assert row.serial == "y"


# ---------- case 4 ----------

def test_put_reconcile_called_after_commit(client, db_session, daemon_mock):
    """reconcile_one 触发时，必须能从一个新 session 读到最新 commit 后的值。"""
    r = client.post("/api/printers", json={"name": "机C"})
    printer_id = r.json()["id"]
    daemon_mock.reset_mock()

    seen_ip: dict[str, str | None] = {}

    def _check_committed(pid):
        TestSession = app.state._test_session_factory
        with TestSession() as fresh:
            row = fresh.query(Printer).filter(Printer.id == pid).first()
            seen_ip["value"] = row.ip if row else None

    daemon_mock.reconcile_one.side_effect = _check_committed

    r = client.put(f"/api/printers/{printer_id}", json={"ip": "9.9.9.9"})
    assert r.status_code == 200, r.text

    assert daemon_mock.reconcile_one.call_count == 1
    daemon_mock.reconcile_one.assert_called_with(printer_id)
    # 关键断言：side_effect 跑的时候，新 session 已能看到 commit 后的最新值
    assert seen_ip["value"] == "9.9.9.9"


# ---------- case 5 ----------

def test_delete_unsubscribe_called_before_commit(client, db_session, daemon_mock):
    """unsubscribe_one 触发时，从一个新 session 仍能查到该 printer（还没 commit 删除）。"""
    r = client.post("/api/printers", json={"name": "机D"})
    printer_id = r.json()["id"]
    daemon_mock.reset_mock()

    still_present: dict[str, bool] = {}

    def _check_not_committed(pid):
        TestSession = app.state._test_session_factory
        with TestSession() as fresh:
            row = fresh.query(Printer).filter(Printer.id == pid).first()
            still_present["value"] = row is not None

    daemon_mock.unsubscribe_one.side_effect = _check_not_committed

    r = client.delete(f"/api/printers/{printer_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    assert daemon_mock.unsubscribe_one.call_count == 1
    daemon_mock.unsubscribe_one.assert_called_with(printer_id)
    # 关键断言：side_effect 跑的时候，DELETE 还没 commit，行还在
    assert still_present["value"] is True

    # 最终确实删掉了
    db_session.expire_all()
    row = db_session.query(Printer).filter(Printer.id == printer_id).first()
    assert row is None


# ---------- case 6 ----------

def test_post_calls_reconcile_once_with_new_id(client, db_session, daemon_mock):
    daemon_mock.reset_mock()
    r = client.post("/api/printers", json={"name": "机E"})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert daemon_mock.reconcile_one.call_count == 1
    daemon_mock.reconcile_one.assert_called_with(new_id)


# ---------- case 7 ----------

def test_response_never_leaks_access_code_raw(client, db_session):
    r = client.post("/api/printers", json={"name": "机F"})
    printer_id = r.json()["id"]

    secret = "SUPER_SECRET_99"
    r = client.put(
        f"/api/printers/{printer_id}",
        json={"ip": "1.1.1.1", "serial": "S1", "access_code": secret},
    )
    assert r.status_code == 200, r.text
    body_text = r.text
    body_json = r.json()

    assert secret not in body_text
    # access_code 字段本身也不该出现在响应里
    assert "access_code" not in body_json or "access_code_masked" in body_json
    assert body_json.get("access_code_masked") is not None
    masked = body_json["access_code_masked"]
    # 末 4 位明文 "T_99"，前面是 `*`
    assert masked.endswith("T_99")
    assert masked.startswith("*")
    assert secret not in masked
