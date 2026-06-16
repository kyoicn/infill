"""目录 CRUD（catalog edit）请求 schema

字段用 snake_case，alias 接 catalog.yaml 的中文 key，
便于前端走中文键直传也能正常 parse（populate_by_name=True）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- 通用配置 ----------

_MODEL_CONFIG = ConfigDict(populate_by_name=True, extra="ignore")


# ---------- Component ----------

class ComponentCreate(BaseModel):
    model_config = _MODEL_CONFIG

    name: str = Field(..., alias="名称")
    description: Optional[str] = Field(default=None, alias="描述")
    colors: list[str] = Field(default_factory=list, alias="可选颜色")


class ComponentUpdate(BaseModel):
    """更新组件：name 不可改（YAML 稳定标识）"""

    model_config = _MODEL_CONFIG

    description: Optional[str] = Field(default=None, alias="描述")
    colors: Optional[list[str]] = Field(default=None, alias="可选颜色")


# ---------- Plate (PrintConfig) ----------

class PlateCreate(BaseModel):
    model_config = _MODEL_CONFIG

    plate_name: str = Field(..., alias="盘号")
    component_name: str = Field(..., alias="组件")
    quantity: int = Field(..., alias="数量")
    duration_minutes: int = Field(..., alias="耗时分钟")


class PlateUpdate(BaseModel):
    """更新打印盘：plate_name 不可改"""

    model_config = _MODEL_CONFIG

    component_name: Optional[str] = Field(default=None, alias="组件")
    quantity: Optional[int] = Field(default=None, alias="数量")
    duration_minutes: Optional[int] = Field(default=None, alias="耗时分钟")


# ---------- Product ----------

class BOMItem(BaseModel):
    model_config = _MODEL_CONFIG

    component_name: str = Field(..., alias="组件")
    color: Optional[str] = Field(default=None, alias="颜色")
    quantity: int = Field(..., alias="数量")


class ProductCreate(BaseModel):
    model_config = _MODEL_CONFIG

    name: str = Field(..., alias="名称")
    description: Optional[str] = Field(default=None, alias="描述")
    bom: list[BOMItem] = Field(..., alias="BOM")


class ProductUpdate(BaseModel):
    """更新产品：name 不可改"""

    model_config = _MODEL_CONFIG

    description: Optional[str] = Field(default=None, alias="描述")
    bom: Optional[list[BOMItem]] = Field(default=None, alias="BOM")
