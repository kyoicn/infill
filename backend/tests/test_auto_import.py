"""Task 2.3 测试：auto_import router 的 scan / commit / -redoN 行为。

只覆盖 Task 2.3 拥有的部分（CUJ-1/3 路径 + 路由编排）：
- TestXhsScan：必填校验 / 重复识别 / LLM 错误降级 / stats 计数
- TestCommit：成功路径 / SKU 失败回滚 / 重复静默跳过 / 响应信封形状
- TestRedoSuffix：override 时 `-redoN` 递增 + 跨平台不互串

不覆盖：CUJ-2 异步截屏（依赖 Task 2.1 的 AdbClient 实现）；
配置 GET/PUT/test-adb（Task 2.1 实现真实逻辑后再补）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import subprocess

from app.database import Base, get_db
from app.main import app
from app.models import Component, Order, OrderItem, Product, ProductComponent, SystemConfig
from app.services import adb_client as adb_client_mod
from app.services import auto_import as auto_import_mod
from app.services import auto_import_llm
from app.services.adb_client import AdbClient as RealAdbClient
from app.services.adb_client import AdbDevice
from app.services.auto_import import (
    diagnose_adb,
    get_adb_config,
    search_skus,
    set_adb_config,
)
from app.services.auto_import_llm import (
    match_listing_to_sku,
    parse_xianyu_screenshot,
)
from app.services.intake_llm import LLMProviderError


# ============================================================
# fixtures
# ============================================================

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

    # 临时注册 auto_import.router 到 app（Task 5.1 才在 main.py 注册）
    from app.routers import auto_import as auto_import_router

    already_registered = any(
        getattr(r, "path", "").startswith("/api/auto-import")
        for r in app.router.routes
    )
    if not already_registered:
        app.include_router(auto_import_router.router)

    app.dependency_overrides[get_db] = _override_get_db
    yield session
    app.dependency_overrides.clear()
    session.close()


@pytest.fixture()
def client(db_session):
    return TestClient(app)


@pytest.fixture()
def products(db_session):
    """两个产品 + 一个未使用的，给 SKU 查询用。"""
    p1 = Product(sku="PR-0001", name="龙猫-大号", description="")
    p2 = Product(sku="PR-0002", name="龙猫-小号", description="")
    p3 = Product(sku="PR-0003", name="床头柜", description="")
    db_session.add_all([p1, p2, p3])
    db_session.commit()
    return [p1, p2, p3]


def _set_llm(monkeypatch, mapping: dict[str, dict] | None = None, fail_titles: set[str] | None = None):
    """把 auto_import_llm.match_listing_to_sku monkeypatch 成可预测的实现。

    mapping: {listing_title: {matched_sku_code, confidence, reasoning}}
    fail_titles: 这些 title 调用时抛 LLMProviderError
    """
    mapping = mapping or {}
    fail_titles = fail_titles or set()

    def fake(listing_title: str, catalog):
        if listing_title in fail_titles:
            raise LLMProviderError("timeout", "fake LLM timeout", None)
        if listing_title in mapping:
            return mapping[listing_title]
        return {"matched_sku_code": None, "confidence": 0.0, "reasoning": "fake no-match"}

    monkeypatch.setattr(auto_import_llm, "match_listing_to_sku", fake)


# ============================================================
# TestXhsScan
# ============================================================

class TestXhsScan:
    def test_scan_drops_missing_required_fields(self, client, db_session, products, monkeypatch):
        """缺 external_order_id / buyer_nickname / 空 products 任一 → 丢弃。"""
        _set_llm(monkeypatch, mapping={
            "龙猫-大号": {"matched_sku_code": "PR-0001", "confidence": 0.95, "reasoning": "ok"},
        })

        body = {
            "batch_id": "b1",
            "raw_orders": [
                # 1. 有效
                {
                    "external_order_id": "X1",
                    "buyer_nickname": "Alice",
                    "external_created_at": "2026-06-18T12:00:00",
                    "products": [{"listing_title": "龙猫-大号", "quantity": 1}],
                },
                # 2. 缺 external_order_id
                {
                    "external_order_id": None,
                    "buyer_nickname": "Bob",
                    "products": [{"listing_title": "龙猫-大号", "quantity": 1}],
                },
                # 3. 缺 buyer_nickname
                {
                    "external_order_id": "X3",
                    "buyer_nickname": "",
                    "products": [{"listing_title": "龙猫-大号", "quantity": 1}],
                },
                # 4. 空 products
                {
                    "external_order_id": "X4",
                    "buyer_nickname": "Dave",
                    "products": [],
                },
            ],
        }
        r = client.post("/api/auto-import/xhs/scan", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert len(data["items"]) == 1
        assert data["items"][0]["external_order_id"] == "X1"
        assert len(data["dropped"]) == 3
        assert all(d["reason"] == "missing_required_fields" for d in data["dropped"])
        assert data["stats"]["dropped_count"] == 3
        assert data["stats"]["total"] == 1

    def test_scan_marks_duplicates_against_db(self, client, db_session, products, monkeypatch):
        """DB 已存在同 (platform, external_order_id) 的订单 → is_duplicate=True + existing_order_id 指回。"""
        _set_llm(monkeypatch, mapping={
            "龙猫-大号": {"matched_sku_code": "PR-0001", "confidence": 0.95, "reasoning": "ok"},
        })

        # 先在 DB 造一条已存在的 xhs 订单
        existing = Order(
            status="pending",
            platform="xhs",
            external_order_id="DUP123",
            buyer_nickname="OldAlice",
        )
        db_session.add(existing)
        db_session.flush()
        db_session.add(OrderItem(order_id=existing.id, product_id=products[0].id, quantity=1))
        db_session.commit()
        existing_id = existing.id

        body = {
            "batch_id": "b2",
            "raw_orders": [
                {
                    "external_order_id": "DUP123",  # 重复
                    "buyer_nickname": "Alice",
                    "products": [{"listing_title": "龙猫-大号", "quantity": 1}],
                },
                {
                    "external_order_id": "NEW456",  # 新单
                    "buyer_nickname": "Bob",
                    "products": [{"listing_title": "龙猫-大号", "quantity": 1}],
                },
            ],
        }
        r = client.post("/api/auto-import/xhs/scan", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert len(data["items"]) == 2

        by_eid = {it["external_order_id"]: it for it in data["items"]}
        assert by_eid["DUP123"]["is_duplicate"] is True
        assert by_eid["DUP123"]["existing_order_id"] == existing_id
        assert by_eid["NEW456"]["is_duplicate"] is False
        assert by_eid["NEW456"]["existing_order_id"] is None
        assert data["stats"]["duplicate_count"] == 1

    def test_scan_per_product_llm_failure_doesnt_abort_batch(self, client, db_session, products, monkeypatch):
        """某条商品 LLM 失败 → 该商品降级（matched_sku=None / confidence=0），其他商品继续。"""
        _set_llm(
            monkeypatch,
            mapping={
                "龙猫-大号": {"matched_sku_code": "PR-0001", "confidence": 0.95, "reasoning": "ok"},
                "床头柜": {"matched_sku_code": "PR-0003", "confidence": 0.90, "reasoning": "ok"},
            },
            fail_titles={"会失败的"},
        )

        body = {
            "batch_id": "b3",
            "raw_orders": [
                {
                    "external_order_id": "X1",
                    "buyer_nickname": "Alice",
                    "products": [
                        {"listing_title": "龙猫-大号", "quantity": 1},
                        {"listing_title": "会失败的", "quantity": 2},
                        {"listing_title": "床头柜", "quantity": 1},
                    ],
                },
            ],
        }
        r = client.post("/api/auto-import/xhs/scan", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert len(data["items"]) == 1
        prods = data["items"][0]["products"]
        assert len(prods) == 3
        # 成功的两条
        assert prods[0]["matched_sku_code"] == "PR-0001"
        assert prods[2]["matched_sku_code"] == "PR-0003"
        # 失败的一条降级
        assert prods[1]["matched_sku_code"] is None
        assert prods[1]["confidence"] == 0.0
        assert "LLM" in prods[1]["reasoning"]

    def test_scan_stats_count_correctly(self, client, db_session, products, monkeypatch):
        """阈值 ≥0.85 高 / ≥0.55 中 / <0.55 低；按商品（而非订单）计数。"""
        _set_llm(monkeypatch, mapping={
            "高_1": {"matched_sku_code": "PR-0001", "confidence": 0.95, "reasoning": ""},
            "高_2": {"matched_sku_code": "PR-0001", "confidence": 0.85, "reasoning": ""},
            "中_1": {"matched_sku_code": "PR-0002", "confidence": 0.70, "reasoning": ""},
            "中_2": {"matched_sku_code": "PR-0002", "confidence": 0.55, "reasoning": ""},
            "低_1": {"matched_sku_code": None, "confidence": 0.30, "reasoning": ""},
        })

        def _mk(eid, titles):
            return {
                "external_order_id": eid,
                "buyer_nickname": "X",
                "products": [{"listing_title": t, "quantity": 1} for t in titles],
            }

        body = {
            "batch_id": "b4",
            "raw_orders": [
                _mk("A", ["高_1", "高_2", "中_1"]),
                _mk("B", ["中_2", "低_1"]),
            ],
        }
        r = client.post("/api/auto-import/xhs/scan", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        s = data["stats"]
        assert s["total"] == 2
        assert s["dropped_count"] == 0
        assert s["duplicate_count"] == 0
        assert s["high_conf"] == 2
        assert s["mid_conf"] == 2
        assert s["low_conf"] == 1


# ============================================================
# TestCommit
# ============================================================

class TestCommit:
    def test_commit_50_orders_happy_path(self, client, db_session, products):
        """50 单全部入库，状态 pending，外部字段写齐。"""
        items = [
            {
                "external_order_id": f"E{i}",
                "buyer_nickname": f"buyer_{i}",
                "external_created_at": "2026-06-18T10:00:00",
                "platform": "xhs",
                "override_duplicate": False,
                "products": [
                    {"sku": "PR-0001", "quantity": 1},
                    {"sku": "PR-0002", "quantity": 2},
                ],
            }
            for i in range(50)
        ]
        r = client.post("/api/auto-import/commit", json={"batch_id": "c1", "items": items})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["stats"]["新增"] == 50
        assert data["stats"]["重复跳过"] == 0
        assert len(data["created_order_ids"]) == 50

        # DB 真实落库
        db_session.expire_all()
        all_orders = db_session.query(Order).filter(Order.platform == "xhs").all()
        assert len(all_orders) == 50
        for o in all_orders:
            assert o.status == "pending"
            assert o.buyer_nickname.startswith("buyer_")
            items_rows = db_session.query(OrderItem).filter(OrderItem.order_id == o.id).all()
            assert len(items_rows) == 2

    def test_commit_atomic_rollback_on_missing_sku(self, client, db_session, products):
        """任一 SKU 不存在 → 整批回滚 + 错误信封 + DB 无任何写入。"""
        # 计数初始 orders
        before = db_session.query(Order).count()

        items = [
            {
                "external_order_id": "E1",
                "buyer_nickname": "Alice",
                "platform": "xhs",
                "products": [{"sku": "PR-0001", "quantity": 1}],
            },
            {
                "external_order_id": "E2",
                "buyer_nickname": "Bob",
                "platform": "xhs",
                "products": [{"sku": "PR-FAKE", "quantity": 1}],  # 不存在
            },
        ]
        r = client.post("/api/auto-import/commit", json={"batch_id": "c2", "items": items})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is False
        assert data["error_kind"] == "commit_sku_not_found"
        assert "PR-FAKE" in data["error"]

        # DB 没有新增任何 order
        db_session.expire_all()
        after = db_session.query(Order).count()
        assert after == before

    def test_commit_dedupe_skipped_silently_no_override(self, client, db_session, products):
        """已有同 (platform, external_order_id) 的订单，commit 时静默跳过 + 计入 dup_skipped。"""
        # 预置一条已存在的
        existing = Order(
            status="pending",
            platform="xhs",
            external_order_id="DUPX",
            buyer_nickname="OldAlice",
        )
        db_session.add(existing)
        db_session.commit()

        items = [
            {
                "external_order_id": "DUPX",
                "buyer_nickname": "Alice",
                "platform": "xhs",
                "override_duplicate": False,
                "products": [{"sku": "PR-0001", "quantity": 1}],
            },
            {
                "external_order_id": "NEWX",
                "buyer_nickname": "Bob",
                "platform": "xhs",
                "override_duplicate": False,
                "products": [{"sku": "PR-0002", "quantity": 1}],
            },
        ]
        r = client.post("/api/auto-import/commit", json={"batch_id": "c3", "items": items})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["stats"]["新增"] == 1
        assert data["stats"]["重复跳过"] == 1
        assert len(data["created_order_ids"]) == 1

    def test_commit_response_envelope_shape(self, client, db_session, products):
        """成功响应必含 ok / stats / created_order_ids / total_ms；stats 是中文键。"""
        items = [{
            "external_order_id": "E1",
            "buyer_nickname": "Alice",
            "platform": "xhs",
            "products": [{"sku": "PR-0001", "quantity": 1}],
        }]
        r = client.post("/api/auto-import/commit", json={"batch_id": "c4", "items": items})
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(data.keys()) >= {"ok", "stats", "created_order_ids", "total_ms"}
        assert data["ok"] is True
        # CommitStats 用中文 alias 序列化（PRD 文案保留）
        assert set(data["stats"].keys()) == {"新增", "重复跳过", "手动跳过", "SKU匹配率"}
        assert isinstance(data["total_ms"], int)
        assert data["total_ms"] >= 0


# ============================================================
# TestRedoSuffix
# ============================================================

class TestRedoSuffix:
    def test_first_override_appends_redo1(self, client, db_session, products):
        """override_duplicate=True 且 DB 已有 base id → 写入 <base>-redo1。"""
        # 预置已存在的
        existing = Order(
            status="pending",
            platform="xhs",
            external_order_id="A123",
            buyer_nickname="Old",
        )
        db_session.add(existing)
        db_session.commit()

        items = [{
            "external_order_id": "A123",
            "buyer_nickname": "AliceRedo",
            "platform": "xhs",
            "override_duplicate": True,
            "products": [{"sku": "PR-0001", "quantity": 1}],
        }]
        r = client.post("/api/auto-import/commit", json={"batch_id": "r1", "items": items})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["stats"]["新增"] == 1

        db_session.expire_all()
        new_order = (
            db_session.query(Order)
            .filter(Order.platform == "xhs", Order.buyer_nickname == "AliceRedo")
            .one()
        )
        assert new_order.external_order_id == "A123-redo1"

    def test_second_override_appends_redo2(self, client, db_session, products):
        """已有 base + base-redo1 → 第三条 override 写 base-redo2。"""
        for eid in ["A123", "A123-redo1"]:
            db_session.add(Order(
                status="pending",
                platform="xhs",
                external_order_id=eid,
                buyer_nickname="Old",
            ))
        db_session.commit()

        items = [{
            "external_order_id": "A123",
            "buyer_nickname": "AliceRedo2",
            "platform": "xhs",
            "override_duplicate": True,
            "products": [{"sku": "PR-0001", "quantity": 1}],
        }]
        r = client.post("/api/auto-import/commit", json={"batch_id": "r2", "items": items})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        db_session.expire_all()
        new_order = (
            db_session.query(Order)
            .filter(Order.platform == "xhs", Order.buyer_nickname == "AliceRedo2")
            .one()
        )
        assert new_order.external_order_id == "A123-redo2"

    def test_redo_only_for_same_platform(self, client, db_session, products):
        """xianyu 平台已有 A123-redo1 不应影响 xhs 的计数 — xhs override 仍从 redo1 起。"""
        db_session.add(Order(
            status="pending",
            platform="xianyu",
            external_order_id="A123",
            buyer_nickname="Old",
        ))
        db_session.add(Order(
            status="pending",
            platform="xianyu",
            external_order_id="A123-redo1",
            buyer_nickname="Old",
        ))
        # xhs 一条独立的 base — 触发 override 路径
        db_session.add(Order(
            status="pending",
            platform="xhs",
            external_order_id="A123",
            buyer_nickname="Old",
        ))
        db_session.commit()

        items = [{
            "external_order_id": "A123",
            "buyer_nickname": "AliceXhsRedo",
            "platform": "xhs",
            "override_duplicate": True,
            "products": [{"sku": "PR-0001", "quantity": 1}],
        }]
        r = client.post("/api/auto-import/commit", json={"batch_id": "r3", "items": items})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        db_session.expire_all()
        new_order = (
            db_session.query(Order)
            .filter(Order.platform == "xhs", Order.buyer_nickname == "AliceXhsRedo")
            .one()
        )
        # 跨平台不共享 redo 计数 → 仍是 redo1（不是 redo2）
        assert new_order.external_order_id == "A123-redo1"


# ============================================================
# Task 2.1: ADB client + diagnose + config
# ============================================================


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    cp = subprocess.CompletedProcess(args=["adb"], returncode=returncode)
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


class TestAdbClient:
    """Tests against the real AdbClient in services/adb_client.py — subprocess.run mocked."""

    def test_is_installed_ok(self, monkeypatch):
        monkeypatch.setattr(
            adb_client_mod.subprocess, "run",
            lambda *a, **k: _make_completed(0, "Android Debug Bridge version 1.0.41\n"),
        )
        assert RealAdbClient().is_installed() is True

    def test_is_installed_missing_binary(self, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError("adb not found")
        monkeypatch.setattr(adb_client_mod.subprocess, "run", _raise)
        assert RealAdbClient().is_installed() is False

    def test_connect_success(self, monkeypatch):
        monkeypatch.setattr(
            adb_client_mod.subprocess, "run",
            lambda *a, **k: _make_completed(0, "connected to 127.0.0.1:7555\n"),
        )
        ok, msg = RealAdbClient().connect("127.0.0.1:7555")
        assert ok is True
        assert "connected to" in msg

    def test_connect_refused(self, monkeypatch):
        monkeypatch.setattr(
            adb_client_mod.subprocess, "run",
            lambda *a, **k: _make_completed(1, "", "failed to connect: Connection refused\n"),
        )
        ok, msg = RealAdbClient().connect("127.0.0.1:7555")
        assert ok is False

    def test_list_devices_parses_rows(self, monkeypatch):
        stdout = (
            "List of devices attached\n"
            "127.0.0.1:7555  device product:MuMu model:MuMu transport_id:1\n"
            "emulator-5554  offline\n"
            "\n"
        )
        monkeypatch.setattr(
            adb_client_mod.subprocess, "run",
            lambda *a, **k: _make_completed(0, stdout),
        )
        devices = RealAdbClient().list_devices()
        assert len(devices) == 2
        assert devices[0].serial == "127.0.0.1:7555"
        assert devices[0].state == "device"

    def test_screencap_pipes_pull_then_reads(self, monkeypatch, tmp_path):
        calls: list[list[str]] = []
        dest = tmp_path / "shot.png"
        payload = b"\x89PNG\r\n\x1a\nFAKE"

        def fake_run(args, *a, **k):
            calls.append(list(args))
            if len(args) >= 4 and args[3] == "pull":
                dest.write_bytes(payload)
            return _make_completed(0, "")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        data = RealAdbClient().screencap("127.0.0.1:7555", str(dest))
        assert data == payload
        tokens = [t for c in calls for t in c]
        assert "screencap" in tokens
        assert "pull" in tokens


class TestDiagnoseAdb:
    """diagnose_adb returns 4 checks with {name, label, ok, hint, detail} shape."""

    def test_all_ok(self, monkeypatch):
        monkeypatch.setattr(RealAdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(RealAdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, "connected"))
        monkeypatch.setattr(
            RealAdbClient, "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="device")],
        )
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert len(diags) == 4
        assert all(d["ok"] for d in diags), diags

    def test_adb_missing(self, monkeypatch):
        monkeypatch.setattr(RealAdbClient, "is_installed", lambda self: False)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(RealAdbClient, "connect", lambda self, ep, timeout_s=5.0: (False, ""))
        monkeypatch.setattr(RealAdbClient, "list_devices", lambda self: [])

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[0]["ok"] is False
        assert diags[3]["ok"] is False

    def test_ping_fails(self, monkeypatch):
        monkeypatch.setattr(RealAdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: False)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(RealAdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, ""))
        monkeypatch.setattr(
            RealAdbClient, "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="device")],
        )

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[1]["ok"] is False

    def test_port_closed(self, monkeypatch):
        monkeypatch.setattr(RealAdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: False)
        monkeypatch.setattr(RealAdbClient, "connect", lambda self, ep, timeout_s=5.0: (False, ""))
        monkeypatch.setattr(RealAdbClient, "list_devices", lambda self: [])

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[2]["ok"] is False

    def test_device_offline(self, monkeypatch):
        monkeypatch.setattr(RealAdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(RealAdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, "connected"))
        monkeypatch.setattr(
            RealAdbClient, "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="offline")],
        )

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[3]["ok"] is False


class TestAdbConfig:
    """get_adb_config / set_adb_config persist via SystemConfig table."""

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


# ============================================================
# Task 2.2: LLM SKU match + 闲鱼 screenshot parse + SKU search
# ============================================================


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

    def test_schema_missing_orders_key_raises(self, monkeypatch):
        fake = _FakeProvider('{"unexpected":"shape"}')
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        with pytest.raises(LLMProviderError) as ei:
            parse_xianyu_screenshot(b"\x89PNG fake")
        assert ei.value.error_kind == "schema_invalid"


def _seed_product(db, sku: str, name: str, colors: list[str] | None = None):
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
    """search_skus returns [{sku_code, sku_name, score}] (router-aligned shape)."""

    def test_chinese_substring_match(self, db_session):
        _seed_product(db_session, "PR-0001", "床头柜", ["白色", "黑色"])
        _seed_product(db_session, "PR-0002", "餐桌椅", [])
        hits = search_skus(db_session, "床头")
        assert len(hits) == 1
        assert hits[0]["sku_code"] == "PR-0001"
        assert hits[0]["sku_name"] == "床头柜"
        assert hits[0]["score"] == 0.8  # name match → 0.8

    def test_sku_code_exact_match(self, db_session):
        _seed_product(db_session, "PR-0001", "床头柜")
        _seed_product(db_session, "PR-0002", "餐桌椅")
        hits = search_skus(db_session, "PR-0002")
        assert len(hits) == 1
        assert hits[0]["sku_code"] == "PR-0002"
        assert hits[0]["score"] == 1.0  # sku code match → 1.0

    def test_empty_query_returns_empty(self, db_session):
        _seed_product(db_session, "PR-0001", "床头柜")
        assert search_skus(db_session, "") == []
        assert search_skus(db_session, "   ") == []

    def test_limit_caps_results(self, db_session):
        for i in range(5):
            _seed_product(db_session, f"PR-000{i + 1}", f"产品{i}")
        hits = search_skus(db_session, "产品", limit=3)
        assert len(hits) == 3
