"""产品录入（intake）服务层

包含：
- 启发式分类器（assembly / produce）— 不调 LLM
- 上传文件落盘 + 过期会话清理（TTL）
- 撞名检测（与现有 catalog 中 Component / PrintConfig / Product 比对）
- 读取 session 图片字节
- merge 5 阶段事务（备份 + append + reload + 失败回滚）+ recent-logs ring buffer

完整契约见 docs/prd/prd-005-intake.md CUJ-1 / CUJ-2 / CUJ-5，
设计文档见 docs/design/design-intake.md §3、§6、§7、§8。
"""

from __future__ import annotations

import collections
import io
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import yaml
from PIL import Image
from sqlalchemy.orm import Session

from ..models import Component, PrintConfig, Product
from ..schemas_intake import Conflict, FinalDraft


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

# session_id / image_id 必须是 uuid4 hex（仅 [0-9a-f]，长度 == 32）。
# 用作文件系统路径片段前必须校验，防止 `..` / `/` 等路径遍历输入。
_HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _is_safe_id(value: str) -> bool:
    """校验 session_id / image_id 是否为合法 uuid4 hex（32 位小写十六进制）。"""
    return isinstance(value, str) and bool(_HEX_ID_RE.match(value))


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
    session_id / image_id 必须先经 `_is_safe_id` 校验（路由层负责），此处再次断言以防路径遍历。
    """
    if not _is_safe_id(session_id) or not _is_safe_id(image_id):
        raise ValueError("invalid session_id / image_id")
    session_dir = _session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{image_id}.{suffix}"
    path.write_bytes(content)
    return path


def cleanup_session(session_id: str) -> None:
    """删除 data/intake_tmp/<session_id>/ 整个子目录；不存在或删除失败均静默。

    非法 session_id 直接静默忽略（不删任何东西，杜绝路径遍历）。
    """
    if not _is_safe_id(session_id):
        return
    session_dir = _session_dir(session_id)
    try:
        shutil.rmtree(session_dir)
    except (OSError, FileNotFoundError):
        return


def load_session_images(session_id: str, image_ids: list[str]) -> Optional[list[bytes]]:
    """按 image_ids 顺序读 session 目录下的图片 bytes。

    任一 image_id 文件不存在 → 返回 None（路由层映射到 session_expired）。
    非法 session_id 或 image_id（非 uuid4 hex）也返回 None — 不读任何文件，杜绝路径遍历。
    """
    if not _is_safe_id(session_id):
        return None
    sd = _session_dir(session_id)
    if not sd.is_dir():
        return None
    result: list[bytes] = []
    for img_id in image_ids:
        if not _is_safe_id(img_id):
            return None
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


# ---------- recent-logs ring buffer（CUJ-5 失败页查看后端日志） ----------

# 进程级环形缓冲（设计 §6 后半段简化方案）— 同 stdout 双写
_RECENT_LOGS: collections.deque[str] = collections.deque(maxlen=500)


def intake_log(msg: str) -> None:
    """同时 print 到 stdout 与 append 进 ring buffer，供 /recent-logs 拉取。"""
    print(msg)
    _RECENT_LOGS.append(msg)


def get_recent_logs(lines: int = 100) -> list[str]:
    """返回最近 N 条 intake_log 记录（按调用顺序）。"""
    if lines <= 0:
        return []
    buf = list(_RECENT_LOGS)
    return buf[-lines:]


# ---------- 颜色矩阵 → YAML 展开（设计 §8） ----------

def expand_to_yaml_structures(
    final_draft: FinalDraft,
) -> tuple[list[dict], list[dict], list[dict]]:
    """把 FinalDraft 展开为 catalog YAML 三段结构 (组件列表, 打印盘列表, 产品列表)。

    - 组件列表：每条 `{"名称": comp.name, "可选颜色": [...union of colors across variants...]}`，
      无可选颜色时不输出 `可选颜色` 字段。
    - 打印盘列表：每条 `{"盘号", "组件", "数量", "耗时分钟"}`，无 `颜色` 字段。
    - 产品列表：每个变体一条 `{"名称": v.variant_name, "BOM": [{"组件", "颜色", "数量"}, ...]}`。
    """
    # 组件 → union(dedupe but keep first-seen order) of colors across all variants
    comp_to_colors: dict[str, list[str]] = {}
    for comp in final_draft.components:
        comp_to_colors[comp.name] = []
    for variant in final_draft.variants:
        for cell in variant.color_cells:
            if cell.component_name not in comp_to_colors:
                comp_to_colors[cell.component_name] = []
            if cell.color and cell.color not in comp_to_colors[cell.component_name]:
                comp_to_colors[cell.component_name].append(cell.color)

    components_out: list[dict] = []
    for comp in final_draft.components:
        entry: dict = {"名称": comp.name}
        colors = comp_to_colors.get(comp.name, [])
        if colors:
            entry["可选颜色"] = colors
        components_out.append(entry)

    plates_out: list[dict] = []
    for p in final_draft.plates:
        plates_out.append({
            "盘号": p.plate_name,
            "组件": p.component_name,
            "数量": p.quantity_per_plate,
            "耗时分钟": p.duration_minutes,
        })

    # 各组件的装配数量（每套产品所需件数）
    comp_to_qty: dict[str, int] = {
        c.name: c.assembly_quantity for c in final_draft.components
    }

    products_out: list[dict] = []
    for variant in final_draft.variants:
        bom = []
        for cell in variant.color_cells:
            bom.append({
                "组件": cell.component_name,
                "颜色": cell.color,
                "数量": comp_to_qty.get(cell.component_name, 1),
            })
        products_out.append({
            "名称": variant.variant_name,
            "BOM": bom,
        })

    return components_out, plates_out, products_out


# ---------- 备份 / append / 回滚 ----------

def backup_catalog(catalog_path: Path, timestamp: str) -> Path:
    """把 catalog.yaml 复制到 <catalog_path>.bak.<timestamp>，返回备份路径。"""
    backup_path = catalog_path.with_name(f"{catalog_path.name}.bak.{timestamp}")
    shutil.copy2(catalog_path, backup_path)
    return backup_path


def append_to_catalog(
    catalog_path: Path,
    new_components: list[dict],
    new_plates: list[dict],
    new_products: list[dict],
) -> None:
    """把 new_* append 到 catalog.yaml 末尾，保持中文键 + 文件 YAML 合法。

    现有条目原样保留，新条目追加在各自段末尾。
    """
    if catalog_path.exists():
        raw = catalog_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) if raw.strip() else {}
        if data is None:
            data = {}
    else:
        data = {}

    if not isinstance(data, dict):
        data = {}

    data.setdefault("组件", []).extend(new_components)
    data.setdefault("打印盘", []).extend(new_plates)
    data.setdefault("产品", []).extend(new_products)

    dumped = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
        indent=2,
    )
    catalog_path.write_text(dumped, encoding="utf-8")


def rollback_from_backup(catalog_path: Path, backup_path: Path) -> None:
    """从 bak 恢复 catalog.yaml；不删 bak 文件（保留作为审计）。"""
    shutil.copy2(backup_path, catalog_path)


# ---------- merge 5 阶段事务（CUJ-5 / 设计 §7） ----------

def do_merge(
    db: Session,
    final_draft: FinalDraft,
    catalog_path: Path,
    session_id: str,
) -> dict:
    """5 阶段流水：撞名兜底 → 备份 → append + 复读验证 → load_catalog → 清理 + 返回。

    任一步失败：撞名（无备份）/ backup_failed（无备份）→ 直接返回；
    write_failed / yaml_invalid / load_failed → 从 bak 回滚 catalog.yaml 后返回。
    """
    # ---- 阶段 1：撞名兜底 ----
    component_names = [c.name for c in final_draft.components]
    plate_names = [p.plate_name for p in final_draft.plates]
    product_names = [v.variant_name for v in final_draft.variants]
    conflicts = detect_conflicts(db, component_names, plate_names, product_names)
    if conflicts:
        intake_log(f"merge 撞名拦截：{len(conflicts)} 项冲突")
        return {
            "ok": False,
            "error_kind": "conflict",
            "error": f"检测到 {len(conflicts)} 项撞名冲突",
            "rolled_back": False,
            "details": [c.model_dump() for c in conflicts],
        }

    # ---- 阶段 2：备份 ----
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        backup_path = backup_catalog(catalog_path, timestamp)
    except OSError as exc:
        intake_log(f"备份失败：{exc!r}")
        return {
            "ok": False,
            "error_kind": "backup_failed",
            "error": str(exc),
            "rolled_back": False,
        }
    intake_log(f"备份创建：{backup_path}")

    # ---- 阶段 3：append + 复读验证 ----
    write_t0 = time.perf_counter()
    try:
        new_components, new_plates, new_products = expand_to_yaml_structures(final_draft)
        append_to_catalog(catalog_path, new_components, new_plates, new_products)
    except (OSError, yaml.YAMLError) as exc:
        intake_log(f"append 写入失败，rollback 触发：原因 = {exc!r}")
        try:
            rollback_from_backup(catalog_path, backup_path)
        except OSError as roll_exc:
            intake_log(f"回滚失败（数据可能不一致）：{roll_exc!r}")
            return {
                "ok": False,
                "error_kind": "write_failed",
                "error": str(exc),
                "rolled_back": False,
                "backup_path": str(backup_path),
            }
        return {
            "ok": False,
            "error_kind": "write_failed",
            "error": str(exc),
            "rolled_back": True,
            "backup_path": str(backup_path),
        }

    # 复读验证 — append 完后整文件应仍是合法 YAML
    try:
        yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
        intake_log(f"yaml 复读校验失败，rollback 触发：原因 = {exc!r}")
        try:
            rollback_from_backup(catalog_path, backup_path)
            rolled_back = True
        except OSError:
            rolled_back = False
        return {
            "ok": False,
            "error_kind": "yaml_invalid",
            "error": str(exc),
            "rolled_back": rolled_back,
            "backup_path": str(backup_path),
        }
    write_ms = int((time.perf_counter() - write_t0) * 1000)
    intake_log("append 写入完成")

    # ---- 阶段 4：load_catalog ----
    from ..database import SessionLocal
    from .catalog import load_catalog

    reload_t0 = time.perf_counter()
    new_session = SessionLocal()
    try:
        try:
            load_catalog(new_session)
        except Exception as exc:  # noqa: BLE001 — load_catalog 实现可能抛多种异常
            intake_log(f"load_catalog 失败，rollback 触发：原因 = {exc!r}")
            try:
                rollback_from_backup(catalog_path, backup_path)
                rolled_back = True
            except OSError:
                rolled_back = False
            return {
                "ok": False,
                "error_kind": "load_failed",
                "error": str(exc),
                "rolled_back": rolled_back,
                "backup_path": str(backup_path),
            }
    finally:
        new_session.close()
    reload_ms = int((time.perf_counter() - reload_t0) * 1000)
    intake_log("load_catalog 成功")

    # ---- 阶段 5：清理 session tmp + 返回成功 ----
    cleanup_session(session_id)
    return {
        "ok": True,
        "stats": {
            "components_added": len(new_components),
            "plates_added": len(new_plates),
            "products_added": len(new_products),
        },
        "backup_path": str(backup_path),
        "timing_ms": {
            "写入": write_ms,
            "重新加载": reload_ms,
        },
    }
