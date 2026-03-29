from datetime import datetime, date
from pydantic import BaseModel


# ---- Component ----

class ComponentBase(BaseModel):
    name: str
    description: str = ""

class ComponentCreate(ComponentBase):
    pass

class ComponentOut(ComponentBase):
    id: int
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
    component_id: int
    model_config = {"from_attributes": True}


# ---- Product ----

class ProductComponentBase(BaseModel):
    component_id: int
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

class OrderOut(BaseModel):
    id: int
    created_at: datetime
    status: str
    shipped_at: datetime | None = None
    items: list[OrderItemOut] = []
    model_config = {"from_attributes": True}


# ---- Inventory ----

class InventoryOut(BaseModel):
    id: int
    component_id: int
    quantity: int
    model_config = {"from_attributes": True}

class InventoryAdjust(BaseModel):
    component_id: int
    quantity: int  # 正数增加，负数减少


# ---- Printer ----

class PrinterBase(BaseModel):
    name: str

class PrinterCreate(PrinterBase):
    pass

class PrinterOut(PrinterBase):
    id: int
    model_config = {"from_attributes": True}


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
    start_time: str
    end_time: str
    model_config = {"from_attributes": True}

class PrintBatchOut(BaseModel):
    id: int
    start_time: str
    batch_order: int
    tasks: list[PrintTaskOut] = []
    model_config = {"from_attributes": True}

class PrintPlanOut(BaseModel):
    id: int
    date: date
    status: str
    created_at: datetime
    batches: list[PrintBatchOut] = []
    model_config = {"from_attributes": True}

class GeneratePlanRequest(BaseModel):
    date: date
    surplus_enabled: bool = True
