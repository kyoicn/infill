"""目录 CRUD（catalog_edit）后端测试

覆盖：
- 9 个 service 函数 happy path（每个：DB 同步 + catalog.yaml 已更新 + 返回 ok=True+stats）
- name_conflict / component_in_use / product_in_use / component_not_found / invalid_input
- update / delete 缺失对象的 not_found
- BOM 完整性校验（空 BOM、组件不存在）
- rollback：mock load_catalog 抛异常 → catalog.yaml 字节回到事务前
- 9 个 HTTP 端点 happy path（通过 TestClient + dependency_overrides）

测试用 in-memory SQLite + tmp_path catalog.yaml，不污染真实 data/catalog.yaml。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Component, Order, OrderItem, PrintConfig, Product
from app.schemas_catalog_edit import (
    BOMItem,
    ComponentCreate,
    ComponentUpdate,
    PlateCreate,
    PlateUpdate,
    ProductCreate,
    ProductUpdate,
)
from app.services import catalog as catalog_module
from app.services import catalog_edit


# ---------- 通用 fixture ----------

def _seed_catalog(path: Path) -> None:
    """写入一个最小可用的 catalog.yaml（2 组件 / 2 盘 / 1 产品）。"""
    initial = {
        "组件": [
            {"名称": "组件A", "描述": "底座", "可选颜色": ["白色", "红色"]},
            {"名称": "组件B", "可选颜色": ["黑色"]},
        ],
        "打印盘": [
            {"盘号": "1号盘", "组件": "组件A", "数量": 10, "耗时分钟": 120},
            {"盘号": "2号盘", "组件": "组件B", "数量": 8, "耗时分钟": 90},
        ],
        "产品": [
            {
                "名称": "产品A",
                "描述": "示例",
                "BOM": [
                    {"组件": "组件A", "颜色": "白色", "数量": 1},
                    {"组件": "组件B", "颜色": "黑色", "数量": 2},
                ],
            },
        ],
    }
    path.write_text(
        yaml.safe_dump(initial, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture()
def catalog_tmp(tmp_path, monkeypatch) -> Path:
    """临时 catalog.yaml + monkeypatch services.catalog.CATALOG_PATH。"""
    catalog = tmp_path / "catalog.yaml"
    _seed_catalog(catalog)
    monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog)
    return catalog


@pytest.fixture()
def db_session(catalog_tmp):
    """in-memory SQLite + load_catalog 预填，每个测试独立。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()

    # 预先 load 一次，让 DB 与 catalog_tmp 同步
    catalog_module.load_catalog(session)

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
def client(db_session) -> TestClient:
    return TestClient(app)


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------- 组件 CRUD ----------

class TestAddComponent:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.add_component(
            db_session,
            ComponentCreate(name="组件C", description="装饰", colors=["金色"]),
        )
        assert result["ok"] is True, result
        assert "stats" in result

        data = _load_yaml(catalog_tmp)
        names = [c["名称"] for c in data["组件"]]
        assert "组件C" in names
        # DB 同步
        assert db_session.query(Component).filter(Component.name == "组件C").first() is not None

    def test_name_conflict(self, db_session, catalog_tmp):
        original = catalog_tmp.read_bytes()
        result = catalog_edit.add_component(
            db_session, ComponentCreate(name="组件A")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "name_conflict"
        # 文件未动
        assert catalog_tmp.read_bytes() == original

    def test_empty_name_rejected(self, db_session, catalog_tmp):
        result = catalog_edit.add_component(
            db_session, ComponentCreate(name="   ")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "invalid_input"

    def test_chinese_preserved_in_yaml(self, db_session, catalog_tmp):
        catalog_edit.add_component(
            db_session, ComponentCreate(name="新组件X", colors=["靛蓝"])
        )
        raw = catalog_tmp.read_text(encoding="utf-8")
        assert "新组件X" in raw
        assert "靛蓝" in raw
        assert "\\u" not in raw


class TestUpdateComponent:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.update_component(
            db_session,
            "组件A",
            ComponentUpdate(description="新描述", colors=["蓝色"]),
        )
        assert result["ok"] is True, result

        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["名称"] == "组件A")
        assert comp["描述"] == "新描述"
        assert comp["可选颜色"] == ["蓝色"]

    def test_partial_update_keeps_other_fields(self, db_session, catalog_tmp):
        # 只改 description，colors 保留原值
        result = catalog_edit.update_component(
            db_session, "组件A", ComponentUpdate(description="改描述")
        )
        assert result["ok"] is True
        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["名称"] == "组件A")
        assert comp["描述"] == "改描述"
        assert comp["可选颜色"] == ["白色", "红色"]  # 未被清除

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_component(
            db_session, "不存在", ComponentUpdate(description="x")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"


class TestDeleteComponent:
    def test_in_use_by_plate(self, db_session, catalog_tmp):
        original = catalog_tmp.read_bytes()
        result = catalog_edit.delete_component(db_session, "组件A")
        assert result["ok"] is False
        assert result["error_kind"] == "component_in_use"
        # 文件未动
        assert catalog_tmp.read_bytes() == original

    def test_in_use_by_product_bom(self, db_session, catalog_tmp):
        # 先解除打印盘引用：删 1号盘 / 2号盘 都用到 A/B；新加一个孤立组件，让产品独自引用它
        catalog_edit.add_component(
            db_session, ComponentCreate(name="孤立组件")
        )
        # 给产品 A 的 BOM 加一项引用「孤立组件」
        catalog_edit.update_product(
            db_session,
            "产品A",
            ProductUpdate(bom=[
                BOMItem(component_name="组件A", color="白色", quantity=1),
                BOMItem(component_name="孤立组件", quantity=1),
            ]),
        )
        result = catalog_edit.delete_component(db_session, "孤立组件")
        assert result["ok"] is False
        assert result["error_kind"] == "component_in_use"

    def test_happy_path_no_refs(self, db_session, catalog_tmp):
        catalog_edit.add_component(
            db_session, ComponentCreate(name="独立组件")
        )
        result = catalog_edit.delete_component(db_session, "独立组件")
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        names = [c["名称"] for c in data["组件"]]
        assert "独立组件" not in names

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.delete_component(db_session, "不存在")
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"


# ---------- 打印盘 CRUD ----------

class TestAddPlate:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.add_plate(
            db_session,
            PlateCreate(
                plate_name="3号盘",
                component_name="组件A",
                quantity=5,
                duration_minutes=60,
            ),
        )
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["盘号"] == "3号盘")
        assert plate["数量"] == 5
        assert plate["耗时分钟"] == 60
        # DB 同步
        cfg = db_session.query(PrintConfig).filter(PrintConfig.plate_name == "3号盘").first()
        assert cfg is not None
        assert cfg.quantity == 5

    def test_name_conflict(self, db_session, catalog_tmp):
        result = catalog_edit.add_plate(
            db_session,
            PlateCreate(
                plate_name="1号盘",
                component_name="组件A",
                quantity=1,
                duration_minutes=1,
            ),
        )
        assert result["ok"] is False
        assert result["error_kind"] == "name_conflict"

    def test_component_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.add_plate(
            db_session,
            PlateCreate(
                plate_name="X",
                component_name="不存在组件",
                quantity=1,
                duration_minutes=1,
            ),
        )
        assert result["ok"] is False
        assert result["error_kind"] == "component_not_found"

    def test_invalid_quantity(self, db_session, catalog_tmp):
        result = catalog_edit.add_plate(
            db_session,
            PlateCreate(
                plate_name="X",
                component_name="组件A",
                quantity=0,
                duration_minutes=1,
            ),
        )
        assert result["ok"] is False
        assert result["error_kind"] == "invalid_input"


class TestUpdatePlate:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.update_plate(
            db_session,
            "1号盘",
            PlateUpdate(quantity=20, duration_minutes=200),
        )
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["盘号"] == "1号盘")
        assert plate["数量"] == 20
        assert plate["耗时分钟"] == 200
        # 原组件保留
        assert plate["组件"] == "组件A"

    def test_change_component(self, db_session, catalog_tmp):
        result = catalog_edit.update_plate(
            db_session, "1号盘", PlateUpdate(component_name="组件B")
        )
        assert result["ok"] is True
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["盘号"] == "1号盘")
        assert plate["组件"] == "组件B"

    def test_component_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_plate(
            db_session, "1号盘", PlateUpdate(component_name="不存在")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "component_not_found"

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_plate(
            db_session, "不存在盘", PlateUpdate(quantity=1)
        )
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"


class TestDeletePlate:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.delete_plate(db_session, "1号盘")
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        names = [p["盘号"] for p in data["打印盘"]]
        assert "1号盘" not in names
        # DB 同步
        assert db_session.query(PrintConfig).filter(PrintConfig.plate_name == "1号盘").first() is None

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.delete_plate(db_session, "不存在盘")
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"


# ---------- 产品 CRUD ----------

class TestAddProduct:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.add_product(
            db_session,
            ProductCreate(
                name="产品B",
                description="新款",
                bom=[
                    BOMItem(component_name="组件A", color="红色", quantity=2),
                    BOMItem(component_name="组件B", color="黑色", quantity=1),
                ],
            ),
        )
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        prod = next(p for p in data["产品"] if p["名称"] == "产品B")
        assert len(prod["BOM"]) == 2
        # DB 同步
        assert db_session.query(Product).filter(Product.name == "产品B").first() is not None

    def test_name_conflict(self, db_session, catalog_tmp):
        result = catalog_edit.add_product(
            db_session,
            ProductCreate(
                name="产品A",
                bom=[BOMItem(component_name="组件A", quantity=1)],
            ),
        )
        assert result["ok"] is False
        assert result["error_kind"] == "name_conflict"

    def test_empty_bom_rejected(self, db_session, catalog_tmp):
        result = catalog_edit.add_product(
            db_session, ProductCreate(name="新产品", bom=[])
        )
        assert result["ok"] is False
        assert result["error_kind"] == "invalid_input"

    def test_bom_component_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.add_product(
            db_session,
            ProductCreate(
                name="新产品",
                bom=[BOMItem(component_name="不存在组件", quantity=1)],
            ),
        )
        assert result["ok"] is False
        assert result["error_kind"] == "component_not_found"


class TestUpdateProduct:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.update_product(
            db_session,
            "产品A",
            ProductUpdate(
                description="改了描述",
                bom=[BOMItem(component_name="组件A", color="红色", quantity=3)],
            ),
        )
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        prod = next(p for p in data["产品"] if p["名称"] == "产品A")
        assert prod["描述"] == "改了描述"
        assert len(prod["BOM"]) == 1
        assert prod["BOM"][0]["数量"] == 3

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_product(
            db_session, "不存在", ProductUpdate(description="x")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"

    def test_empty_bom_rejected(self, db_session, catalog_tmp):
        result = catalog_edit.update_product(
            db_session, "产品A", ProductUpdate(bom=[])
        )
        assert result["ok"] is False
        assert result["error_kind"] == "invalid_input"


class TestDeleteProduct:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.delete_product(db_session, "产品A")
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        names = [p["名称"] for p in data["产品"]]
        assert "产品A" not in names

    def test_product_in_use_by_pending_order(self, db_session, catalog_tmp):
        # 造 pending 订单关联产品A
        prod = db_session.query(Product).filter(Product.name == "产品A").first()
        order = Order(status="pending")
        db_session.add(order)
        db_session.flush()
        db_session.add(OrderItem(order_id=order.id, product_id=prod.id, quantity=1))
        db_session.commit()

        original = catalog_tmp.read_bytes()
        result = catalog_edit.delete_product(db_session, "产品A")
        assert result["ok"] is False
        assert result["error_kind"] == "product_in_use"
        # 文件未动
        assert catalog_tmp.read_bytes() == original

    def test_shipped_order_does_not_block_delete(self, db_session, catalog_tmp):
        # shipped 订单不应阻塞删除
        prod = db_session.query(Product).filter(Product.name == "产品A").first()
        order = Order(status="shipped")
        db_session.add(order)
        db_session.flush()
        db_session.add(OrderItem(order_id=order.id, product_id=prod.id, quantity=1))
        db_session.commit()

        result = catalog_edit.delete_product(db_session, "产品A")
        assert result["ok"] is True, result

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.delete_product(db_session, "不存在")
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"


# ---------- rollback ----------

class TestRollbackOnLoadFailure:
    def test_load_failure_rolls_back_file(self, db_session, catalog_tmp, monkeypatch):
        """load_catalog 抛错 → catalog.yaml 字节回到事务前。"""
        original_bytes = catalog_tmp.read_bytes()

        calls = {"n": 0}

        def fake_load_catalog(session):
            calls["n"] += 1
            # 第一次（业务调用）抛；第二次（rollback 后的 best-effort 恢复）放行
            if calls["n"] == 1:
                raise ValueError("simulated reload failure")
            return {"组件": 0, "打印盘": 0, "产品": 0}

        monkeypatch.setattr(catalog_edit, "load_catalog", fake_load_catalog)

        result = catalog_edit.add_component(
            db_session, ComponentCreate(name="新组件Z")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "load_failed"
        assert result["rolled_back"] is True
        # 文件字节级 == 原始
        assert catalog_tmp.read_bytes() == original_bytes


# ---------- HTTP 端点 happy path ----------

class TestEndpoints:
    def test_create_component_endpoint(self, client, catalog_tmp):
        r = client.post(
            "/api/catalog/components",
            json={"name": "EP组件", "colors": ["紫色"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        assert "EP组件" in [c["名称"] for c in _load_yaml(catalog_tmp)["组件"]]

    def test_create_component_with_chinese_alias(self, client, catalog_tmp):
        # 中文 alias 也能解析（populate_by_name=True + alias）
        r = client.post(
            "/api/catalog/components",
            json={"名称": "中文别名组件", "可选颜色": ["金色"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        assert "中文别名组件" in [c["名称"] for c in _load_yaml(catalog_tmp)["组件"]]

    def test_update_component_endpoint(self, client, catalog_tmp):
        r = client.put(
            "/api/catalog/components/组件A",
            json={"description": "更新描述"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["名称"] == "组件A")
        assert comp["描述"] == "更新描述"

    def test_delete_component_endpoint(self, client, db_session, catalog_tmp):
        # 先新建一个无引用的组件再删
        client.post("/api/catalog/components", json={"name": "待删组件"})
        r = client.delete("/api/catalog/components/待删组件")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

    def test_create_plate_endpoint(self, client, catalog_tmp):
        r = client.post(
            "/api/catalog/plates",
            json={
                "plate_name": "EP盘",
                "component_name": "组件A",
                "quantity": 3,
                "duration_minutes": 45,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

    def test_update_plate_endpoint(self, client, catalog_tmp):
        r = client.put(
            "/api/catalog/plates/1号盘",
            json={"quantity": 15},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["盘号"] == "1号盘")
        assert plate["数量"] == 15

    def test_delete_plate_endpoint(self, client, catalog_tmp):
        r = client.delete("/api/catalog/plates/2号盘")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

    def test_create_product_endpoint(self, client, catalog_tmp):
        r = client.post(
            "/api/catalog/products",
            json={
                "name": "EP产品",
                "description": "端到端测试",
                "bom": [
                    {"component_name": "组件A", "color": "白色", "quantity": 1},
                ],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

    def test_update_product_endpoint(self, client, catalog_tmp):
        r = client.put(
            "/api/catalog/products/产品A",
            json={"description": "通过 API 更新"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        data = _load_yaml(catalog_tmp)
        prod = next(p for p in data["产品"] if p["名称"] == "产品A")
        assert prod["描述"] == "通过 API 更新"

    def test_delete_product_endpoint(self, client, catalog_tmp):
        r = client.delete("/api/catalog/products/产品A")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

    def test_component_in_use_endpoint_returns_error_body(self, client, catalog_tmp):
        r = client.delete("/api/catalog/components/组件A")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error_kind"] == "component_in_use"
