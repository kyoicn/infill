"""自动导入订单（prd-006）Pydantic schemas

完整契约见 docs/prd/prd-006-auto-import-orders.md CUJ-1/2/3/4。

所有响应 schema 以 `ok: bool` 开头，错误分支带 `error_kind` / `error` 字段
（与 prd-005 intake 一致风格）。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ==== ADB Config + Probe + Extension (Task 2.1) ====
# Task 2.1 owns AdbConfig / Diagnostic / TestAdbResponse / ExtensionStatusResponse。
# 本 worktree 早于 Task 2.1 落地，先放占位定义；Task 2.1 合入后保留同形即可。

class AdbConfig(BaseModel):
    device_type: str = "mumu"  # mumu / bluestacks / leidian / usb
    pc_ip: str = ""
    port: int = 7555


class Diagnostic(BaseModel):
    name: str          # 如 "adb_client_installed"
    label: str         # 用户可见中文标签
    ok: bool
    detail: str = ""   # 失败时的诊断详情（命令输出 / 报错 / 修复建议）


class TestAdbResponse(BaseModel):
    ok: bool
    adb_connected: bool = False
    device_serial: Optional[str] = None
    android_version: Optional[str] = None
    diagnostics: list[Diagnostic] = []
    error_kind: Optional[str] = None
    error: Optional[str] = None


class ExtensionStatusResponse(BaseModel):
    ok: bool
    installed: bool = False
    version: Optional[str] = None
    last_probe_at: Optional[str] = None
    error_kind: Optional[str] = None
    error: Optional[str] = None


# ==== LLM SKU Match (Task 2.2) ====
# Task 2.2 owns SkuSearchRequest / SkuSearchResponse / SkuSearchHit。
# 本 worktree 早于 Task 2.2 落地，先放占位定义；Task 2.2 合入后保留同形即可。

class SkuSearchHit(BaseModel):
    sku_code: str
    sku_name: str
    score: float = 0.0


class SkuSearchRequest(BaseModel):
    q: str
    limit: int = 10


class SkuSearchResponse(BaseModel):
    ok: bool
    hits: list[SkuSearchHit] = []
    error_kind: Optional[str] = None
    error: Optional[str] = None


# ==== Scan + Commit (Task 2.3) ====

# 扫描输入：扩展 / 截屏 LLM 解析后的原始订单结构（去结构化后端口）

class RawOrder(BaseModel):
    external_order_id: Optional[str] = None
    buyer_nickname: Optional[str] = None
    external_created_at: Optional[str] = None
    products: list[dict] = []  # 每个 dict 至少含 listing_title / quantity


# 预览态

class PreviewProduct(BaseModel):
    listing_title: str
    quantity: int
    matched_sku_code: Optional[str] = None
    confidence: float = 0.0
    reasoning: str = ""


class PreviewItem(BaseModel):
    external_order_id: str
    buyer_nickname: str
    external_created_at: Optional[str] = None
    is_duplicate: bool = False
    existing_order_id: Optional[int] = None
    products: list[PreviewProduct] = []


class DroppedOrder(BaseModel):
    external_order_id: Optional[str] = None
    buyer_nickname: Optional[str] = None
    external_created_at: Optional[str] = None
    products: list[dict] = []
    reason: str  # 如 "missing_required_fields"
    missing_fields: list[str] = []


class ScanStats(BaseModel):
    total: int = 0
    dropped_count: int = 0
    duplicate_count: int = 0
    high_conf: int = 0
    mid_conf: int = 0
    low_conf: int = 0


class ScanResponse(BaseModel):
    ok: bool
    batch_id: str = ""
    items: list[PreviewItem] = []
    dropped: list[DroppedOrder] = []
    screen_count: int = 0  # 闲鱼批次保留下的截屏数；小红书 0
    stats: ScanStats = Field(default_factory=ScanStats)
    error_kind: Optional[str] = None
    error: Optional[str] = None


class ScanRequest(BaseModel):
    batch_id: str
    raw_orders: list[RawOrder]


# 提交（CUJ-3 落库）

class CommitProduct(BaseModel):
    sku: str
    quantity: int


class CommitItem(BaseModel):
    external_order_id: str
    buyer_nickname: str
    external_created_at: Optional[str] = None
    platform: str  # xhs / xianyu
    override_duplicate: bool = False
    products: list[CommitProduct] = []


class CommitRequest(BaseModel):
    batch_id: str
    items: list[CommitItem]


class CommitStats(BaseModel):
    """统计：用中文别名输出（与 PRD 文案一致），同时允许 Python 名字读写。"""

    model_config = ConfigDict(populate_by_name=True)

    new: int = Field(default=0, alias="新增")
    dup_skipped: int = Field(default=0, alias="重复跳过")
    manual_skipped: int = Field(default=0, alias="手动跳过")
    match_rate: float = Field(default=0.0, alias="SKU匹配率")


class CommitResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    stats: Optional[CommitStats] = None
    created_order_ids: list[int] = []
    total_ms: int = 0
    error_kind: Optional[str] = None
    error: Optional[str] = None


# CUJ-2 闲鱼截屏 + scan 状态

class ScreencapRequest(BaseModel):
    batch_id: str
    config: AdbConfig


class ScreencapResponse(BaseModel):
    ok: bool
    screen_id: str = ""
    seq: int = 0
    error_kind: Optional[str] = None
    error: Optional[str] = None


class ScanStatusResponse(BaseModel):
    batch_id: str
    screens: list[dict] = []          # [{seq, status, error}]
    parsed_orders: list[dict] = []    # [{external_order_id, buyer_nickname, products, ...}]


class FinishScanRequest(BaseModel):
    batch_id: str


class CancelScanRequest(BaseModel):
    batch_id: str


# CUJ-1 小红书探活

class ProbeXhsResponse(BaseModel):
    ok: bool
    has_xhs_tab: bool = False
    error_kind: Optional[str] = None
    error: Optional[str] = None


class ProbeXianyuRequest(BaseModel):
    config: AdbConfig


class ProbeXianyuResponse(BaseModel):
    ok: bool
    adb_connected: bool = False
    device_serial: Optional[str] = None
    diagnostics: list[Diagnostic] = []
    error_kind: Optional[str] = None
    error: Optional[str] = None
