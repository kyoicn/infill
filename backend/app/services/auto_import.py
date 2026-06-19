"""auto-import services — ADB + LLM SKU match + SKU search + scan/commit business logic.

Sections owned by:
- Task 2.1: ADB diagnose + config + extension status (uses adb_client.py for real subprocess work)
- Task 2.2: LLM SKU matching & 闲鱼 screenshot parsing (lives in auto_import_llm.py)
            + search_skus + load_catalog_for_llm here
- Task 2.3: BATCH_SCREENS / BATCH_TASKS / _next_redo_suffix for scan/commit orchestration
"""
from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import tempfile

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Product, SystemConfig
from .adb_client import AdbClient as RealAdbClient
from .adb_client import AdbError


# ==== ADB + Extension Status (Task 2.1) ====

DEFAULT_PORTS: dict[str, int] = {
    "mumu": 7555,
    "bluestacks": 5555,
    "ldplayer": 5555,
    "usb": 5037,
}

_CFG_KEYS = {
    "device_type": "auto_import_adb_device_type",
    "pc_ip": "auto_import_adb_pc_ip",
    "port": "auto_import_adb_port",
}

_CFG_DEFAULTS = {
    "device_type": "mumu",
    "pc_ip": "127.0.0.1",
    "port": 7555,
}


def _ping(host: str, timeout_s: int = 2) -> bool:
    """Return True if `host` answers a single ICMP echo within `timeout_s`."""
    if not host:
        return False
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout_s), host],
            capture_output=True,
            timeout=timeout_s + 2,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _tcp_open(host: str, port: int, timeout_s: float = 2.0) -> bool:
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except (OSError, socket.timeout):
        return False


def _normalize_adb_args(arg1, arg2=None, arg3=None) -> tuple[str, str, int]:
    """Accept either (AdbConfig,) or (device_type, pc_ip, port)."""
    if arg2 is None and arg3 is None and hasattr(arg1, "device_type"):
        return arg1.device_type, arg1.pc_ip, arg1.port
    return arg1, arg2 or "", int(arg3 or 0)


def diagnose_adb(arg1, arg2=None, arg3=None) -> list[dict]:
    """Return 4 diagnostic checks. Accepts AdbConfig OR (device_type, pc_ip, port)."""
    device_type, pc_ip, port = _normalize_adb_args(arg1, arg2, arg3)

    diagnostics: list[dict] = []
    client = RealAdbClient()

    installed = client.is_installed()
    diagnostics.append({
        "name": "adb_installed",
        "label": "ADB 可执行文件已安装",
        "ok": installed,
        "hint": None if installed else "未在 PATH 找到 adb，请安装 platform-tools 或设置 ADB_PATH 环境变量。",
        "detail": "" if installed else "binary missing",
    })

    pingable = _ping(pc_ip) if pc_ip else False
    diagnostics.append({
        "name": "ping",
        "label": f"PC 主机 {pc_ip or '(未设置)'} 可达",
        "ok": pingable,
        "hint": None if pingable else "无法 ping 通该 IP，请检查模拟器/USB 主机的 IP 设置和防火墙。",
        "detail": "" if pingable else "ping failed",
    })

    port_open = _tcp_open(pc_ip, port) if pc_ip and port else False
    diagnostics.append({
        "name": "tcp_port",
        "label": f"TCP 端口 {port} 已打开",
        "ok": port_open,
        "hint": None if port_open else f"无法连接 {pc_ip}:{port}，请确认 ADB 服务监听端口，或在 {device_type} 中开启调试。",
        "detail": "" if port_open else "port closed",
    })

    device_ok = False
    if installed and pc_ip and port:
        endpoint = f"{pc_ip}:{port}"
        try:
            client.connect(endpoint)
            devices = client.list_devices()
            for d in devices:
                if d.serial.startswith(pc_ip) or d.serial == endpoint:
                    device_ok = (d.state == "device")
                    break
            if not device_ok and devices and device_type == "usb":
                device_ok = any(d.state == "device" for d in devices)
        except AdbError:
            device_ok = False
    diagnostics.append({
        "name": "device_state",
        "label": "设备处于 device 状态（已授权）",
        "ok": device_ok,
        "hint": None if device_ok else "设备未授权或处于 offline / unauthorized 状态，请在模拟器/手机上点击允许调试。",
        "detail": "" if device_ok else "device offline",
    })

    return diagnostics


class AdbClient:
    """Router-facing wrapper. Instantiable with no args; uses adb_client.py for actual subprocess work.

    The test suite tests `services.adb_client.AdbClient` (real) directly. This wrapper just
    bridges the router's preferred signature (`list_devices() -> list[dict]`, `screencap(config) -> bytes`).
    """

    def __init__(self) -> None:
        self._real = RealAdbClient()

    def list_devices(self) -> list[dict]:
        try:
            devices = self._real.list_devices()
        except AdbError:
            return []
        return [
            {
                "serial": d.serial,
                "state": d.state,
                "android_version": d.properties.get("release") or d.properties.get("product"),
            }
            for d in devices
        ]

    def screencap(self, config) -> bytes:
        """Take one screenshot. For network emulators (mumu/bluestacks/ldplayer),
        `adb connect host:port` first and use `host:port` as the serial. For USB,
        the device's real hardware serial (not host:port) must be passed to `adb -s`.
        """
        device_type = getattr(config, "device_type", "")

        if device_type == "usb":
            # USB: find the first online USB device (state == "device").
            try:
                devices = self._real.list_devices()
            except AdbError:
                devices = []
            usb_devices = [
                d for d in devices
                if d.state == "device" and ":" not in d.serial
            ]
            if not usb_devices:
                raise AdbError(
                    "offline",
                    "未发现已授权的 USB 真机。请检查 USB 线、开启「USB 调试」、并在手机上点「允许」。",
                )
            serial = usb_devices[0].serial
        else:
            endpoint = f"{config.pc_ip}:{config.port}"
            try:
                self._real.connect(endpoint)
            except AdbError:
                pass
            serial = endpoint

        fd, dest_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            return self._real.screencap(serial, dest_path)
        finally:
            try:
                os.unlink(dest_path)
            except OSError:
                pass


def get_adb_config(db: Session) -> dict:
    """Read ADB config from SystemConfig rows; return defaults when unset.

    Returns a plain dict (FastAPI will coerce to AdbConfig response_model at the router boundary).
    """
    result = dict(_CFG_DEFAULTS)
    for field, key in _CFG_KEYS.items():
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row is None or row.value == "":
            continue
        if field == "port":
            try:
                result["port"] = int(row.value)
            except ValueError:
                result["port"] = _CFG_DEFAULTS["port"]
        else:
            result[field] = row.value
    return result


def set_adb_config(db: Session, arg1, arg2=None, arg3=None) -> None:
    """Upsert the 3 SystemConfig rows. Accepts AdbConfig OR (device_type, pc_ip, port)."""
    device_type, pc_ip, port = _normalize_adb_args(arg1, arg2, arg3)
    values = {
        "device_type": device_type,
        "pc_ip": pc_ip,
        "port": str(port),
    }
    for field, key in _CFG_KEYS.items():
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row is None:
            db.add(SystemConfig(key=key, value=values[field]))
        else:
            row.value = values[field]
    db.commit()


def get_extension_status():
    """Return 2.3-shaped ExtensionStatusResponse — installed reflects INFILL_EXT_ID env presence."""
    from ..schemas_auto_import import ExtensionStatusResponse

    configured = bool(os.environ.get("INFILL_EXT_ID", "").strip())
    return ExtensionStatusResponse(
        ok=True,
        installed=configured,
        version=os.environ.get("INFILL_EXT_VERSION") if configured else None,
        last_probe_at=None,
    )


# ==== LLM SKU Match + SKU Search (Task 2.2) ====


def load_catalog_for_llm(db: Session) -> list[dict]:
    """Return catalog rows for LLM prompt injection. Used by CUJ-1/2 scan endpoints."""
    return [
        {"sku_code": p.sku, "sku_name": p.name, "description": (p.description or "")}
        for p in db.query(Product).all()
    ]


def search_skus(db: Session, q: str, limit: int = 10) -> list[dict]:
    """SKU 搜索：name / sku 子串命中（不区分大小写）；返回 [{sku_code, sku_name, score}]。

    sku_code 精确子串 → score=1.0；name 命中 → score=0.8。空 query 返回 [].
    Shape aligns with `SkuSearchHit` so router can do `SkuSearchHit(**h)`.
    """
    q = (q or "").strip()
    if not q:
        return []

    like = f"%{q}%"
    rows = (
        db.query(Product)
        .filter(or_(Product.name.ilike(like), Product.sku.ilike(like)))
        .order_by(Product.sku.asc())
        .limit(limit)
        .all()
    )

    hits: list[dict] = []
    q_lower = q.lower()
    for product in rows:
        score = 1.0 if q_lower in product.sku.lower() else 0.8
        hits.append({
            "sku_code": product.sku,
            "sku_name": product.name,
            "score": score,
        })
    return hits


# ==== Scan + Commit + Batch State (Task 2.3) ====

BATCH_SCREENS: dict[str, dict] = {}
"""{ batch_id: { "screens": [{seq, status, error}], "parsed_orders": [{...}], "tmp_dir": "..." } }"""

BATCH_TASKS: dict[str, list[asyncio.Task]] = {}
"""{ batch_id: [Task, Task, ...] } — cancel-scan iterates and .cancel() each."""


_REDO_RE = re.compile(r"-redo(\d+)$")


def _next_redo_suffix(db: Session, platform: str, base_external_id: str) -> str:
    """Compute the next `-redoN` suffix for an override-duplicate commit.

    Queries existing orders with `(platform, external_order_id LIKE '<base>-redo%')`,
    extracts max N, returns `<base>-redo<N+1>`. Used in CUJ-3 commit endpoint.
    """
    from ..models import Order

    rows = (
        db.query(Order.external_order_id)
        .filter(
            Order.platform == platform,
            Order.external_order_id.like(f"{base_external_id}-redo%"),
        )
        .all()
    )
    max_n = 0
    for (eid,) in rows:
        if not eid:
            continue
        m = _REDO_RE.search(eid)
        if not m:
            continue
        try:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
        except ValueError:
            continue
    return f"{base_external_id}-redo{max_n + 1}"
