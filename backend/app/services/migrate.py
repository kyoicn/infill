"""
启动时自动检测模型与数据库表结构差异，补齐缺失的列。
不处理列删除、类型变更等复杂迁移——遇到无法自动修复的情况直接跳过。
"""

import logging
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from ..database import Base

logger = logging.getLogger(__name__)


# SQLAlchemy type → SQLite type 映射
_TYPE_MAP = {
    "INTEGER": "INTEGER",
    "VARCHAR": "TEXT",
    "TEXT": "TEXT",
    "FLOAT": "REAL",
    "BOOLEAN": "INTEGER",
    "DATE": "TEXT",
    "DATETIME": "TEXT",
    "JSON": "TEXT",
}


def _sa_type_to_sqlite(sa_type) -> str:
    type_name = type(sa_type).__name__.upper()
    return _TYPE_MAP.get(type_name, "TEXT")


def auto_migrate(engine: Engine):
    """比对 ORM 模型和实际表结构，用 ALTER TABLE ADD COLUMN 补齐缺失列。"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            # 整张表不存在，create_all 会处理
            continue

        existing_cols = {col["name"] for col in inspector.get_columns(table_name)}

        for column in table.columns:
            if column.name in existing_cols:
                continue

            col_type = _sa_type_to_sqlite(column.type)
            default = ""
            if column.default is not None:
                val = column.default.arg
                if callable(val):
                    # 跳过动态默认值（如 datetime.now）
                    default = ""
                elif isinstance(val, str):
                    default = f" DEFAULT '{val}'"
                else:
                    default = f" DEFAULT {val}"
            elif column.nullable:
                default = " DEFAULT NULL"

            sql = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type}{default}"
            logger.info(f"自动补列: {sql}")
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
