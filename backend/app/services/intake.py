"""产品录入（intake）服务层

包含：
- 启发式分类器（assembly / produce）— 不调 LLM
- 上传文件落盘 + 过期会话清理（TTL）

完整契约见 docs/prd/prd-005-intake.md CUJ-1。
"""

from __future__ import annotations

import io
import shutil
import time
from pathlib import Path
from typing import Literal

from PIL import Image

# 临时文件目录（与运行目录相对；上传的图片落到 data/intake_tmp/<session_id>/<image_id>.<suffix>）
INTAKE_TMP_DIR = Path("data/intake_tmp")

# 会话过期时间（秒）— 上传后 1 小时未合并即清理
TTL_SECONDS = 3600

# 启发式分类：右上面板亮度阈值 — 均值 < 80（暗）认为是切片软件的耗材面板 → produce
PRODUCE_PANEL_LUMINANCE_THRESHOLD = 80

# 启发式分类：右上面板裁切比例（left, top, right, bottom），相对图片宽高
PRODUCE_PANEL_REGION = (0.72, 0.02, 0.98, 0.30)

# 单文件最大上传字节
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 允许的 MIME 类型
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}


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
        # ImageStat.Stat.mean[0] 是 region 灰度均值；避免 getdata() 在 Pillow 14 移除
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
    session_dir = INTAKE_TMP_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{image_id}.{suffix}"
    path.write_bytes(content)
    return path


def cleanup_session(session_id: str) -> None:
    """删除 data/intake_tmp/<session_id>/ 整个子目录；不存在或删除失败均静默。"""
    session_dir = INTAKE_TMP_DIR / session_id
    try:
        shutil.rmtree(session_dir)
    except (OSError, FileNotFoundError):
        return
