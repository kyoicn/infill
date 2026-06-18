"""auto-import backend tests (prd-006) — co-authored across Tasks 2.1 / 2.2 / 2.3.

Each task contributes one or more `Test*` classes; fixtures `db_session`
(legacy 2.1 naming) and `db` (2.2/2.3 naming) are both kept for clarity.
LLM calls are stubbed via monkeypatch of get_active_provider — no network.
"""
from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Component, Product, ProductComponent, SystemConfig
from app.services import adb_client as adb_client_mod
from app.services import auto_import as auto_import_mod
from app.services import auto_import_llm
from app.services.adb_client import AdbClient, AdbDevice
from app.services.auto_import import (
    diagnose_adb,
    get_adb_config,
    search_skus,
    set_adb_config,
)
from app.services.auto_import_llm import (
    LLMProviderError,
    match_listing_to_sku,
    parse_xianyu_screenshot,
)


# ---------- shared fixtures ----------


def _make_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return TestSession()


@pytest.fixture()
def db_session():
    session = _make_db_session()
    yield session
    session.close()


@pytest.fixture()
def db():
    session = _make_db_session()
    yield session
    session.close()


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    cp = subprocess.CompletedProcess(args=["adb"], returncode=returncode)
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


# ==== Task 2.1: ADB client + diagnose + config ====


class TestAdbClient:
    """Mock subprocess.run via monkeypatch."""

    def test_is_installed_ok(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            return _make_completed(0, "Android Debug Bridge version 1.0.41\n")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        assert client.is_installed() is True

    def test_is_installed_missing_binary(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            raise FileNotFoundError("adb not found")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        assert client.is_installed() is False

    def test_connect_success(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            return _make_completed(0, "connected to 127.0.0.1:7555\n")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        ok, msg = client.connect("127.0.0.1:7555")
        assert ok is True
        assert "connected to" in msg

    def test_connect_refused(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            return _make_completed(1, "", "failed to connect to 127.0.0.1:7555: Connection refused\n")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        ok, msg = client.connect("127.0.0.1:7555")
        assert ok is False
        assert "failed" in msg.lower() or "refused" in msg.lower()

    def test_list_devices_parses_rows(self, monkeypatch):
        stdout = (
            "List of devices attached\n"
            "127.0.0.1:7555  device product:MuMu model:MuMu transport_id:1\n"
            "emulator-5554  offline\n"
            "\n"
        )

        def fake_run(args, capture_output, text, timeout):
            return _make_completed(0, stdout)

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        devices = client.list_devices()
        assert len(devices) == 2
        assert devices[0].serial == "127.0.0.1:7555"
        assert devices[0].state == "device"
        assert devices[0].properties.get("product") == "MuMu"
        assert devices[1].serial == "emulator-5554"
        assert devices[1].state == "offline"

    def test_screencap_pipes_pull_then_reads(self, monkeypatch, tmp_path):
        calls: list[list[str]] = []
        dest = tmp_path / "shot.png"
        png_payload = b"\x89PNG\r\n\x1a\nFAKE"

        def fake_run(args, capture_output, text, timeout):
            calls.append(list(args))
            if len(args) >= 4 and args[3] == "pull":
                dest.write_bytes(png_payload)
            return _make_completed(0, "")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)

        client = AdbClient()
        data = client.screencap("127.0.0.1:7555", str(dest))
        assert data == png_payload
        all_tokens = [tok for c in calls for tok in c]
        assert "screencap" in all_tokens
        assert "pull" in all_tokens


class TestDiagnoseAdb:
    """Mock AdbClient + socket + ping."""

    def test_all_ok(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, "connected"))
        monkeypatch.setattr(
            AdbClient,
            "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="device")],
        )
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert len(diags) == 4
        assert all(d["ok"] for d in diags), diags

    def test_adb_missing(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: False)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (False, ""))
        monkeypatch.setattr(AdbClient, "list_devices", lambda self: [])

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[0]["ok"] is False
        assert diags[3]["ok"] is False
        assert diags[0]["hint"]

    def test_ping_fails(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: False)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, ""))
        monkeypatch.setattr(
            AdbClient,
            "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="device")],
        )

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[1]["ok"] is False
        assert diags[1]["hint"]

    def test_port_closed(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: False)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (False, ""))
        monkeypatch.setattr(AdbClient, "list_devices", lambda self: [])

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[2]["ok"] is False
        assert diags[2]["hint"]

    def test_device_offline(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, "connected"))
        monkeypatch.setattr(
            AdbClient,
            "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="offline")],
        )

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[0]["ok"] is True
        assert diags[1]["ok"] is True
        assert diags[2]["ok"] is True
        assert diags[3]["ok"] is False


class TestAdbConfig:
    """In-memory SQLite via existing fixture."""

    def test_get_config_returns_defaults_when_unset(self, db_session):
        cfg = get_adb_config(db_session)
        assert cfg == {"device_type": "mumu", "pc_ip": "", "port": 7555}

    def test_set_then_get_roundtrip(self, db_session):
        set_adb_config(db_session, "bluestacks", "192.168.1.100", 5555)
        cfg = get_adb_config(db_session)
        assert cfg == {"device_type": "bluestacks", "pc_ip": "192.168.1.100", "port": 5555}

    def test_set_config_upserts_three_rows(self, db_session):
        set_adb_config(db_session, "ldplayer", "10.0.0.5", 5555)
        set_adb_config(db_session, "mumu", "127.0.0.1", 7555)
        rows = db_session.query(SystemConfig).filter(
            SystemConfig.key.in_([
                "auto_import_adb_device_type",
                "auto_import_adb_pc_ip",
                "auto_import_adb_port",
            ])
        ).all()
        assert len(rows) == 3
        as_dict = {r.key: r.value for r in rows}
        assert as_dict["auto_import_adb_device_type"] == "mumu"
        assert as_dict["auto_import_adb_pc_ip"] == "127.0.0.1"
        assert as_dict["auto_import_adb_port"] == "7555"


# ==== Task 2.2: LLM SKU match + 闲鱼 screenshot parse + SKU search ====


class _FakeProvider:
    """Stub provider whose chat_completion returns a canned string."""

    def __init__(self, content: str):
        self._content = content
        self.last_call: dict | None = None

    def is_configured(self) -> bool:
        return True

    def chat_completion(self, messages, *, json_object=False, **kwargs):
        self.last_call = {
            "messages": messages,
            "json_object": json_object,
            "kwargs": kwargs,
        }
        return self._content


class TestSkuMatch:
    CATALOG = [
        {"sku": "PR-0001", "name": "床头柜", "color": "白色,黑色"},
        {"sku": "PR-0002", "name": "餐桌椅", "color": ""},
    ]

    def test_normal_match_returns_sku_with_confidence(self, monkeypatch):
        fake = _FakeProvider(
            '{"matched_sku_code":"PR-0001","confidence":0.92,"reasoning":"标题里出现床头柜"}'
        )
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        sku, conf, reasoning = match_listing_to_sku("纯色床头柜 白色 现货", self.CATALOG)
        assert sku == "PR-0001"
        assert conf == pytest.approx(0.92)
        assert "床头柜" in reasoning
        assert fake.last_call is not None
        assert fake.last_call["json_object"] is True

    def test_markdown_wrapped_json_strips_correctly(self, monkeypatch):
        wrapped = (
            '```json\n'
            '{"matched_sku_code":"PR-0002","confidence":0.7,"reasoning":"模糊匹配"}\n'
            '```'
        )
        fake = _FakeProvider(wrapped)
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        sku, conf, _ = match_listing_to_sku("餐桌", self.CATALOG)
        assert sku == "PR-0002"
        assert conf == pytest.approx(0.7)

    def test_low_confidence_returns_null_sku(self, monkeypatch):
        fake = _FakeProvider(
            '{"matched_sku_code":null,"confidence":0.3,"reasoning":"无明显匹配"}'
        )
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        sku, conf, _ = match_listing_to_sku("不相关的随机商品", self.CATALOG)
        assert sku is None
        assert conf == pytest.approx(0.3)

    def test_no_api_key_raises_provider_error(self, monkeypatch):
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: None)
        with pytest.raises(LLMProviderError) as ei:
            match_listing_to_sku("床头柜", self.CATALOG)
        assert ei.value.error_kind == "no_api_key"

    def test_json_parse_failure_raises(self, monkeypatch):
        fake = _FakeProvider("this is not json at all {broken")
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        with pytest.raises(LLMProviderError) as ei:
            match_listing_to_sku("床头柜", self.CATALOG)
        assert ei.value.error_kind == "parse_failed"


class TestXianyuParse:
    def test_valid_screenshot_returns_orders(self, monkeypatch):
        canned = (
            '{"orders":[{"external_order_id":"X-1","buyer_nickname":"小明",'
            '"external_created_at":"2026-06-15T12:00:00Z",'
            '"products":[{"listing_title":"床头柜 白色","quantity":1}]}]}'
        )
        fake = _FakeProvider(canned)
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        orders = parse_xianyu_screenshot(b"\x89PNG fake")
        assert len(orders) == 1
        assert orders[0]["external_order_id"] == "X-1"
        assert orders[0]["products"][0]["listing_title"] == "床头柜 白色"

    def test_schema_missing_orders_key_raises(self, monkeypatch):
        fake = _FakeProvider('{"unexpected":"shape"}')
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        with pytest.raises(LLMProviderError) as ei:
            parse_xianyu_screenshot(b"\x89PNG fake")
        assert ei.value.error_kind == "schema_invalid"


def _seed_product(db, sku: str, name: str, colors: list[str] | None = None) -> Product:
    p = Product(sku=sku, name=name)
    db.add(p)
    db.flush()
    if colors:
        comp = Component(sku=f"C-{sku[-4:]}", name=f"{name}-组件", colors=colors)
        db.add(comp)
        db.flush()
        for color in colors:
            db.add(ProductComponent(
                product_id=p.id,
                component_id=comp.id,
                color=color,
                quantity=1,
            ))
    db.commit()
    return p


class TestSkuSearch:
    def test_chinese_substring_match(self, db):
        _seed_product(db, "PR-0001", "床头柜", ["白色", "黑色"])
        _seed_product(db, "PR-0002", "餐桌椅", [])
        hits = search_skus(db, "床头")
        assert len(hits) == 1
        assert hits[0]["sku"] == "PR-0001"
        assert hits[0]["name"] == "床头柜"
        assert hits[0]["color"] is not None
        assert "白色" in hits[0]["color"]
        assert "黑色" in hits[0]["color"]

    def test_sku_code_exact_match(self, db):
        _seed_product(db, "PR-0001", "床头柜")
        _seed_product(db, "PR-0002", "餐桌椅")
        hits = search_skus(db, "PR-0002")
        assert len(hits) == 1
        assert hits[0]["sku"] == "PR-0002"
        assert hits[0]["color"] is None

    def test_empty_query_returns_empty(self, db):
        _seed_product(db, "PR-0001", "床头柜")
        assert search_skus(db, "") == []
        assert search_skus(db, "   ") == []

    def test_limit_caps_results(self, db):
        for i in range(5):
            _seed_product(db, f"PR-000{i + 1}", f"产品{i}")
        hits = search_skus(db, "产品", limit=3)
        assert len(hits) == 3
