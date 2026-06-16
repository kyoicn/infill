"""目录 CRUD（catalog_edit）后端测试 — v0.3.0 SKU-keyed

覆盖：
- 9 个 service 函数 happy path（DB 同步 + catalog.yaml 已更新 + 返回 ok=True+stats+new_sku）
- component_in_use / product_in_use / component_not_found / invalid_input
- update / delete 缺失 SKU 的 not_found
- BOM 完整性校验（空 BOM、组件不存在）
- rollback：mock load_catalog 抛异常 → catalog.yaml 字节回到事务前
- rename 测试：改 name / plate_name 不影响 id / 关联引用
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
    """写入一个最小可用的 catalog.yaml（2 组件 / 2 盘 / 1 产品，已是 SKU-keyed 格式）。"""
    initial = {
        "组件": [
            {"编号": "C-0001", "名称": "组件A", "描述": "底座", "可选颜色": ["白色", "红色"]},
            {"编号": "C-0002", "名称": "组件B", "可选颜色": ["黑色"]},
        ],
        "打印盘": [
            {"编号": "P-0001", "盘号": "1号盘", "组件编号": "C-0001", "数量": 10, "耗时分钟": 120},
            {"编号": "P-0002", "盘号": "2号盘", "组件编号": "C-0002", "数量": 8, "耗时分钟": 90},
        ],
        "产品": [
            {
                "编号": "PR-0001",
                "名称": "产品A",
                "描述": "示例",
                "BOM": [
                    {"组件编号": "C-0001", "颜色": "白色", "数量": 1},
                    {"组件编号": "C-0002", "颜色": "黑色", "数量": 2},
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
        # 后端自动生成 SKU；从 C-0003 起步（已用 C-0001/C-0002）
        assert result["new_sku"] == "C-0003"

        data = _load_yaml(catalog_tmp)
        skus = [c["编号"] for c in data["组件"]]
        assert "C-0003" in skus
        # 新条目 name 落 yaml
        new_entry = next(c for c in data["组件"] if c["编号"] == "C-0003")
        assert new_entry["名称"] == "组件C"
        # DB 同步：按 SKU 查
        comp = db_session.query(Component).filter(Component.sku == "C-0003").first()
        assert comp is not None
        assert comp.name == "组件C"

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

    def test_sku_auto_increments(self, db_session, catalog_tmp):
        r1 = catalog_edit.add_component(db_session, ComponentCreate(name="X"))
        r2 = catalog_edit.add_component(db_session, ComponentCreate(name="Y"))
        assert r1["new_sku"] == "C-0003"
        assert r2["new_sku"] == "C-0004"


class TestUpdateComponent:
    def test_happy_path_description_and_colors(self, db_session, catalog_tmp):
        result = catalog_edit.update_component(
            db_session,
            "C-0001",
            ComponentUpdate(description="新描述", colors=["蓝色"]),
        )
        assert result["ok"] is True, result

        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["编号"] == "C-0001")
        assert comp["描述"] == "新描述"
        assert comp["可选颜色"] == ["蓝色"]
        # 名称未动
        assert comp["名称"] == "组件A"

    def test_rename_component_preserves_id_and_refs(self, db_session, catalog_tmp):
        """改 name → DB Component.name 改、id 不变、关联的 plate / product BOM 不动。"""
        comp_before = db_session.query(Component).filter(Component.sku == "C-0001").first()
        old_id = comp_before.id

        result = catalog_edit.update_component(
            db_session, "C-0001", ComponentUpdate(name="组件A-改名")
        )
        assert result["ok"] is True, result

        # DB 端：name 已改，id 没变
        comp_after = db_session.query(Component).filter(Component.sku == "C-0001").first()
        assert comp_after.id == old_id
        assert comp_after.name == "组件A-改名"

        # YAML 端：name 改
        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["编号"] == "C-0001")
        assert comp["名称"] == "组件A-改名"

        # 引用未受影响：plate 的"组件编号"仍 C-0001、产品 BOM 仍 C-0001
        plate = next(p for p in data["打印盘"] if p["编号"] == "P-0001")
        assert plate["组件编号"] == "C-0001"
        prod = next(p for p in data["产品"] if p["编号"] == "PR-0001")
        assert prod["BOM"][0]["组件编号"] == "C-0001"

    def test_partial_update_keeps_other_fields(self, db_session, catalog_tmp):
        result = catalog_edit.update_component(
            db_session, "C-0001", ComponentUpdate(description="改描述")
        )
        assert result["ok"] is True
        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["编号"] == "C-0001")
        assert comp["描述"] == "改描述"
        assert comp["可选颜色"] == ["白色", "红色"]
        assert comp["名称"] == "组件A"

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_component(
            db_session, "C-9999", ComponentUpdate(description="x")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"

    def test_rename_to_empty_rejected(self, db_session, catalog_tmp):
        result = catalog_edit.update_component(
            db_session, "C-0001", ComponentUpdate(name="   ")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "invalid_input"


class TestDeleteComponent:
    def test_in_use_by_plate(self, db_session, catalog_tmp):
        original = catalog_tmp.read_bytes()
        result = catalog_edit.delete_component(db_session, "C-0001")
        assert result["ok"] is False
        assert result["error_kind"] == "component_in_use"
        assert catalog_tmp.read_bytes() == original

    def test_in_use_by_product_bom(self, db_session, catalog_tmp):
        r = catalog_edit.add_component(db_session, ComponentCreate(name="孤立组件"))
        new_sku = r["new_sku"]
        # 给产品 A 的 BOM 加一项引用「孤立组件」
        catalog_edit.update_product(
            db_session,
            "PR-0001",
            ProductUpdate(bom=[
                BOMItem(component_sku="C-0001", color="白色", quantity=1),
                BOMItem(component_sku=new_sku, quantity=1),
            ]),
        )
        result = catalog_edit.delete_component(db_session, new_sku)
        assert result["ok"] is False
        assert result["error_kind"] == "component_in_use"

    def test_happy_path_no_refs(self, db_session, catalog_tmp):
        r = catalog_edit.add_component(db_session, ComponentCreate(name="独立组件"))
        new_sku = r["new_sku"]
        result = catalog_edit.delete_component(db_session, new_sku)
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        skus = [c["编号"] for c in data["组件"]]
        assert new_sku not in skus

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.delete_component(db_session, "C-9999")
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"


# ---------- 打印盘 CRUD ----------

class TestAddPlate:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.add_plate(
            db_session,
            PlateCreate(
                plate_name="3号盘",
                component_sku="C-0001",
                quantity=5,
                duration_minutes=60,
            ),
        )
        assert result["ok"] is True, result
        assert result["new_sku"] == "P-0003"
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["编号"] == "P-0003")
        assert plate["数量"] == 5
        assert plate["耗时分钟"] == 60
        # DB 同步
        cfg = db_session.query(PrintConfig).filter(PrintConfig.sku == "P-0003").first()
        assert cfg is not None
        assert cfg.quantity == 5

    def test_component_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.add_plate(
            db_session,
            PlateCreate(
                plate_name="X",
                component_sku="C-9999",
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
                component_sku="C-0001",
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
            "P-0001",
            PlateUpdate(quantity=20, duration_minutes=200),
        )
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["编号"] == "P-0001")
        assert plate["数量"] == 20
        assert plate["耗时分钟"] == 200
        # 组件编号保留
        assert plate["组件编号"] == "C-0001"
        # 盘号未动
        assert plate["盘号"] == "1号盘"

    def test_rename_plate_preserves_id_and_refs(self, db_session, catalog_tmp):
        """改 plate_name → DB PrintConfig.plate_name 改、id / 组件引用不动。"""
        cfg_before = db_session.query(PrintConfig).filter(PrintConfig.sku == "P-0001").first()
        old_id = cfg_before.id
        old_comp = cfg_before.component_id

        result = catalog_edit.update_plate(
            db_session, "P-0001", PlateUpdate(plate_name="改盘号-1")
        )
        assert result["ok"] is True

        cfg_after = db_session.query(PrintConfig).filter(PrintConfig.sku == "P-0001").first()
        assert cfg_after.id == old_id
        assert cfg_after.component_id == old_comp
        assert cfg_after.plate_name == "改盘号-1"

    def test_change_component(self, db_session, catalog_tmp):
        result = catalog_edit.update_plate(
            db_session, "P-0001", PlateUpdate(component_sku="C-0002")
        )
        assert result["ok"] is True
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["编号"] == "P-0001")
        assert plate["组件编号"] == "C-0002"

    def test_component_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_plate(
            db_session, "P-0001", PlateUpdate(component_sku="C-9999")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "component_not_found"

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_plate(
            db_session, "P-9999", PlateUpdate(quantity=1)
        )
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"


class TestDeletePlate:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.delete_plate(db_session, "P-0001")
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        skus = [p["编号"] for p in data["打印盘"]]
        assert "P-0001" not in skus
        assert db_session.query(PrintConfig).filter(PrintConfig.sku == "P-0001").first() is None

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.delete_plate(db_session, "P-9999")
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
                    BOMItem(component_sku="C-0001", color="红色", quantity=2),
                    BOMItem(component_sku="C-0002", color="黑色", quantity=1),
                ],
            ),
        )
        assert result["ok"] is True, result
        assert result["new_sku"] == "PR-0002"
        data = _load_yaml(catalog_tmp)
        prod = next(p for p in data["产品"] if p["编号"] == "PR-0002")
        assert len(prod["BOM"]) == 2
        assert db_session.query(Product).filter(Product.sku == "PR-0002").first() is not None

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
                bom=[BOMItem(component_sku="C-9999", quantity=1)],
            ),
        )
        assert result["ok"] is False
        assert result["error_kind"] == "component_not_found"


class TestUpdateProduct:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.update_product(
            db_session,
            "PR-0001",
            ProductUpdate(
                description="改了描述",
                bom=[BOMItem(component_sku="C-0001", color="红色", quantity=3)],
            ),
        )
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        prod = next(p for p in data["产品"] if p["编号"] == "PR-0001")
        assert prod["描述"] == "改了描述"
        assert len(prod["BOM"]) == 1
        assert prod["BOM"][0]["数量"] == 3

    def test_rename_product_preserves_id(self, db_session, catalog_tmp):
        prod_before = db_session.query(Product).filter(Product.sku == "PR-0001").first()
        old_id = prod_before.id

        result = catalog_edit.update_product(
            db_session, "PR-0001", ProductUpdate(name="产品A-改名")
        )
        assert result["ok"] is True

        prod_after = db_session.query(Product).filter(Product.sku == "PR-0001").first()
        assert prod_after.id == old_id
        assert prod_after.name == "产品A-改名"

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.update_product(
            db_session, "PR-9999", ProductUpdate(description="x")
        )
        assert result["ok"] is False
        assert result["error_kind"] == "not_found"

    def test_empty_bom_rejected(self, db_session, catalog_tmp):
        result = catalog_edit.update_product(
            db_session, "PR-0001", ProductUpdate(bom=[])
        )
        assert result["ok"] is False
        assert result["error_kind"] == "invalid_input"


class TestDeleteProduct:
    def test_happy_path(self, db_session, catalog_tmp):
        result = catalog_edit.delete_product(db_session, "PR-0001")
        assert result["ok"] is True, result
        data = _load_yaml(catalog_tmp)
        skus = [p["编号"] for p in data["产品"]]
        assert "PR-0001" not in skus

    def test_product_in_use_by_pending_order(self, db_session, catalog_tmp):
        prod = db_session.query(Product).filter(Product.sku == "PR-0001").first()
        order = Order(status="pending")
        db_session.add(order)
        db_session.flush()
        db_session.add(OrderItem(order_id=order.id, product_id=prod.id, quantity=1))
        db_session.commit()

        original = catalog_tmp.read_bytes()
        result = catalog_edit.delete_product(db_session, "PR-0001")
        assert result["ok"] is False
        assert result["error_kind"] == "product_in_use"
        assert catalog_tmp.read_bytes() == original

    def test_shipped_order_does_not_block_delete(self, db_session, catalog_tmp):
        prod = db_session.query(Product).filter(Product.sku == "PR-0001").first()
        order = Order(status="shipped")
        db_session.add(order)
        db_session.flush()
        db_session.add(OrderItem(order_id=order.id, product_id=prod.id, quantity=1))
        db_session.commit()

        result = catalog_edit.delete_product(db_session, "PR-0001")
        assert result["ok"] is True, result

    def test_not_found(self, db_session, catalog_tmp):
        result = catalog_edit.delete_product(db_session, "PR-9999")
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
        assert "new_sku" in body
        assert "EP组件" in [c["名称"] for c in _load_yaml(catalog_tmp)["组件"]]

    def test_create_component_with_chinese_alias(self, client, catalog_tmp):
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
            "/api/catalog/components/C-0001",
            json={"description": "更新描述"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["编号"] == "C-0001")
        assert comp["描述"] == "更新描述"

    def test_rename_component_endpoint(self, client, catalog_tmp):
        """v0.3.0：可通过 PUT 改名（name 可改）"""
        r = client.put(
            "/api/catalog/components/C-0001",
            json={"name": "新名字"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        data = _load_yaml(catalog_tmp)
        comp = next(c for c in data["组件"] if c["编号"] == "C-0001")
        assert comp["名称"] == "新名字"

    def test_delete_component_endpoint(self, client, db_session, catalog_tmp):
        r1 = client.post("/api/catalog/components", json={"name": "待删组件"})
        sku = r1.json()["new_sku"]
        r = client.delete(f"/api/catalog/components/{sku}")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

    def test_create_plate_endpoint(self, client, catalog_tmp):
        r = client.post(
            "/api/catalog/plates",
            json={
                "plate_name": "EP盘",
                "component_sku": "C-0001",
                "quantity": 3,
                "duration_minutes": 45,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        assert "new_sku" in body

    def test_update_plate_endpoint(self, client, catalog_tmp):
        r = client.put(
            "/api/catalog/plates/P-0001",
            json={"quantity": 15},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        data = _load_yaml(catalog_tmp)
        plate = next(p for p in data["打印盘"] if p["编号"] == "P-0001")
        assert plate["数量"] == 15

    def test_delete_plate_endpoint(self, client, catalog_tmp):
        r = client.delete("/api/catalog/plates/P-0002")
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
                    {"component_sku": "C-0001", "color": "白色", "quantity": 1},
                ],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        assert "new_sku" in body

    def test_update_product_endpoint(self, client, catalog_tmp):
        r = client.put(
            "/api/catalog/products/PR-0001",
            json={"description": "通过 API 更新"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body
        data = _load_yaml(catalog_tmp)
        prod = next(p for p in data["产品"] if p["编号"] == "PR-0001")
        assert prod["描述"] == "通过 API 更新"

    def test_delete_product_endpoint(self, client, catalog_tmp):
        r = client.delete("/api/catalog/products/PR-0001")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True, body

    def test_component_in_use_endpoint_returns_error_body(self, client, catalog_tmp):
        r = client.delete("/api/catalog/components/C-0001")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error_kind"] == "component_in_use"

    def test_migrate_to_sku_endpoint_idempotent(self, client, catalog_tmp):
        """已是 SKU-keyed 格式 → 调端点照常 reload，backfilled=False。"""
        r = client.post("/api/catalog/migrate-to-sku")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["backfilled"] is False
        assert "stats" in body
