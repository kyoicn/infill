from datetime import datetime, date
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, model_validator


# ---- Component ----

class ComponentBase(BaseModel):
    name: str
    description: str = ""

class ComponentCreate(ComponentBase):
    pass

class ComponentOut(ComponentBase):
    id: int
    sku: str
    colors: list[str] = []
    model_config = {"from_attributes": True}


# ---- PrintConfig ----

class PrintConfigBase(BaseModel):
    plate_name: str
    quantity: int
    duration_minutes: int

class PrintConfigCreate(PrintConfigBase):
    pass

class PrintConfigOut(PrintConfigBase):
    id: int
    sku: str
    component_id: int
    model_config = {"from_attributes": True}


# ---- Product ----

class ProductComponentBase(BaseModel):
    component_id: int
    color: str = ""
    quantity: int

class ProductComponentCreate(ProductComponentBase):
    pass

class ProductComponentOut(ProductComponentBase):
    id: int
    model_config = {"from_attributes": True}

class ProductBase(BaseModel):
    name: str
    description: str = ""

class ProductCreate(ProductBase):
    bom_items: list[ProductComponentCreate] = []

class ProductOut(ProductBase):
    id: int
    sku: str
    bom_items: list[ProductComponentOut] = []
    model_config = {"from_attributes": True}


# ---- Order ----

class OrderItemBase(BaseModel):
    product_id: int
    quantity: int

class OrderItemCreate(OrderItemBase):
    pass

class OrderItemOut(OrderItemBase):
    id: int
    model_config = {"from_attributes": True}

class OrderCreate(BaseModel):
    items: list[OrderItemCreate]
    notes: Optional[str] = None

class OrderUpdate(BaseModel):
    items: Optional[list[OrderItemCreate]] = None  # 给了就 REPLACE 所有 OrderItem
    notes: Optional[str] = None                    # 给了就更新备注

class OrderOut(BaseModel):
    id: int
    created_at: datetime
    status: str
    shipped_at: datetime | None = None
    notes: Optional[str] = ""
    # v0.4 auto-import 字段（手动单全为 None）
    platform: Optional[str] = None
    external_order_id: Optional[str] = None
    buyer_nickname: Optional[str] = None
    external_created_at: Optional[datetime] = None
    # v0.4.1 收货信息
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    recipient_address: Optional[str] = None
    items: list[OrderItemOut] = []
    model_config = {"from_attributes": True}


# ---- Inventory ----

class InventoryOut(BaseModel):
    id: int
    component_id: int
    color: str = ""
    quantity: int
    model_config = {"from_attributes": True}

class InventoryAdjust(BaseModel):
    component_id: int
    color: str = ""
    quantity: int  # 正数增加，负数减少


# ---- Printer ----

def _mask_access_code(access_code: Optional[str]) -> Optional[str]:
    """access_code 掩码：末 4 位明文，前缀 `*` 占位。

    - None → None
    - 长度 >= 4：前面用 `*` 占位，保留末 4 位明文，总长度等于原长
      （例：`"12345678"` → `"****5678"`；恰 4 位 `"1234"` → `"****"` 全掩码，
      避免 4 位时无 `*` 让人看不出是掩码）
    - 长度 < 4：全 `*`，长度等于原长（例：`"abc"` → `"***"`）
    """
    if access_code is None:
        return None
    n = len(access_code)
    if n < 4:
        return "*" * n
    if n == 4:
        return "****"
    return "*" * (n - 4) + access_code[-4:]


class PrinterBase(BaseModel):
    name: str

class PrinterCreate(PrinterBase):
    pass

class PrinterUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    serial: Optional[str] = None
    access_code: Optional[str] = None
    model_config = ConfigDict(extra="forbid")

class PrinterOut(PrinterBase):
    id: int
    ip: Optional[str] = None
    serial: Optional[str] = None
    access_code_masked: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _derive_access_code_masked(cls, data: Any) -> Any:
        """从 ORM 对象 / dict 的 access_code 字段派生 access_code_masked。

        不把 access_code 原值往外送 —— 仅在内部读取后转成掩码。
        """
        if isinstance(data, dict):
            if "access_code_masked" not in data and "access_code" in data:
                data = {**data}
                data["access_code_masked"] = _mask_access_code(data.get("access_code"))
            return data
        # ORM 对象 / 任意 attribute-style 入参
        if not hasattr(data, "access_code_masked") or getattr(data, "access_code_masked", None) is None:
            access_code = getattr(data, "access_code", None)
            masked = _mask_access_code(access_code)
            if masked is not None:
                try:
                    setattr(data, "access_code_masked", masked)
                except (AttributeError, TypeError):
                    pass
        return data


# ---- Schedule Config ----

class TimeWindow(BaseModel):
    start: str  # "HH:MM"
    end: str    # "HH:MM"

class ScheduleConfigBase(BaseModel):
    day_of_week: int
    windows: list[TimeWindow]

class ScheduleConfigCreate(ScheduleConfigBase):
    pass

class ScheduleConfigOut(ScheduleConfigBase):
    id: int
    model_config = {"from_attributes": True}


# ---- System Config ----

class SystemConfigOut(BaseModel):
    key: str
    value: str
    model_config = {"from_attributes": True}

class SystemConfigUpdate(BaseModel):
    key: str
    value: str


# ---- Print Plan ----

class PrintTaskOut(BaseModel):
    id: int
    printer_id: int
    print_config_id: int
    color: str = ""
    is_surplus: bool = False
    start_time: str
    end_time: str
    status: str
    model_config = {"from_attributes": True}

class PrintBatchOut(BaseModel):
    id: int
    start_time: str
    batch_order: int
    status: str
    tasks: list[PrintTaskOut] = []
    model_config = {"from_attributes": True}

class PrintPlanOut(BaseModel):
    id: int
    date: date
    start_time: str
    duration_hours: int
    status: str
    created_at: datetime
    batches: list[PrintBatchOut] = []
    model_config = {"from_attributes": True}

class GeneratePlanRequest(BaseModel):
    date: date
    surplus_enabled: bool = True
    start_time: str = "00:00"
    duration_hours: int = 24
    strategy: str = "product_first"  # "product_first" | "utilization" | "two_phase"
    target_product_ids: list[int] | None = None  # 指定产品过滤，None 表示不过滤
    sync_strength: int = 50  # 0~100, 同步强度
