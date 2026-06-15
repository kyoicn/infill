"""产品录入（intake）路由

完整契约见 docs/prd/prd-005-intake.md（CUJ-1 上传 + 分类、CUJ-2 LLM 识别 + 撞名检测、
CUJ-5 合并到 catalog.yaml + 失败回滚 + recent-logs）。
"""

from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas_intake import MergeRequest, RecognizeRequest, UploadedImage
from app.services.intake import (
    ALLOWED_MIME,
    INTAKE_TMP_DIR,
    MAX_UPLOAD_BYTES,
    _is_safe_id,
    cleanup_stale_sessions,
    detect_conflicts,
    do_merge,
    get_recent_logs,
    heuristic_classify,
    load_session_images,
    save_uploaded_image,
)
from app.services.intake_llm import LLMProviderError, get_active_provider

router = APIRouter(prefix="/api/intake", tags=["产品录入"])


_MIME_TO_SUFFIX = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
_SUFFIX_TO_MIME = {v: k for k, v in _MIME_TO_SUFFIX.items()}


@router.get("/provider-status")
def provider_status():
    provider = get_active_provider()
    return {
        "ok": True,
        "provider_name": provider.name if provider else None,
        "configured": provider is not None,
    }


@router.get("/session-image/{session_id}/{image_id}")
def session_image(session_id: str, image_id: str):
    """返回上传期临时存放的原图。

    路径安全：session_id / image_id 必须是 uuid4 hex（32 位小写十六进制），
    否则直接 404 — 杜绝路径遍历（如 `../../etc/passwd`）。
    用于校对页（Draft）「原图复核」抽屉的真实图像显示。
    """
    if not _is_safe_id(session_id) or not _is_safe_id(image_id):
        raise HTTPException(status_code=404, detail="not found")

    session_dir = INTAKE_TMP_DIR / session_id
    for suffix in ("png", "jpg", "webp"):
        path = session_dir / f"{image_id}.{suffix}"
        if path.is_file():
            return FileResponse(
                path,
                media_type=_SUFFIX_TO_MIME[suffix],
                headers={"Cache-Control": "private, max-age=3600"},
            )
    raise HTTPException(status_code=404, detail="image not found or session expired")


@router.post("/upload")
async def upload(
    files: list[UploadFile] = File(...),
    session_id: str | None = Form(None),
):
    cleanup_stale_sessions()

    if not session_id:
        session_id = uuid.uuid4().hex
    elif not _is_safe_id(session_id):
        # 拒绝非 uuid4 hex 的 session_id —— 防止路径遍历（`../foo`）写到 intake_tmp 之外
        return {
            "ok": False,
            "session_id": None,
            "images": [],
            "error_kind": "invalid_session_id",
            "error": f"非法 session_id: {session_id!r}",
        }

    images: list[dict] = []
    for file in files:
        if file.content_type not in ALLOWED_MIME:
            return {
                "ok": False,
                "session_id": session_id,
                "images": [],
                "error_kind": "invalid_mime",
                "error": f"不支持的文件类型: {file.content_type}",
            }

        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return {
                "ok": False,
                "session_id": session_id,
                "images": [],
                "error_kind": "too_large",
                "error": (
                    f"文件 {file.filename} 超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 限制"
                ),
            }

        image_id = uuid.uuid4().hex
        suffix = _MIME_TO_SUFFIX[file.content_type]
        save_uploaded_image(session_id, image_id, suffix, content)

        suggested = heuristic_classify(content)
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size

        images.append(
            UploadedImage(
                image_id=image_id,
                filename=file.filename or f"{image_id}.{suffix}",
                suggested_class=suggested,
                width=width,
                height=height,
            ).model_dump()
        )

    return {
        "ok": True,
        "session_id": session_id,
        "images": images,
    }


@router.post("/recognize")
def recognize(req: RecognizeRequest, db: Session = Depends(get_db)):
    """调 LLM 识别 → 转 Draft → 检测撞名 → 返回。

    错误分支统一返回 `{ok: False, error_kind, error}`（HTTP 200，前端按 error_kind 分发）。
    """
    provider = get_active_provider()
    if provider is None:
        return {
            "ok": False,
            "error_kind": "no_api_key",
            "error": "未检测到 LLM 提供商 API key，请在 .env 配置 LLM_PROVIDER 及对应 <PROVIDER>_API_KEY",
        }

    # 读 session 图片
    assembly_bytes = load_session_images(req.session_id, req.assembly_image_ids)
    produce_bytes = load_session_images(req.session_id, req.produce_image_ids)
    if assembly_bytes is None or produce_bytes is None:
        return {
            "ok": False,
            "error_kind": "session_expired",
            "error": f"会话 {req.session_id} 已过期或部分图片不存在，请回到上一步重新上传",
        }

    # 调 LLM
    try:
        llm_result = provider.recognize(
            assembly_images=assembly_bytes,
            produce_images=produce_bytes,
            product_base_name=req.product_base_name,
        )
    except LLMProviderError as exc:
        resp: dict = {
            "ok": False,
            "error_kind": exc.error_kind,
            "error": exc.message,
        }
        if exc.raw_preview:
            resp["raw_response_preview"] = exc.raw_preview
        return resp

    # 决定产品基名：用户预填优先，LLM 推断兜底
    product_base_name = (
        req.product_base_name
        or llm_result.get("product_base_name")
        or ""
    )
    if not product_base_name:
        # 安全兜底（实际几乎不会触发，prompt 已强制 LLM 输出）
        product_base_name = "未命名产品"

    # 转换为前端 Draft schema：拼前缀、生成默认盘号、反查 source_image_id
    components_out: list[dict] = []
    short_to_full: dict[str, str] = {}
    for comp in llm_result.get("components", []):
        short_name = comp["name"]
        full_name = f"{product_base_name}-{short_name}"
        short_to_full[short_name] = full_name
        components_out.append({
            "name": full_name,
            "assembly_quantity": int(comp["bom_quantity"]),
        })

    plates_out: list[dict] = []
    for plate in llm_result.get("plates", []):
        short_cn = plate["component_name"]
        full_cn = short_to_full.get(short_cn, f"{product_base_name}-{short_cn}")
        qpp = int(plate["quantity_per_plate"])
        plate_name = f"{full_cn}-{qpp}"
        si = int(plate["source_index"])
        # 反查 source_image_id：source_index 是 produce_image_ids 数组下标
        if 0 <= si < len(req.produce_image_ids):
            source_image_id = req.produce_image_ids[si]
        else:
            source_image_id = ""
        plates_out.append({
            "plate_name": plate_name,
            "component_name": full_cn,
            "quantity_per_plate": qpp,
            "duration_minutes": int(plate["duration_minutes"]),
            "source_image_id": source_image_id,
        })

    # 撞名检测：组件名 + 盘号（产品名等 CUJ-5 颜色矩阵展开后才查）
    component_names = [c["name"] for c in components_out]
    plate_names = [p["plate_name"] for p in plates_out]
    conflicts = detect_conflicts(db, component_names, plate_names, product_names=[])

    return {
        "ok": True,
        "draft": {
            "product_base_name": product_base_name,
            "components": components_out,
            "plates": plates_out,
        },
        "conflicts": [c.model_dump() for c in conflicts],
    }


@router.post("/merge")
def merge(req: MergeRequest, db: Session = Depends(get_db)):
    """5 阶段事务：撞名兜底 → 备份 → append + 复读 → load_catalog → 清理 + 返回。

    错误统一 HTTP 200 + `{ok: False, error_kind, error, rolled_back, ...}`。
    """
    # 重新解析 CATALOG_PATH（services.catalog 模块属性可能在测试中被 monkeypatch）
    from app.services import catalog as catalog_module

    return do_merge(
        db=db,
        final_draft=req.final_draft,
        catalog_path=catalog_module.CATALOG_PATH,
        session_id=req.session_id,
    )


@router.get("/recent-logs")
def recent_logs(lines: int = 100):
    """返回最近 N 条 intake_log 记录（CUJ-5 失败页『查看后端日志』）。

    响应 schema 见 design-intake.md §1：`{ok: true, logs: "<line1>\\n<line2>\\n..."}`，
    前端 textarea 直接展示，无需 join 一次。
    """
    buf = get_recent_logs(lines=lines)
    return {
        "ok": True,
        "logs": "\n".join(buf),
    }
