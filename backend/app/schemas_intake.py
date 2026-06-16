"""产品录入（intake）Pydantic schemas

完整契约见 docs/design/design-intake.md §2 数据契约。
所有响应 schema 以 `ok: bool` 开头，错误分支带 `error_kind` / `error` 字段。
"""

from typing import Literal, Optional

from pydantic import BaseModel


# ---------- Upload ----------

class UploadedImage(BaseModel):
    image_id: str                          # uuid4 hex
    filename: str
    suggested_class: Literal["assembly", "produce"]
    width: int
    height: int

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    ok: bool
    session_id: Optional[str] = None       # uuid4 hex；若客户端没传则后端生成
    images: list[UploadedImage] = []
    error_kind: Optional[str] = None
    error: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------- Recognize ----------

class RecognizeRequest(BaseModel):
    session_id: str
    assembly_image_ids: list[str]
    produce_image_ids: list[str]
    product_base_name: Optional[str] = None  # 可留空；LLM 推断
    # v0.2.5：用户可选的组件粒度提示，例如 "3 个组件：底柜、抽屉、抽屉把手"
    # 提供时强制 LLM 按用户列表归类，避免细分到打印件级别
    component_hints: Optional[str] = None

    model_config = {"from_attributes": True}


class DraftComponent(BaseModel):
    name: str                              # 形如 "床头柜-侧板"，已含产品基名前缀
    assembly_quantity: int                 # 装配件数（每套产品所需）

    model_config = {"from_attributes": True}


class DraftPlate(BaseModel):
    plate_name: str                        # 形如 "床头柜-侧板-2"，默认按命名约定生成
    component_name: str                    # 必须出现在 components 列表
    quantity_per_plate: int                # 单盘件数
    duration_minutes: int                  # 由 "2h43m"/"17m45s" 解析后的整数分钟
    source_image_id: str                   # 用于「原图复核」drawer 反查

    model_config = {"from_attributes": True}


class Conflict(BaseModel):
    kind: Literal["component", "plate", "product"]
    name: str                              # 草稿里冲突的名字
    existing_name: str                     # 与之冲突的现有 catalog 条目名

    model_config = {"from_attributes": True}


class Draft(BaseModel):
    product_base_name: str                 # LLM 推断或用户预填
    components: list[DraftComponent]
    plates: list[DraftPlate]

    model_config = {"from_attributes": True}


class RecognizeResponse(BaseModel):
    ok: bool
    draft: Optional[Draft] = None
    conflicts: list[Conflict] = []         # 预先兜底，前端 CUJ-3 顶部 alert 直接用
    error_kind: Optional[Literal[
        "no_api_key", "http_401", "http_5xx", "timeout",
        "parse_failed", "schema_invalid", "image_too_large",
        "not_implemented",
    ]] = None
    error: Optional[str] = None
    raw_response_preview: Optional[str] = None  # parse_failed 时附最多 200 字符的原始响应

    model_config = {"from_attributes": True}


# ---------- Merge ----------

class ColorCell(BaseModel):
    component_name: str                    # 必须出现在 components
    color: str                             # 中文字符串，非空（CUJ-4 要求所有 cell 填齐）

    model_config = {"from_attributes": True}


class Variant(BaseModel):
    variant_name: str                      # "床头柜 - 灰白"
    color_cells: list[ColorCell]           # 长度 == len(components)

    model_config = {"from_attributes": True}


class FinalDraft(BaseModel):
    product_base_name: str
    components: list[DraftComponent]
    plates: list[DraftPlate]
    variants: list[Variant]                # 长度 >= 1

    model_config = {"from_attributes": True}


class MergeRequest(BaseModel):
    session_id: str
    final_draft: FinalDraft

    model_config = {"from_attributes": True}


class MergeStats(BaseModel):
    新增组件: int
    新增打印盘: int
    新增产品变体: int
    new_skus: list[str] = []                # v0.3.0：本次 merge 新分配的所有 SKU

    model_config = {"from_attributes": True}


class MergeResponse(BaseModel):
    ok: bool
    stats: Optional[MergeStats] = None
    backup_path: Optional[str] = None
    timing_ms: Optional[dict[str, int]] = None  # {"写入": 12, "重新加载": 130}
    error_kind: Optional[Literal[
        "conflict", "backup_failed", "write_failed",
        "yaml_invalid", "load_failed", "not_implemented",
    ]] = None
    error: Optional[str] = None
    rolled_back: bool = False              # 写入或 reload 失败时是否成功从 bak 恢复
    details: Optional[list[Conflict]] = None  # error_kind == "conflict" 时

    model_config = {"from_attributes": True}
