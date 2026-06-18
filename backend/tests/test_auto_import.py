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

from app.database import Base, get_db
from app.main import app
from app.models import Order, OrderItem, Product
from app.services import auto_import_llm
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
