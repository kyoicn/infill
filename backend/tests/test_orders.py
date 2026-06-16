"""v0.3.1 测试：Order.notes + PUT /api/orders/{id} + load_catalog 名称兜底。

覆盖：
- PUT 改 notes / items / 校验 product_id / 不动 created_at / shipped 仍可编辑
- load_catalog 对 sku=NULL 旧行的 name fallback 保留原 id（用于 FK 修复）
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Component, Order, OrderItem, PrintConfig, Product
from app.services import catalog as catalog_module
from app.services.catalog import load_catalog


# ---------- fixtures ----------

@pytest.fixture()
def db_session(tmp_path, monkeypatch):
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
def client(db_session):
    return TestClient(app)


@pytest.fixture()
def products(db_session):
    """造两个产品 + 一个未引用的产品，方便测试。"""
    p1 = Product(sku="PR-0001", name="产品1", description="")
    p2 = Product(sku="PR-0002", name="产品2", description="")
    p3 = Product(sku="PR-0003", name="产品3", description="")
    db_session.add_all([p1, p2, p3])
    db_session.commit()
    return [p1, p2, p3]


def _make_order(db, items: list[tuple[int, int]], notes: str = "", status: str = "pending"):
    """直接在 DB 建一个 order，绕过 API。items=[(product_id, qty), ...]"""
    order = Order(notes=notes, status=status)
    db.add(order)
    db.flush()
    for pid, qty in items:
        db.add(OrderItem(order_id=order.id, product_id=pid, quantity=qty))
    db.commit()
    db.refresh(order)
    return order


# ---------- PUT /api/orders/{id} ----------

class TestUpdateOrder:
    def test_update_order_changes_notes(self, client, db_session, products):
        order = _make_order(db_session, [(products[0].id, 2)], notes="old")

        r = client.put(f"/api/orders/{order.id}", json={"notes": "新备注"})
        assert r.status_code == 200, r.text
        assert r.json()["notes"] == "新备注"

        # GET 也能看到
        got = client.get(f"/api/orders/{order.id}")
        assert got.status_code == 200
        assert got.json()["notes"] == "新备注"

    def test_update_order_replaces_items(self, client, db_session, products):
        order = _make_order(db_session, [(products[0].id, 2), (products[1].id, 3)])
        assert len(order.items) == 2

        r = client.put(
            f"/api/orders/{order.id}",
            json={"items": [{"product_id": products[2].id, "quantity": 5}]},
        )
        assert r.status_code == 200, r.text

        # DB 状态：旧 items 没了，只剩新的一条
        db_session.expire_all()
        remaining = db_session.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        assert len(remaining) == 1
        assert remaining[0].product_id == products[2].id
        assert remaining[0].quantity == 5
        # 旧的 product_id 都不在
        product_ids_left = {it.product_id for it in remaining}
        assert products[0].id not in product_ids_left
        assert products[1].id not in product_ids_left

    def test_update_order_rejects_invalid_product_id(self, client, db_session, products):
        order = _make_order(db_session, [(products[0].id, 1)])

        r = client.put(
            f"/api/orders/{order.id}",
            json={"items": [{"product_id": 99999, "quantity": 1}]},
        )
        assert r.status_code == 400
        assert "99999" in r.json()["detail"]

        # 原 items 未受影响（事务回滚由 commit 前 raise 保证）
        db_session.expire_all()
        items = db_session.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        assert len(items) == 1
        assert items[0].product_id == products[0].id

    def test_update_order_does_not_touch_created_at(self, client, db_session, products):
        # 故意设一个明显的旧 created_at
        original_created = datetime(2024, 1, 1, 12, 0, 0)
        order = Order(created_at=original_created, notes="")
        db_session.add(order)
        db_session.flush()
        db_session.add(OrderItem(order_id=order.id, product_id=products[0].id, quantity=1))
        db_session.commit()
        order_id = order.id

        r = client.put(
            f"/api/orders/{order_id}",
            json={"notes": "改个备注", "items": [{"product_id": products[1].id, "quantity": 9}]},
        )
        assert r.status_code == 200

        db_session.expire_all()
        fresh = db_session.get(Order, order_id)
        assert fresh.created_at == original_created

    def test_update_order_allows_shipped(self, client, db_session, products):
        """shipped 订单也能编辑（用户用此机制修复 v0.3.0 留下的孤儿 FK）。"""
        shipped_at = datetime(2025, 6, 1, 10, 0, 0)
        order = Order(status="shipped", shipped_at=shipped_at, notes="")
        db_session.add(order)
        db_session.flush()
        db_session.add(OrderItem(order_id=order.id, product_id=products[0].id, quantity=1))
        db_session.commit()
        order_id = order.id

        r = client.put(
            f"/api/orders/{order_id}",
            json={"items": [{"product_id": products[1].id, "quantity": 2}]},
        )
        assert r.status_code == 200

        db_session.expire_all()
        fresh = db_session.get(Order, order_id)
        # status / shipped_at 不变
        assert fresh.status == "shipped"
        assert fresh.shipped_at == shipped_at
        # items 已被替换
        assert len(fresh.items) == 1
        assert fresh.items[0].product_id == products[1].id

    def test_update_order_404_when_missing(self, client, db_session):
        r = client.put("/api/orders/999999", json={"notes": "x"})
        assert r.status_code == 404

    def test_update_order_notes_only_does_not_touch_items(self, client, db_session, products):
        """PUT 只传 notes 时，items 字段保持不变。"""
        order = _make_order(db_session, [(products[0].id, 7), (products[1].id, 4)])
        original_item_ids = sorted(it.id for it in order.items)

        r = client.put(f"/api/orders/{order.id}", json={"notes": "仅改备注"})
        assert r.status_code == 200

        db_session.expire_all()
        items = db_session.query(OrderItem).filter(OrderItem.order_id == order.id).order_by(OrderItem.id).all()
        assert [it.id for it in items] == original_item_ids
        # 数量也没变
        assert items[0].quantity == 7
        assert items[1].quantity == 4

    def test_update_order_empty_items_clears_all(self, client, db_session, products):
        """PUT items=[] 视为「替换为空集」。"""
        order = _make_order(db_session, [(products[0].id, 1)])

        r = client.put(f"/api/orders/{order.id}", json={"items": []})
        assert r.status_code == 200

        db_session.expire_all()
        remaining = db_session.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        assert remaining == []

    def test_create_order_persists_notes(self, client, db_session, products):
        """POST 带 notes 也要持久化（顺带覆盖 schema 改动）。"""
        r = client.post(
            "/api/orders",
            json={
                "notes": "首发备注",
                "items": [{"product_id": products[0].id, "quantity": 1}],
            },
        )
        assert r.status_code == 200, r.text
        oid = r.json()["id"]

        got = client.get(f"/api/orders/{oid}")
        assert got.json()["notes"] == "首发备注"


# ---------- load_catalog name fallback ----------

class TestLoadCatalogNameFallback:
    """模拟 v0.3.0 broken-style DB：表升级前是无 sku 列的 v0.2 schema，
    `ensure_sku_column_exists` 跑过后 sku 列存在但全 NULL。
    name fallback 必须在 load_catalog 里把这些行接回来（保留 id，赋值 sku）。
    """

    def _build_old_v02_engine(self):
        """造一个 v0.2 风格的 DB（无 sku 列），然后 ALTER TABLE 加 nullable sku 列。
        模拟 v0.3.0 升级路径下的真实 DB 状态。
        """
        from app.services.catalog import ensure_sku_column_exists
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE components (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    colors TEXT NOT NULL DEFAULT '[]'
                )
            """))
            conn.execute(text("""
                CREATE TABLE print_configs (
                    id INTEGER PRIMARY KEY,
                    plate_name VARCHAR NOT NULL,
                    component_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    duration_minutes INTEGER NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT ''
                )
            """))
            conn.execute(text("""
                CREATE TABLE product_components (
                    id INTEGER PRIMARY KEY,
                    product_id INTEGER NOT NULL,
                    component_id INTEGER NOT NULL,
                    color VARCHAR DEFAULT '' NOT NULL,
                    quantity INTEGER NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE inventory (
                    id INTEGER PRIMARY KEY,
                    component_id INTEGER NOT NULL,
                    color VARCHAR DEFAULT '' NOT NULL,
                    quantity INTEGER DEFAULT 0 NOT NULL
                )
            """))
            conn.commit()
        # ALTER TABLE 加 nullable sku 列（对应 v0.3.0 升级时 ensure_sku_column_exists）
        ensure_sku_column_exists(engine)
        return engine

    def _session_for(self, engine):
        TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        return TestSession()

    def _write_catalog(self, tmp_path, monkeypatch, body: str):
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(body, encoding="utf-8")
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog)
        return catalog

    def test_load_catalog_name_fallback_preserves_ids(self, tmp_path, monkeypatch):
        """Component 旧行 sku=NULL，按 name 兜底必须复用原 id。"""
        engine = self._build_old_v02_engine()
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO components (id, name, description, colors, sku) "
                "VALUES (4, '床头柜-侧板', '', '[\"白色\"]', NULL)"
            ))
            conn.commit()

        session = self._session_for(engine)

        yaml_body = """
组件:
  - 编号: C-0001
    名称: 床头柜-侧板
    描述: 升级后的描述
    可选颜色: [白色, 红色]
打印盘: []
产品: []
"""
        self._write_catalog(tmp_path, monkeypatch, yaml_body)

        stats = load_catalog(session)
        assert stats["组件"] == 1

        session.expire_all()
        comp = session.query(Component).filter(Component.name == "床头柜-侧板").one()
        assert comp.id == 4, "id 必须保留，否则外键悬空"
        assert comp.sku == "C-0001"
        assert comp.description == "升级后的描述"
        assert "红色" in comp.colors
        session.close()

    def test_load_catalog_name_fallback_print_config(self, tmp_path, monkeypatch):
        """PrintConfig 旧行 sku=NULL，按 plate_name 兜底必须复用原 id。"""
        engine = self._build_old_v02_engine()
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO components (id, name, description, colors, sku) "
                "VALUES (7, '组件A', '', '[\"\"]', NULL)"
            ))
            conn.execute(text(
                "INSERT INTO print_configs (id, plate_name, component_id, quantity, duration_minutes, sku) "
                "VALUES (9, '盘A', 7, 5, 100, NULL)"
            ))
            conn.commit()

        session = self._session_for(engine)

        yaml_body = """
组件:
  - 编号: C-0001
    名称: 组件A
    可选颜色: [""]
打印盘:
  - 编号: P-0001
    盘号: 盘A
    组件编号: C-0001
    数量: 10
    耗时分钟: 200
产品: []
"""
        self._write_catalog(tmp_path, monkeypatch, yaml_body)
        load_catalog(session)
        session.expire_all()

        cfg = session.query(PrintConfig).filter(PrintConfig.plate_name == "盘A").one()
        assert cfg.id == 9, "PrintConfig id 必须保留"
        assert cfg.sku == "P-0001"
        assert cfg.quantity == 10
        assert cfg.duration_minutes == 200
        session.close()

    def test_load_catalog_name_fallback_product(self, tmp_path, monkeypatch):
        """Product 旧行 sku=NULL，按 name 兜底必须复用原 id。"""
        engine = self._build_old_v02_engine()
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO components (id, name, description, colors, sku) "
                "VALUES (3, '组件A', '', '[\"\"]', NULL)"
            ))
            conn.execute(text(
                "INSERT INTO products (id, name, description, sku) "
                "VALUES (8, '产品X', '旧描述', NULL)"
            ))
            conn.commit()

        session = self._session_for(engine)

        yaml_body = """
组件:
  - 编号: C-0001
    名称: 组件A
    可选颜色: [""]
打印盘: []
产品:
  - 编号: PR-0001
    名称: 产品X
    描述: 新描述
    BOM:
      - 组件编号: C-0001
        数量: 1
"""
        self._write_catalog(tmp_path, monkeypatch, yaml_body)
        load_catalog(session)
        session.expire_all()

        p = session.query(Product).filter(Product.name == "产品X").one()
        assert p.id == 8, "Product id 必须保留（OrderItem.product_id 才不会悬空）"
        assert p.sku == "PR-0001"
        assert p.description == "新描述"
        session.close()


# ---------- ensure_order_notes_column_exists ----------

class TestEnsureOrderNotesColumnExists:
    def test_clean_orm_db_is_noop(self):
        """ORM 建的表已经有 notes 列 → 不应再 ALTER。"""
        from sqlalchemy import inspect as sa_inspect
        from app.services.catalog import ensure_order_notes_column_exists

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        assert ensure_order_notes_column_exists(engine) is False
        cols = {c["name"] for c in sa_inspect(engine).get_columns("orders")}
        assert "notes" in cols

    def test_old_schema_db_gets_notes_added(self):
        from sqlalchemy import inspect as sa_inspect, text
        from app.services.catalog import ensure_order_notes_column_exists

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # 手工建无 notes 列的旧 orders 表
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    shipped_at TEXT
                )
            """))
            conn.commit()

        assert ensure_order_notes_column_exists(engine) is True
        cols = {c["name"] for c in sa_inspect(engine).get_columns("orders")}
        assert "notes" in cols

        # 幂等
        assert ensure_order_notes_column_exists(engine) is False
