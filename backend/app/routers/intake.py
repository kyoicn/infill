"""产品录入（intake）路由

完整契约见 docs/prd/prd-005-intake.md。
本任务（T3）实现 GET /provider-status 与 POST /upload；
其他端点（recognize / merge / recent-logs）由 T5 / T9 接管，仍保持 stub。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, UploadFile
from PIL import Image
import io

from app.schemas_intake import UploadedImage
from app.services.intake import (
    ALLOWED_MIME,
    MAX_UPLOAD_BYTES,
    cleanup_stale_sessions,
    heuristic_classify,
    save_uploaded_image,
)
from app.services.intake_llm import get_active_provider

router = APIRouter(prefix="/api/intake", tags=["产品录入"])


_NOT_IMPLEMENTED = {
    "ok": False,
    "error_kind": "not_implemented",
    "error": "not implemented",
}


_MIME_TO_SUFFIX = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


@router.get("/provider-status")
def provider_status():
    provider = get_active_provider()
    return {
        "ok": True,
        "provider_name": provider.name if provider else None,
        "configured": provider is not None,
    }


@router.post("/upload")
async def upload(
    files: list[UploadFile] = File(...),
    session_id: str | None = Form(None),
):
    cleanup_stale_sessions()

    if not session_id:
        session_id = uuid.uuid4().hex

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
def recognize():
    return _NOT_IMPLEMENTED


@router.post("/merge")
def merge():
    return _NOT_IMPLEMENTED


@router.get("/recent-logs")
def recent_logs(lines: int = 100):
    return _NOT_IMPLEMENTED
