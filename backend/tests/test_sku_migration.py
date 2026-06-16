"""SKU 迁移测试 (v0.3.0)

覆盖：
- next_sku：基本 + max+1 + 起步 + 5 位扩展
- backfill_skus_if_needed：旧格式 → 新格式 + 引用迁移 + 幂等
- ensure_sku_column_exists：干净 DB 直接通过；旧无 sku 列 DB → ALTER TABLE 加列
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Component, PrintConfig, Product
from app.services.catalog import (
    backfill_skus_if_needed,
    ensure_sku_column_exists,
    load_catalog,
    next_sku,
)
from app.services import catalog as catalog_module


# ---------- next_sku ----------

class TestNextSku:
    def test_empty_set_starts_at_one(self):
        assert next_sku("组件", set()) == "C-0001"
        assert next_sku("打印盘", set()) == "P-0001"
        assert next_sku("产品", set()) == "PR-0001"

    def test_max_plus_one(self):
        assert next_sku("组件", {"C-0001", "C-0005", "C-0003"}) == "C-0006"
        assert next_sku("打印盘", {"P-0009"}) == "P-0010"
        assert next_sku("产品", {"PR-0042"}) == "PR-0043"

    def test_ignores_other_prefixes(self):
        # 段是组件 → 不应被 P-/PR- SKU 干扰
        assert next_sku("组件", {"P-9999", "PR-9999"}) == "C-0001"

    def test_handles_non_string_gracefully(self):
        # 包含 None 等异常值不应报错
        skus = {"C-0003", None, "garbage", "C-0001"}
        assert next_sku("组件", skus) == "C-0004"

    def test_unknown_section_raises(self):
        with pytest.raises(ValueError):
            next_sku("unknown", set())

    def test_extends_to_five_digits_at_overflow(self):
        # 已到 9999 → 下一个 10000，5 位
        assert next_sku("组件", {"C-9999"}) == "C-10000"


# ---------- backfill_skus_if_needed ----------

OLD_FORMAT_YAML = """
组件:
  - 名称: 床头柜-侧板
    描述: 装在两侧的板
    可选颜色: [白色, 红色]
  - 名称: 床头柜-抽屉

打印盘:
  - 盘号: 床头柜-侧板-3
    组件: 床头柜-侧板
    数量: 3
    耗时分钟: 163
  - 盘号: 床头柜-抽屉-2
    组件: 床头柜-抽屉
    数量: 2
    耗时分钟: 80

产品:
  - 名称: 床头柜
    描述: 三抽屉款
    BOM:
      - 组件: 床头柜-侧板
        颜色: 白色
        数量: 2
      - 组件: 床头柜-抽屉
        数量: 3
"""


class TestBackfillSkusIfNeeded:
    def test_old_format_gets_backfilled(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(OLD_FORMAT_YAML, encoding="utf-8")

        changed = backfill_skus_if_needed(catalog)
        assert changed is True

        data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
        # 组件每条都有"编号"
        for comp in data["组件"]:
            assert "编号" in comp
        # 顺序：第一个 = C-0001
        assert data["组件"][0]["编号"] == "C-0001"
        assert data["组件"][0]["名称"] == "床头柜-侧板"
        assert data["组件"][1]["编号"] == "C-0002"

        # 打印盘：编号 + 组件编号（不再是"组件: <name>"）
        for plate in data["打印盘"]:
            assert "编号" in plate
            assert "组件编号" in plate
            assert "组件" not in plate
        assert data["打印盘"][0]["编号"] == "P-0001"
        assert data["打印盘"][0]["组件编号"] == "C-0001"
        assert data["打印盘"][1]["组件编号"] == "C-0002"

        # 产品：编号 + BOM[].组件编号
        for prod in data["产品"]:
            assert "编号" in prod
            for bom in prod["BOM"]:
                assert "组件编号" in bom
                assert "组件" not in bom
        assert data["产品"][0]["编号"] == "PR-0001"
        assert data["产品"][0]["BOM"][0]["组件编号"] == "C-0001"
        assert data["产品"][0]["BOM"][1]["组件编号"] == "C-0002"

    def test_idempotent_on_already_backfilled(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(OLD_FORMAT_YAML, encoding="utf-8")
        # 第一次：补齐
        assert backfill_skus_if_needed(catalog) is True
        first_bytes = catalog.read_bytes()
        # 第二次：检测到所有条目都有编号 → 不动文件，返回 False
        assert backfill_skus_if_needed(catalog) is False
        assert catalog.read_bytes() == first_bytes

    def test_missing_file_returns_false(self, tmp_path):
        catalog = tmp_path / "does_not_exist.yaml"
        assert backfill_skus_if_needed(catalog) is False

    def test_empty_yaml_returns_false(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text("", encoding="utf-8")
        # 空 yaml → 没有任何条目缺编号（_has_skus 对空段返回 True）
        assert backfill_skus_if_needed(catalog) is False

    def test_partial_backfill_preserves_existing_skus(self, tmp_path):
        """部分条目已有编号、部分没有 → 只为缺的分配，从 max+1 起步。"""
        partial = """
组件:
  - 编号: C-0005
    名称: 已有编号
    可选颜色: [蓝色]
  - 名称: 缺编号

打印盘: []
产品: []
"""
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(partial, encoding="utf-8")
        assert backfill_skus_if_needed(catalog) is True
        data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
        # 已有的保留
        assert data["组件"][0]["编号"] == "C-0005"
        assert data["组件"][0]["名称"] == "已有编号"
        # 缺的分配 C-0006 (max=5, +1)
        assert data["组件"][1]["编号"] == "C-0006"
        assert data["组件"][1]["名称"] == "缺编号"

    def test_unknown_component_ref_raises(self, tmp_path):
        bad = """
组件:
  - 名称: 已知组件
打印盘:
  - 盘号: 盘1
    组件: 不存在的组件
    数量: 1
    耗时分钟: 30
产品: []
"""
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            backfill_skus_if_needed(catalog)

    def test_chinese_keys_preserved(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(OLD_FORMAT_YAML, encoding="utf-8")
        backfill_skus_if_needed(catalog)
        raw = catalog.read_text(encoding="utf-8")
        # 中文键存在
        assert "编号:" in raw
        assert "组件编号:" in raw
        # 没有 \u 转义
        assert "\\u" not in raw


# ---------- ensure_sku_column_exists ----------

class TestEnsureSkuColumnExists:
    def _new_engine_with_tables_via_orm(self):
        """用 ORM 建表（自带 sku 列）。"""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        return engine

    def _new_engine_with_old_schema(self):
        """模拟旧 DB：手工建无 sku 列的三表。"""
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
            conn.commit()
        return engine

    def test_clean_orm_db_is_noop(self):
        """ORM 建的表已经有 sku 列 → 不应再 ALTER。"""
        engine = self._new_engine_with_tables_via_orm()
        altered = ensure_sku_column_exists(engine)
        assert altered == []

    def test_old_schema_db_gets_sku_added(self):
        engine = self._new_engine_with_old_schema()
        altered = ensure_sku_column_exists(engine)
        # 三张表都该被 ALTER
        assert set(altered) == {"components", "print_configs", "products"}
        inspector = inspect(engine)
        for table in ("components", "print_configs", "products"):
            cols = {c["name"] for c in inspector.get_columns(table)}
            assert "sku" in cols, f"{table} 缺 sku 列"

    def test_idempotent_when_run_twice(self):
        engine = self._new_engine_with_old_schema()
        first = ensure_sku_column_exists(engine)
        assert len(first) == 3
        # 第二次跑：所有表都已有列 → 空列表
        second = ensure_sku_column_exists(engine)
        assert second == []

    def test_skips_missing_tables(self):
        """表不存在 → 跳过（由 create_all 后续补建），不报错。"""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # 不建任何表
        altered = ensure_sku_column_exists(engine)
        assert altered == []


# ---------- 端到端：旧格式 yaml + load_catalog 自动 backfill ----------

class TestLoadCatalogAutoBackfill:
    def test_old_yaml_then_load_catalog_succeeds(self, tmp_path, monkeypatch):
        """加载旧格式 catalog.yaml → 自动 backfill SKU → load 成功。"""
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text(OLD_FORMAT_YAML, encoding="utf-8")
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        session = TestSession()

        stats = load_catalog(session)
        assert stats["组件"] == 2
        assert stats["打印盘"] == 2
        assert stats["产品"] == 1

        # DB 端：组件每个都有 sku
        comps = session.query(Component).all()
        assert all(c.sku.startswith("C-") for c in comps)
        # 打印盘：sku 存在 + component_id 正确指向
        plates = session.query(PrintConfig).all()
        assert all(p.sku.startswith("P-") for p in plates)
        # 产品：sku 存在
        prods = session.query(Product).all()
        assert all(p.sku.startswith("PR-") for p in prods)

        # yaml 已被改写为新格式（含编号）
        data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
        for comp in data["组件"]:
            assert "编号" in comp

        session.close()
