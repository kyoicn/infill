from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Boolean,
)
from sqlalchemy.orm import relationship

from .database import Base


class Component(Base):
    __tablename__ = "components"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    colors = Column(JSON, default=list, nullable=False)  # ["白色", "红色", ...]

    print_configs = relationship("PrintConfig", back_populates="component", cascade="all, delete-orphan")
    inventory_items = relationship("Inventory", back_populates="component", cascade="all, delete-orphan")


class PrintConfig(Base):
    __tablename__ = "print_configs"

    id = Column(Integer, primary_key=True, index=True)
    plate_name = Column(String, nullable=False)  # 盘号，如"1号盘"
    component_id = Column(Integer, ForeignKey("components.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    duration_minutes = Column(Integer, nullable=False)

    component = relationship("Component", back_populates="print_configs")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, default="")

    bom_items = relationship("ProductComponent", back_populates="product", cascade="all, delete-orphan")


class ProductComponent(Base):
    __tablename__ = "product_components"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    component_id = Column(Integer, ForeignKey("components.id"), nullable=False)
    color = Column(String, default="", nullable=False)
    quantity = Column(Integer, nullable=False)

    product = relationship("Product", back_populates="bom_items")
    component = relationship("Component")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending / shipped
    shipped_at = Column(DateTime, nullable=True)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product")


class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)
    component_id = Column(Integer, ForeignKey("components.id"), nullable=False)
    color = Column(String, default="", nullable=False)
    quantity = Column(Integer, default=0, nullable=False)

    component = relationship("Component", back_populates="inventory_items")


class Printer(Base):
    __tablename__ = "printers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)


class ScheduleConfig(Base):
    __tablename__ = "schedule_configs"

    id = Column(Integer, primary_key=True, index=True)
    day_of_week = Column(Integer, unique=True, nullable=False)  # 0=周一, 6=周日
    windows = Column(JSON, nullable=False)  # [{"start": "08:00", "end": "12:00"}, ...]


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=False)


class PrintPlan(Base):
    __tablename__ = "print_plans"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False)
    start_time = Column(String, default="00:00", nullable=False)  # "HH:MM"
    duration_hours = Column(Integer, default=24, nullable=False)
    status = Column(String, default="draft", nullable=False)  # draft / confirmed
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    batches = relationship("PrintBatch", back_populates="plan", cascade="all, delete-orphan", order_by="PrintBatch.batch_order")


class PrintBatch(Base):
    __tablename__ = "print_batches"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("print_plans.id"), nullable=False)
    start_time = Column(String, nullable=False)  # "HH:MM" 格式
    batch_order = Column(Integer, nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending / started / completed

    plan = relationship("PrintPlan", back_populates="batches")
    tasks = relationship("PrintTask", back_populates="batch", cascade="all, delete-orphan")


class PrintTask(Base):
    __tablename__ = "print_tasks"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("print_batches.id"), nullable=False)
    printer_id = Column(Integer, ForeignKey("printers.id"), nullable=False)
    print_config_id = Column(Integer, ForeignKey("print_configs.id"), nullable=False)
    color = Column(String, default="", nullable=False)
    is_surplus = Column(Boolean, default=False, nullable=False)  # 富余生产任务
    start_time = Column(String, nullable=False)  # "HH:MM"
    end_time = Column(String, nullable=False)  # "HH:MM"
    status = Column(String, default="pending", nullable=False)  # pending / completed

    batch = relationship("PrintBatch", back_populates="tasks")
    printer = relationship("Printer")
    print_config = relationship("PrintConfig")
