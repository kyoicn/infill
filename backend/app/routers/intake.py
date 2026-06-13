"""产品录入（intake）路由 stub

完整契约见 docs/design/design-intake.md §1 端点契约。
本文件为 T1 阶段的骨架，5 个端点均返回 not_implemented，待后续任务填充实现。
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/intake", tags=["产品录入"])


_NOT_IMPLEMENTED = {
    "ok": False,
    "error_kind": "not_implemented",
    "error": "not implemented",
}


@router.get("/provider-status")
def provider_status():
    return _NOT_IMPLEMENTED


@router.post("/upload")
def upload():
    return _NOT_IMPLEMENTED


@router.post("/recognize")
def recognize():
    return _NOT_IMPLEMENTED


@router.post("/merge")
def merge():
    return _NOT_IMPLEMENTED


@router.get("/recent-logs")
def recent_logs(lines: int = 100):
    return _NOT_IMPLEMENTED
