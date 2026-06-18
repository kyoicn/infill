"""auto-import services — ADB + LLM + SKU search + scan/commit business logic.

This file is co-authored across Group 2 tasks (2.1 / 2.2 / 2.3). Each task's
section is fenced with a banner; do not interleave.
"""
from __future__ import annotations

import os
import socket
import subprocess
from typing import TYPE_CHECKING

from .adb_client import AdbClient, AdbDevice, AdbError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ==== ADB & Config (Task 2.1) ====

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
    "pc_ip": "",
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


def diagnose_adb(device_type: str, pc_ip: str, port: int) -> list[dict]:
    """Return 4 diagnostic checks for ADB connectivity.

    Each item: {label: str, ok: bool, hint: str | None}
    Checks:
      1. ADB binary installed on host
      2. PC reachable via ping
      3. TCP port open on PC
      4. adb sees the device in "device" state
    """
    diagnostics: list[dict] = []
    client = AdbClient()

    # 1. ADB installed
    installed = client.is_installed()
    diagnostics.append({
        "label": "ADB 可执行文件已安装",
        "ok": installed,
        "hint": None if installed else "未在 PATH 找到 adb，请安装 platform-tools 或设置 ADB_PATH 环境变量。",
    })

    # 2. PC reachable
    pingable = _ping(pc_ip) if pc_ip else False
    diagnostics.append({
        "label": f"PC 主机 {pc_ip or '(未设置)'} 可达",
        "ok": pingable,
        "hint": None if pingable else "无法 ping 通该 IP，请检查模拟器/USB 主机的 IP 设置和防火墙。",
    })

    # 3. TCP port open
    port_open = _tcp_open(pc_ip, port) if pc_ip and port else False
    diagnostics.append({
        "label": f"TCP 端口 {port} 已打开",
        "ok": port_open,
        "hint": None if port_open else f"无法连接 {pc_ip}:{port}，请确认 ADB 服务监听端口，或在 {device_type} 中开启调试。",
    })

    # 4. Device in "device" state
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
            if not device_ok and devices:
                # Fall back: any device in "device" state counts when USB
                if device_type == "usb":
                    device_ok = any(d.state == "device" for d in devices)
        except AdbError:
            device_ok = False
    diagnostics.append({
        "label": "设备处于 device 状态（已授权）",
        "ok": device_ok,
        "hint": None if device_ok else "设备未授权或处于 offline / unauthorized 状态，请在模拟器/手机上点击允许调试。",
    })

    return diagnostics


def get_adb_config(db: "Session") -> dict:
    """Read ADB config from SystemConfig rows; return defaults when unset."""
    from app.models import SystemConfig

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


def set_adb_config(db: "Session", device_type: str, pc_ip: str, port: int) -> None:
    """Upsert the 3 SystemConfig rows for ADB config."""
    from app.models import SystemConfig

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


def get_extension_status() -> dict:
    """Backend-side extension visibility — frontend pings via chrome.runtime.sendMessage."""
    configured = bool(os.environ.get("INFILL_EXT_ID", "").strip())
    return {
        "configured": configured,
        "env_var_name": "VITE_INFILL_EXT_ID",
        "expected_version_prefix": "0.1",
    }
