"""PRD-007 (T1.1) 打印机状态 schema 迁移测试。

覆盖：
- case 1: 旧 DB（printers 无 ip/serial/access_code 三列）→ auto_migrate + create_all → 三列补齐
- case 2: create_all 后 printer_status_sample 表存在
- case 3: 删 Printer 触发 FK CASCADE → 所有关联 sample 消失
- case 4: 复合索引 ix_printer_status_sample_printer_ts 存在
- case 5: 新部署（空 DB → create_all）一次性建好所有列 + 表
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Printer, PrinterStatusSample
from app.services.migrate import auto_migrate


def _new_empty_engine():
    """完全空的 in-memory SQLite engine（StaticPool 保证多连接共享同一 DB）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # StaticPool 复用同一物理连接，但 connect 事件已在 app.database 导入时挂上 Engine
    # 类（全局），新 engine 也会触发。这里再显式确保 foreign_keys=ON（防御性）。
    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _session_for(engine):
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return TestSession()


def _create_old_printers_table(engine):
    """模拟 v0.4 之前的 printers 表：只有 id + name，没有凭证三列。"""
    with engine.connect() as conn:
        conn.execute(text(
            """
            CREATE TABLE printers (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL
            )
            """
        ))
        conn.commit()


def test_old_db_auto_migrate_adds_three_credential_columns():
    """case 1：旧 DB 只有 id+name → auto_migrate 把 ip/serial/access_code 补上。"""
    engine = _new_empty_engine()
    _create_old_printers_table(engine)

    # 预置一行旧数据，验证迁移不破坏现有行
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO printers (name) VALUES ('旧机')"))
        conn.commit()

    auto_migrate(engine)
    Base.metadata.create_all(bind=engine)

    cols_info = inspect(engine).get_columns("printers")
    col_names = {c["name"] for c in cols_info}
    for name in ("ip", "serial", "access_code"):
        assert name in col_names, f"printers 缺 {name} 列"

    # 旧行仍在，且新列为 NULL
    session = _session_for(engine)
    try:
        existing = session.query(Printer).all()
        assert len(existing) == 1
        assert existing[0].name == "旧机"
        assert existing[0].ip is None
        assert existing[0].serial is None
        assert existing[0].access_code is None
    finally:
        session.close()


def test_create_all_creates_printer_status_sample_table():
    """case 2：create_all 后 printer_status_sample 表出现在 schema 中。"""
    engine = _new_empty_engine()
    Base.metadata.create_all(bind=engine)

    tables = set(inspect(engine).get_table_names())
    assert "printer_status_sample" in tables


def test_fk_cascade_deletes_samples_when_printer_deleted():
    """case 3：删 Printer 触发 FK CASCADE，关联 sample 全部消失。"""
    engine = _new_empty_engine()
    Base.metadata.create_all(bind=engine)

    session = _session_for(engine)
    try:
        p = Printer(name="A1 mini", ip="192.168.1.10", serial="SN-0001", access_code="abc12345")
        session.add(p)
        session.commit()

        base_ts = datetime(2026, 6, 22, 10, 0, 0)
        session.add_all([
            PrinterStatusSample(printer_id=p.id, ts=base_ts, state="running"),
            PrinterStatusSample(printer_id=p.id, ts=base_ts + timedelta(minutes=1), state="running"),
            PrinterStatusSample(printer_id=p.id, ts=base_ts + timedelta(minutes=2), state="pause"),
        ])
        session.commit()
        assert session.query(PrinterStatusSample).count() == 3

        # 删 Printer：依赖 SQLite FK ON DELETE CASCADE
        session.delete(p)
        session.commit()

        # 所有 sample 都消失
        assert session.query(PrinterStatusSample).count() == 0
    finally:
        session.close()


def test_composite_index_on_printer_id_and_ts_exists():
    """case 4：复合索引 ix_printer_status_sample_printer_ts 已建。"""
    engine = _new_empty_engine()
    Base.metadata.create_all(bind=engine)

    indexes = inspect(engine).get_indexes("printer_status_sample")
    target = next(
        (idx for idx in indexes if idx["name"] == "ix_printer_status_sample_printer_ts"),
        None,
    )
    assert target is not None, "未找到复合索引 ix_printer_status_sample_printer_ts"
    assert list(target["column_names"]) == ["printer_id", "ts"]


def test_fresh_deployment_creates_all_schema_in_one_shot():
    """case 5：空 DB → create_all → printers 含三新列、printer_status_sample 存在 + 索引存在。"""
    engine = _new_empty_engine()
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)

    # printers 三新列
    col_names = {c["name"] for c in inspector.get_columns("printers")}
    for name in ("ip", "serial", "access_code"):
        assert name in col_names, f"printers 缺 {name} 列"

    # printer_status_sample 表
    assert "printer_status_sample" in set(inspector.get_table_names())

    # 复合索引
    idx_names = {idx["name"] for idx in inspector.get_indexes("printer_status_sample")}
    assert "ix_printer_status_sample_printer_ts" in idx_names
