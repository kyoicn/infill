"""v0.4 auto-import 迁移测试：ensure_order_auto_import_schema_exists。

覆盖：
- 第一次调用 → True；第二次 → False（幂等）
- 4 个新列被加上；partial unique index 被建
- 手动单 (platform/external_order_id 全 NULL) 不触发唯一冲突
- 真实平台单 (platform, external_order_id) 重复 → IntegrityError
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Order
from app.services.catalog import ensure_order_auto_import_schema_exists


def _new_engine_with_orm_tables():
    """用 ORM 建表（自带 4 个新列，但 partial unique index 还没建）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _session_for(engine):
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return TestSession()


def test_first_call_returns_true_second_false():
    """空 fresh in-memory SQLite → 第一次调用建 partial unique index 返回 True；第二次返回 False。"""
    engine = _new_engine_with_orm_tables()
    # ORM 建的表自带 4 列，但 partial unique index 还没建 → 第一次应建索引
    assert ensure_order_auto_import_schema_exists(engine) is True
    # 第二次：所有列 + 索引都在 → False
    assert ensure_order_auto_import_schema_exists(engine) is False


def test_creates_partial_unique_index():
    """调用后查 sqlite_master 应该能找到 uq_orders_platform_external。"""
    engine = _new_engine_with_orm_tables()
    ensure_order_auto_import_schema_exists(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='uq_orders_platform_external'"
            )
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "uq_orders_platform_external"


def test_old_schema_db_gets_columns_added():
    """v0.3 旧 orders 表（无 4 列）→ ALTER 加列 + 建索引。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY,
                    created_at DATETIME NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    shipped_at DATETIME,
                    notes TEXT DEFAULT ''
                )
                """
            )
        )
        conn.commit()

    assert ensure_order_auto_import_schema_exists(engine) is True
    cols = {c["name"] for c in inspect(engine).get_columns("orders")}
    for name in ("platform", "external_order_id", "buyer_nickname", "external_created_at"):
        assert name in cols, f"orders 缺 {name} 列"
    # 幂等
    assert ensure_order_auto_import_schema_exists(engine) is False


def test_manual_orders_with_null_external_id_dont_conflict():
    """3 条手动单（platform=None, external_order_id=None）应能共存。"""
    engine = _new_engine_with_orm_tables()
    ensure_order_auto_import_schema_exists(engine)

    session = _session_for(engine)
    try:
        session.add_all([
            Order(notes="m1"),
            Order(notes="m2"),
            Order(notes="m3"),
        ])
        session.commit()
        # 三条都进去了
        assert session.query(Order).count() == 3
        # platform / external_order_id 全 NULL
        for o in session.query(Order).all():
            assert o.platform is None
            assert o.external_order_id is None
    finally:
        session.close()


def test_duplicate_real_external_id_violates_index():
    """两条 (platform='xhs', external_order_id='X-001') → 第二条 IntegrityError。"""
    engine = _new_engine_with_orm_tables()
    ensure_order_auto_import_schema_exists(engine)

    session = _session_for(engine)
    try:
        session.add(Order(platform="xhs", external_order_id="X-001", buyer_nickname="alice"))
        session.commit()

        session.add(Order(platform="xhs", external_order_id="X-001", buyer_nickname="bob"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        # 不同 external_order_id 仍可插入
        session.add(Order(platform="xhs", external_order_id="X-002", buyer_nickname="carol"))
        session.commit()
        # 不同 platform、相同 external_order_id 也可
        session.add(Order(platform="dy", external_order_id="X-001", buyer_nickname="dave"))
        session.commit()

        assert session.query(Order).count() == 3
    finally:
        session.close()
