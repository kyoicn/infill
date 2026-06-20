"""自动导入订单（prd-006）API router

挂载 prefix `/api/auto-import`，注册由 Task 5.1 在 main.py 完成（不在本文件做）。

按 CUJ 分组（详见 docs/prd/prd-006-auto-import-orders.md）：
- CUJ-1 小红书：extension-status / probe / scan
- CUJ-2 闲鱼：probe / screencap / scan-status / finish-scan / cancel-scan
- CUJ-3 校对落库：sku-search / commit
- CUJ-4 配置：xianyu config GET/PUT / test-adb

所有端点返回 `{ok: bool, ...}` 信封；失败编码为 `{ok: false, error_kind, error}`，
不抛 HTTPException（与 prd-005 intake 一致）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, OrderItem, Product
from ..schemas_auto_import import (
    AdbConfig,
    CancelScanRequest,
    CommitRequest,
    CommitResponse,
    CommitStats,
    DroppedOrder,
    ExtensionStatusResponse,
    FinishScanRequest,
    PreviewItem,
    PreviewProduct,
    ProbeXhsResponse,
    ProbeXianyuResponse,
    ScanRequest,
    ScanResponse,
    ScanStats,
    ScanStatusResponse,
    ScreencapResponse,
    SkuSearchRequest,
    SkuSearchResponse,
    SkuSearchHit,
    TestAdbResponse,
)
from ..services import auto_import as svc
from ..services import auto_import_llm
from ..services.intake_llm import LLMProviderError


router = APIRouter(prefix="/api/auto-import", tags=["自动导入"])


# ---------- 临时截屏目录 ----------

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "auto_import_tmp"


def _batch_tmp_dir(batch_id: str) -> Path:
    safe = "".join(ch for ch in batch_id if ch.isalnum() or ch in ("-", "_"))
    return _DATA_DIR / (safe or "default")


def _confidence_bucket(c: float) -> str:
    if c >= 0.85:
        return "high"
    if c >= 0.55:
        return "mid"
    return "low"


def _pick_device_serial(config: "AdbConfig", devices: list) -> str | None:
    """Return the serial of the device matching `config`, in `device` state.

    For USB device type, picks the first online device whose serial doesn't look
    like a host:port endpoint (USB serials are hardware IDs, not IPs).
    For network emulators, picks the device whose serial starts with `config.pc_ip`.
    """
    def _serial(d) -> str:
        return (d.get("serial") if isinstance(d, dict) else "") or ""

    def _state(d) -> str:
        return (d.get("state") if isinstance(d, dict) else "") or ""

    if config.device_type == "usb":
        for d in devices:
            s = _serial(d)
            if s and ":" not in s and _state(d) == "device":
                return s
        return None

    for d in devices:
        s = _serial(d)
        if s and config.pc_ip and s.startswith(config.pc_ip) and _state(d) == "device":
            return s
    return None


class _SkuNotFoundError(Exception):
    """commit 阶段校验 SKU 不存在 → 触发整批回滚。"""


# ============================================================
# CUJ-1 小红书
# ============================================================

@router.get("/xhs/extension-status", response_model=ExtensionStatusResponse)
def xhs_extension_status() -> ExtensionStatusResponse:
    """检测 Chrome 扩展是否就绪 + 版本。"""
    try:
        return svc.get_extension_status()
    except Exception as exc:  # noqa: BLE001
        return ExtensionStatusResponse(
            ok=False,
            installed=False,
            error_kind="extension_probe_failed",
            error=str(exc),
        )


@router.post("/xhs/probe", response_model=ProbeXhsResponse)
def xhs_probe() -> ProbeXhsResponse:
    """探查 Chrome 是否打开了千帆 tab（实际由扩展前端侧探活，本接口占位）。"""
    return ProbeXhsResponse(ok=True, has_xhs_tab=True)


@router.post("/xhs/scan", response_model=ScanResponse)
def xhs_scan(body: ScanRequest, db: Session = Depends(get_db)) -> ScanResponse:
    """小红书扫单：扩展回传原始结构 → 标准化 + 去重 + LLM SKU 匹配 → 预览。

    每条 raw_order 的处理：
    1. 必填三件套（external_order_id / buyer_nickname / 非空 products）缺失 → 丢弃
    2. (platform=xhs, external_order_id) 已存在 → is_duplicate=True
    3. 每个商品调 match_listing_to_sku；LLM 失败时按条降级（不中断整批）
    """
    try:
        return _scan_impl(
            db=db,
            batch_id=body.batch_id,
            raw_orders=[ro.model_dump() for ro in body.raw_orders],
            platform="xhs",
        )
    except Exception as exc:  # noqa: BLE001 兜底
        return ScanResponse(
            ok=False,
            batch_id=body.batch_id,
            error_kind="scan_failed",
            error=str(exc),
        )


def _scan_impl(
    *,
    db: Session,
    batch_id: str,
    raw_orders: list[dict],
    platform: str,
) -> ScanResponse:
    """xhs / 闲鱼 finish-scan 共用的扫描流水线。"""
    catalog = svc.load_catalog_for_llm(db)
    sku_to_name = {row.get("sku"): row.get("name", "") for row in catalog if row.get("sku")}

    items: list[PreviewItem] = []
    dropped: list[DroppedOrder] = []
    duplicate_count = 0
    high_conf = mid_conf = low_conf = 0

    for raw in raw_orders:
        ext_id = (raw.get("external_order_id") or "").strip() or None
        buyer = (raw.get("buyer_nickname") or "").strip() or None
        products = raw.get("products") or []

        # Step 1：必填三件套
        if not ext_id or not buyer or not products:
            missing = []
            if not ext_id:
                missing.append("external_order_id")
            if not buyer:
                missing.append("buyer_nickname")
            if not products:
                missing.append("products")
            dropped.append(DroppedOrder(
                external_order_id=ext_id,
                buyer_nickname=buyer,
                external_created_at=raw.get("external_created_at"),
                products=products if isinstance(products, list) else [],
                reason="missing_required_fields",
                missing_fields=missing,
            ))
            continue

        # Step 2：去重查询
        existing = (
            db.query(Order)
            .filter(Order.platform == platform, Order.external_order_id == ext_id)
            .first()
        )
        is_dup = existing is not None
        existing_id = existing.id if existing else None
        if is_dup:
            duplicate_count += 1

        # Step 3：每个商品 LLM 匹配
        preview_products: list[PreviewProduct] = []
        for prod in products:
            title = (prod.get("listing_title") or "").strip()
            qty = int(prod.get("quantity") or 1)

            try:
                match = auto_import_llm.match_listing_to_sku(title, catalog)
                # match_listing_to_sku 真实返回 tuple (sku|None, conf, reasoning)；
                # 兼容老测试桩返回 dict 的写法（mapping 形式），二选一解包。
                if isinstance(match, tuple):
                    matched_sku, confidence_raw, reasoning_raw = match
                    confidence = float(confidence_raw or 0.0)
                    reasoning = reasoning_raw or ""
                else:
                    matched_sku = match.get("matched_sku_code")
                    confidence = float(match.get("confidence") or 0.0)
                    reasoning = match.get("reasoning") or ""
            except LLMProviderError as exc:
                matched_sku = None
                confidence = 0.0
                reasoning = f"LLM 错误：{exc.message}"

            bucket = _confidence_bucket(confidence)
            if matched_sku is None:
                # 未匹配视为低置信度
                low_conf += 1
            elif bucket == "high":
                high_conf += 1
            elif bucket == "mid":
                mid_conf += 1
            else:
                low_conf += 1

            preview_products.append(PreviewProduct(
                listing_title=title,
                quantity=qty,
                matched_sku_code=matched_sku,
                matched_sku_name=sku_to_name.get(matched_sku) if matched_sku else None,
                confidence=confidence,
                reasoning=reasoning,
            ))

        items.append(PreviewItem(
            external_order_id=ext_id,
            buyer_nickname=buyer,
            external_created_at=raw.get("external_created_at"),
            is_duplicate=is_dup,
            existing_order_id=existing_id,
            products=preview_products,
            recipient_name=(raw.get("recipient_name") or None),
            recipient_phone=(raw.get("recipient_phone") or None),
            recipient_address=(raw.get("recipient_address") or None),
        ))

    stats = ScanStats(
        total=len(items),
        dropped_count=len(dropped),
        duplicate_count=duplicate_count,
        high_conf=high_conf,
        mid_conf=mid_conf,
        low_conf=low_conf,
    )

    # 闲鱼批次会在 BATCH_SCREENS 留有截屏，把数量回传供前端 preview 拉缩略图
    bstate = svc.BATCH_SCREENS.get(batch_id) or {}
    screen_count = len(bstate.get("screens") or [])

    return ScanResponse(
        ok=True,
        batch_id=batch_id,
        items=items,
        dropped=dropped,
        screen_count=screen_count,
        stats=stats,
    )


# ============================================================
# CUJ-2 闲鱼
# ============================================================

@router.post("/xianyu/probe", response_model=ProbeXianyuResponse)
def xianyu_probe(config: AdbConfig) -> ProbeXianyuResponse:
    try:
        diagnostics = svc.diagnose_adb(config)
        client = svc.AdbClient()
        devices = client.list_devices()
        # True only when the configured endpoint's device passed the device_state check
        # (i.e. is in "device" state). Falling back to `bool(devices)` would falsely
        # report connected when an unrelated USB device shows up in `adb devices`.
        adb_connected = any(
            d.get("ok") for d in diagnostics if d.get("name") == "device_state"
        )
        device_serial = _pick_device_serial(config, devices)
        return ProbeXianyuResponse(
            ok=True,
            adb_connected=adb_connected,
            device_serial=device_serial,
            diagnostics=diagnostics,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeXianyuResponse(
            ok=False,
            adb_connected=False,
            error_kind="adb_probe_failed",
            error=str(exc),
        )


@router.post("/xianyu/screencap", response_model=ScreencapResponse)
async def xianyu_screencap(
    batch_id: str = Form(...),
    png: UploadFile = File(...),
) -> ScreencapResponse:
    """接收 WebADB（浏览器端）抓到的 PNG，落盘到临时目录供 finish-scan 解析。

    v0.4.x：从服务端跑 ADB subprocess 改为浏览器 WebUSB 直读。Backend 不再调 adb；
    PNG 直接由前端上传。device config / probe / test-adb 端点保留但闲鱼前端不再调。
    """
    try:
        png_bytes = await png.read()
        if not png_bytes:
            return ScreencapResponse(
                ok=False,
                error_kind="empty_png",
                error="上传的 PNG 字节为空",
            )
    except Exception as exc:  # noqa: BLE001
        return ScreencapResponse(
            ok=False,
            error_kind="upload_failed",
            error=str(exc),
        )

    state = svc.BATCH_SCREENS.setdefault(batch_id, {
        "screens": [],
        "parsed_orders": [],
        "tmp_dir": str(_batch_tmp_dir(batch_id)),
    })

    # seq 单调递增，不复用已删除的号，避免文件名冲突 + 前端 React key 错位
    existing = state["screens"]
    seq = (max((s["seq"] for s in existing), default=-1) + 1)
    tmp_dir = Path(state["tmp_dir"])
    tmp_dir.mkdir(parents=True, exist_ok=True)
    png_path = tmp_dir / f"screen_{seq}.png"
    try:
        png_path.write_bytes(png_bytes)
    except OSError as exc:
        return ScreencapResponse(
            ok=False,
            error_kind="screencap_save_failed",
            error=str(exc),
        )

    state["screens"].append({"seq": seq, "status": "captured", "error": None})
    return ScreencapResponse(
        ok=True,
        screen_id=f"{batch_id}-{seq}",
        seq=seq,
    )


@router.post("/xianyu/screen-delete")
def xianyu_screen_delete(batch_id: str = Form(...), seq: int = Form(...)) -> dict:
    """删除已截屏中某一张（finish-scan 前可用），同时删除磁盘上对应 PNG。"""
    state = svc.BATCH_SCREENS.get(batch_id)
    if state is None:
        return {"ok": False, "error_kind": "batch_not_found"}
    screens = state.get("screens") or []
    idx = next((i for i, s in enumerate(screens) if s["seq"] == seq), None)
    if idx is None:
        return {"ok": False, "error_kind": "screen_not_found"}
    screens.pop(idx)
    tmp_dir = Path(state.get("tmp_dir") or "")
    png_path = tmp_dir / f"screen_{seq}.png"
    if png_path.is_file():
        try:
            png_path.unlink()
        except OSError:
            pass  # 删失败不影响主流程
    return {"ok": True, "remaining": len(screens)}


@router.get("/xianyu/screen")
def xianyu_screen(batch_id: str, seq: int):
    """返回截屏 PNG 字节流（仅当批次仍在 BATCH_SCREENS 中时可用，finish 后清理）。"""
    from fastapi.responses import FileResponse, JSONResponse

    state = svc.BATCH_SCREENS.get(batch_id)
    if state is None:
        return JSONResponse({"ok": False, "error_kind": "batch_not_found"}, status_code=404)
    tmp_dir = Path(state.get("tmp_dir") or "")
    png_path = tmp_dir / f"screen_{seq}.png"
    if not png_path.is_file():
        return JSONResponse({"ok": False, "error_kind": "png_not_found"}, status_code=404)
    return FileResponse(png_path, media_type="image/png")


@router.get("/xianyu/scan-status", response_model=ScanStatusResponse)
def xianyu_scan_status(batch_id: str) -> ScanStatusResponse:
    state = svc.BATCH_SCREENS.get(batch_id) or {
        "screens": [],
        "parsed_orders": [],
    }
    return ScanStatusResponse(
        batch_id=batch_id,
        screens=list(state.get("screens") or []),
        parsed_orders=list(state.get("parsed_orders") or []),
    )


@router.post("/xianyu/finish-scan", response_model=ScanResponse)
async def xianyu_finish_scan(body: FinishScanRequest, db: Session = Depends(get_db)) -> ScanResponse:
    batch_id = body.batch_id
    state = svc.BATCH_SCREENS.get(batch_id)
    if state is None:
        return ScanResponse(
            ok=False,
            batch_id=batch_id,
            error_kind="batch_not_found",
            error=f"batch_id={batch_id} 未找到（未截过屏 / 已被清理）",
        )

    # 1. 顺序解析所有已截屏 PNG（顺序跑避免 DashScope 限流）
    tmp_dir = Path(state["tmp_dir"])
    parsed_orders: list[dict] = []
    screens = state["screens"]
    for screen in screens:
        seq = screen["seq"]
        png_path = tmp_dir / f"screen_{seq}.png"
        if not png_path.exists():
            screen["status"] = "failed"
            screen["error"] = "PNG 文件不存在（uvicorn 可能在截屏后重启过）"
            continue
        screen["status"] = "parsing"
        try:
            png_bytes = png_path.read_bytes()
            parsed = await asyncio.to_thread(
                auto_import_llm.parse_xianyu_screenshot, png_bytes
            )
            screen["status"] = "parsed"
            for ord_dict in parsed or []:
                parsed_orders.append(ord_dict)
        except LLMProviderError as exc:
            screen["status"] = "failed"
            screen["error"] = f"{exc.error_kind}: {exc.message}"
        except Exception as exc:  # noqa: BLE001
            screen["status"] = "failed"
            screen["error"] = str(exc)
    state["parsed_orders"] = parsed_orders

    # 1b. 如果所有截屏都失败，把第一个错误抛回前端
    failed = [s for s in screens if s["status"] == "failed"]
    if screens and len(failed) == len(screens):
        first_err = failed[0].get("error") or "未知错误"
        _cleanup_batch(batch_id)
        return ScanResponse(
            ok=False,
            batch_id=batch_id,
            error_kind="parse_all_failed",
            error=f"{len(failed)}/{len(screens)} 张截屏解析失败：{first_err}",
        )

    # 2. 去重（按 external_order_id）
    seen: dict[str, dict] = {}
    for parsed in parsed_orders:
        ext_id = (parsed.get("external_order_id") or "").strip()
        if not ext_id or ext_id in seen:
            continue
        seen[ext_id] = parsed
    raw_orders = list(seen.values())

    # 3. 跑统一扫描流水线
    response = _scan_impl(
        db=db,
        batch_id=batch_id,
        raw_orders=raw_orders,
        platform="xianyu",
    )

    # 4. 不清理 — 截图保留供 preview 页校对；commit/cancel 时清

    return response


@router.post("/cancel-scan")
async def cancel_scan(body: CancelScanRequest) -> dict:
    batch_id = body.batch_id
    tasks = svc.BATCH_TASKS.get(batch_id) or []
    for t in tasks:
        if not t.done():
            t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _cleanup_batch(batch_id)
    return {"ok": True}


def _cleanup_batch(batch_id: str) -> None:
    state = svc.BATCH_SCREENS.pop(batch_id, None)
    svc.BATCH_TASKS.pop(batch_id, None)
    if not state:
        return
    tmp_dir = state.get("tmp_dir")
    if not tmp_dir:
        return
    p = Path(tmp_dir)
    if not p.exists():
        return
    try:
        for f in p.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        p.rmdir()
    except OSError:
        pass


# ============================================================
# CUJ-3 SKU 搜索 + Commit
# ============================================================

@router.post("/sku-search", response_model=SkuSearchResponse)
def sku_search(body: SkuSearchRequest, db: Session = Depends(get_db)) -> SkuSearchResponse:
    try:
        raw_hits = svc.search_skus(db, body.q, body.limit)
        hits = [SkuSearchHit(**h) for h in raw_hits]
        return SkuSearchResponse(ok=True, hits=hits)
    except Exception as exc:  # noqa: BLE001
        return SkuSearchResponse(
            ok=False,
            error_kind="sku_search_failed",
            error=str(exc),
        )


@router.post("/commit", response_model=CommitResponse)
def commit(body: CommitRequest, db: Session = Depends(get_db)) -> CommitResponse:
    """单事务批量落库：要么全成功，要么全回滚。

    - SKU 校验失败 → 整批回滚 + 返回 commit_sku_not_found
    - override_duplicate=True → 给 external_order_id 追加 `-redoN` 后缀
    - 未 override 的重复单 → 静默跳过（计入 dup_skipped 统计）
    """
    t0 = time.monotonic()
    created_ids: list[int] = []
    new_count = 0
    dup_skipped = 0
    manual_skipped = 0  # 由前端 surface；commit 端点本身不知道未勾选的订单
    high_conf_count = 0
    total_matches = 0

    try:
        # 单事务：成功 → db.commit；任何异常 → db.rollback 整批回滚。
        # Step 1: 校验所有 SKU 都存在
        sku_codes = {p.sku for item in body.items for p in item.products}
        present_rows = (
            db.query(Product).filter(Product.sku.in_(sku_codes)).all() if sku_codes else []
        )
        present = {row.sku for row in present_rows}
        missing = sku_codes - present
        if missing:
            raise _SkuNotFoundError(
                f"以下 SKU 不存在于 catalog：{', '.join(sorted(missing))}"
            )
        sku_to_pid = {row.sku: row.id for row in present_rows}

        # Step 2: 逐单写入
        for item in body.items:
            base_ext_id = item.external_order_id
            use_ext_id = base_ext_id

            if item.override_duplicate:
                use_ext_id = svc._next_redo_suffix(db, item.platform, base_ext_id)
            else:
                # 重复兜底：DB 已有同 (platform, external_order_id) 静默跳过
                existing = (
                    db.query(Order)
                    .filter(
                        Order.platform == item.platform,
                        Order.external_order_id == base_ext_id,
                    )
                    .first()
                )
                if existing:
                    dup_skipped += 1
                    continue

            created_at_dt = None
            if item.external_created_at:
                try:
                    created_at_dt = datetime.fromisoformat(item.external_created_at)
                except ValueError:
                    created_at_dt = None

            order = Order(
                status="pending",
                platform=item.platform,
                external_order_id=use_ext_id,
                buyer_nickname=item.buyer_nickname,
                external_created_at=created_at_dt,
                recipient_name=item.recipient_name,
                recipient_phone=item.recipient_phone,
                recipient_address=item.recipient_address,
            )
            db.add(order)
            db.flush()
            for p in item.products:
                db.add(OrderItem(
                    order_id=order.id,
                    product_id=sku_to_pid[p.sku],
                    quantity=p.quantity,
                ))
                total_matches += 1
                high_conf_count += 1  # 走到 commit 的都视为高（手选 / LLM 高置信度）
            created_ids.append(order.id)
            new_count += 1

        db.commit()
    except _SkuNotFoundError as exc:
        db.rollback()
        return CommitResponse(
            ok=False,
            error_kind="commit_sku_not_found",
            error=str(exc),
            total_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return CommitResponse(
            ok=False,
            error_kind="commit_failed",
            error=str(exc),
            total_ms=int((time.monotonic() - t0) * 1000),
        )

    # 提交成功 — 把对应批次的截屏/状态清掉
    _cleanup_batch(body.batch_id)

    match_rate = (high_conf_count / total_matches) if total_matches else 0.0
    return CommitResponse(
        ok=True,
        stats=CommitStats(
            new=new_count,
            dup_skipped=dup_skipped,
            manual_skipped=manual_skipped,
            match_rate=round(match_rate, 4),
        ),
        created_order_ids=created_ids,
        total_ms=int((time.monotonic() - t0) * 1000),
    )


# ============================================================
# CUJ-4 配置
# ============================================================

@router.get("/xianyu/config", response_model=AdbConfig)
def xianyu_get_config(db: Session = Depends(get_db)) -> AdbConfig:
    return svc.get_adb_config(db)


@router.put("/xianyu/config")
def xianyu_put_config(body: AdbConfig, db: Session = Depends(get_db)) -> dict:
    try:
        svc.set_adb_config(db, body)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error_kind": "set_config_failed", "error": str(exc)}


@router.post("/xianyu/test-adb", response_model=TestAdbResponse)
def xianyu_test_adb(body: AdbConfig) -> TestAdbResponse:
    try:
        diagnostics = svc.diagnose_adb(body)
        client = svc.AdbClient()
        devices = client.list_devices()
        # True only when the configured endpoint's device passed device_state.
        connected = any(
            d.get("ok") for d in diagnostics if d.get("name") == "device_state"
        )
        serial = _pick_device_serial(body, devices)
        matched = next(
            (d for d in devices if isinstance(d, dict) and d.get("serial") == serial),
            None,
        )
        android_version = matched.get("android_version") if matched else None
        return TestAdbResponse(
            ok=True,
            adb_connected=connected,
            device_serial=serial,
            android_version=android_version,
            diagnostics=diagnostics,
        )
    except Exception as exc:  # noqa: BLE001
        return TestAdbResponse(
            ok=False,
            adb_connected=False,
            error_kind="test_adb_failed",
            error=str(exc),
        )
