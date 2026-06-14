"""产品录入（intake）服务层

包含：
- 启发式分类器（assembly / produce）— 不调 LLM
- 上传文件落盘 + 过期会话清理（TTL）
- 撞名检测（与现有 catalog 中 Component / PrintConfig / Product 比对）
- 读取 session 图片字节

完整契约见 docs/prd/prd-005-intake.md CUJ-1 / CUJ-2，
设计文档见 docs/design/design-intake.md §3、§6、§7。
"""

from __future__ import annotations

import io
import os
import shutil
import time
from pathlib import Path
from typing import Literal, Optional

from PIL import Image
from sqlalchemy.orm import Session

from ..models import Component, PrintConfig, Product
from ..schemas_intake import Conflict


# ---------- 文件系统路径 ----------

# 默认临时目录：项目根的 data/intake_tmp（绝对路径，cwd 无关）
_DEFAULT_TMP_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "intake_tmp"
INTAKE_TMP_DIR = Path(os.environ.get("INTAKE_TMP_DIR", str(_DEFAULT_TMP_DIR)))

# 会话过期时间（秒）— 上传后 1 小时未合并即清理
TTL_SECONDS = 3600

# 启发式分类：右上面板亮度阈值 — 均值 < 140（暗）认为是切片软件的耗材面板 → produce
# 真实样例校准：produce 截图区域均值 ~80-85，assembly 截图区域均值 ~190-200
# 140 是安全中点，对 1700×1800 拓竹截图稳定区分
PRODUCE_PANEL_LUMINANCE_THRESHOLD = 140

# 启发式分类：右上面板裁切比例（left, top, right, bottom），相对图片宽高
PRODUCE_PANEL_REGION = (0.72, 0.02, 0.98, 0.30)

# 单文件最大上传字节
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 允许的 MIME 类型
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}


def _session_dir(session_id: str) -> Path:
    return INTAKE_TMP_DIR / session_id


# ---------- 启发式分类 + 落盘 + TTL 清理（CUJ-1） ----------

def heuristic_classify(image_bytes: bytes) -> Literal["assembly", "produce"]:
    """启发式判定截图是 assembly（组装图）还是 produce（打印盘）。

    规则：拓竹切片软件的打印盘截图右上区域含暗色耗材面板（耗材色块列 + 总时间面板）。
    取右上区域 (0.72~0.98 宽, 0.02~0.30 高) 灰度均值，< 80 → produce，否则 assembly。
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        gray = img.convert("L")
        w, h = gray.size
        left = int(w * PRODUCE_PANEL_REGION[0])
        top = int(h * PRODUCE_PANEL_REGION[1])
        right = int(w * PRODUCE_PANEL_REGION[2])
        bottom = int(h * PRODUCE_PANEL_REGION[3])
        region = gray.crop((left, top, right, bottom))
        from PIL import ImageStat

        if region.size[0] == 0 or region.size[1] == 0:
            return "assembly"
        mean = ImageStat.Stat(region).mean[0]
    if mean < PRODUCE_PANEL_LUMINANCE_THRESHOLD:
        return "produce"
    return "assembly"


def cleanup_stale_sessions(now: float | None = None) -> int:
    """扫描 INTAKE_TMP_DIR 子目录，删除 mtime + TTL 已过期的会话目录。

    返回成功清理的子目录数。容错：目录不存在视为 0；单个删除失败用 try/except 兜底跳过。
    """
    if now is None:
        now = time.time()
    if not INTAKE_TMP_DIR.exists():
        return 0
    cleaned = 0
    try:
        children = list(INTAKE_TMP_DIR.iterdir())
    except OSError:
        return 0
    for child in children:
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime + TTL_SECONDS < now:
            try:
                shutil.rmtree(child)
                cleaned += 1
            except OSError:
                continue
    return cleaned


def save_uploaded_image(session_id: str, image_id: str, suffix: str, content: bytes) -> Path:
    """把上传字节写到 data/intake_tmp/<session_id>/<image_id>.<suffix>，返回该路径。

    父目录用 mkdir(parents=True, exist_ok=True) 自动创建。
    """
    session_dir = _session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{image_id}.{suffix}"
    path.write_bytes(content)
    return path


def cleanup_session(session_id: str) -> None:
    """删除 data/intake_tmp/<session_id>/ 整个子目录；不存在或删除失败均静默。"""
    session_dir = _session_dir(session_id)
    try:
        shutil.rmtree(session_dir)
    except (OSError, FileNotFoundError):
        return


def load_session_images(session_id: str, image_ids: list[str]) -> Optional[list[bytes]]:
    """按 image_ids 顺序读 session 目录下的图片 bytes。

    任一 image_id 文件不存在 → 返回 None（路由层映射到 session_expired）。
    """
    sd = _session_dir(session_id)
    if not sd.is_dir():
        return None
    result: list[bytes] = []
    for img_id in image_ids:
        matches = list(sd.glob(f"{img_id}.*"))
        if not matches:
            return None
        result.append(matches[0].read_bytes())
    return result


# ---------- 撞名检测（CUJ-2 / CUJ-5） ----------

def detect_conflicts(
    db: Session,
    component_names: list[str],
    plate_names: list[str],
    product_names: list[str],
) -> list[Conflict]:
    """查询 DB Component / PrintConfig / Product 表，找出与现有同名的条目。

    返回 list[Conflict]，每条含 kind / name / existing_name；
    现有同名条目就是 DB 中已存在的同名记录（`existing_name == name`）。
    """
    conflicts: list[Conflict] = []

    if component_names:
        existing_components = (
            db.query(Component.name)
            .filter(Component.name.in_(component_names))
            .all()
        )
        existing_comp_set = {row[0] for row in existing_components}
        for name in component_names:
            if name in existing_comp_set:
                conflicts.append(Conflict(kind="component", name=name, existing_name=name))

    if plate_names:
        existing_plates = (
            db.query(PrintConfig.plate_name)
            .filter(PrintConfig.plate_name.in_(plate_names))
            .all()
        )
        existing_plate_set = {row[0] for row in existing_plates}
        for name in plate_names:
            if name in existing_plate_set:
                conflicts.append(Conflict(kind="plate", name=name, existing_name=name))

    if product_names:
        existing_products = (
            db.query(Product.name)
            .filter(Product.name.in_(product_names))
            .all()
        )
        existing_product_set = {row[0] for row in existing_products}
        for name in product_names:
            if name in existing_product_set:
                conflicts.append(Conflict(kind="product", name=name, existing_name=name))

    return conflicts
