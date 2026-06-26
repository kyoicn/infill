"""infill 打印机状态采集器（mini 宿主机原生进程）。

为什么独立于 infill 容器：
  Docker on macOS（colima）容器跑在 Linux VM 里、与 Mac 宿主同 LAN 隔离 → 容器
  里的 MQTT 客户端连不到 192.168.31.x 上的 Bambu 打印机。bridge 跑在 Mac 宿主
  上没有这层隔离，能直连 LAN。

数据流：
  Bambu 打印机 ──MQTT 8883 TLS──▶ bridge.py ──HTTP POST──▶ infill 容器
                                                          (/api/internal/printer_state)
                                                          → Sampler.on_event(...)
                                                          → DB sample + WS 广播

部署：
  - 打成 shiv 单文件 .pyz，作为 release asset 分发
  - mini 上 launchd 跑 `python3 bridge.pyz`
  - 每次 release，deploy job 把新 .pyz 写到 ~/.infill-bridge/bin/ 然后 launchctl kickstart

配置（env vars，由 launchd plist 注入）：
  INFILL_DB_PATH   SQLite 路径，默认 ~/workspace/infill-deploy/data/data.db
  INFILL_API_URL   容器后端 URL，默认 http://localhost:8000
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import ssl
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

import paho.mqtt.client as mqtt

logger = logging.getLogger("infill.bridge")

DEFAULT_DB = "~/workspace/infill-deploy/data/data.db"
DEFAULT_API = "http://localhost:8000"


def _mask_access_code(code: Optional[str]) -> str:
    if not code:
        return "none"
    if len(code) < 4:
        return "****"
    return f"{code[:2]}...{code[-2:]}"


def _normalize_gcode_state(raw: Optional[str]) -> Literal["running", "pause", "idle"]:
    """对齐 backend/app/services/printer_mqtt_daemon.py 同名函数。"""
    if raw == "RUNNING":
        return "running"
    if raw == "PAUSE":
        return "pause"
    return "idle"


def _post_event(api_url: str, printer_id: int, state: str, ts: datetime) -> None:
    payload = json.dumps(
        {
            "printer_id": printer_id,
            "state": state,
            "ts": ts.isoformat(),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url}/api/internal/printer_state",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
    except urllib.error.URLError as e:
        logger.warning(
            "post event failed printer=%s state=%s err=%s", printer_id, state, e
        )


def _on_connect(client, userdata, _flags, rc, _properties=None):
    if rc == 0:
        client.subscribe(f"device/{userdata['serial']}/report")
        logger.info("printer %s subscribed", userdata["printer_id"])
    else:
        logger.warning(
            "printer %s connect failed rc=%s", userdata["printer_id"], rc
        )


def _on_message(_client, userdata, msg):
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    raw_state = payload.get("print", {}).get("gcode_state")
    if not raw_state:
        return
    state = _normalize_gcode_state(raw_state)
    _post_event(userdata["api_url"], userdata["printer_id"], state, datetime.now())


def _on_disconnect(_client, userdata, _disconnect_flags, reason_code, _properties=None):
    """对齐 backend daemon：paho 2.x VERSION2 五参数签名。

    真断开（认证失败/网络抖动/打印机断电）立刻报 offline，不依赖 sampler timeout。
    paho loop_start 会自动重连；重连后 on_message 推真实 state 覆盖回来。
    """
    logger.info("printer %s disconnected rc=%s", userdata["printer_id"], reason_code)
    _post_event(userdata["api_url"], userdata["printer_id"], "offline", datetime.now())


def _build_client(printer_id: int, ip: str, serial: str, access_code: str, api_url: str):
    client_id = f"infill-bridge-{printer_id}-{uuid4().hex[:8]}"
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    client.tls_set_context(ctx)
    client.tls_insecure_set(True)
    client.username_pw_set(username="bblp", password=access_code)
    client.user_data_set(
        {"printer_id": printer_id, "serial": serial, "api_url": api_url}
    )
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.on_disconnect = _on_disconnect
    logger.info(
        "printer %s @ %s code=%s starting", printer_id, ip, _mask_access_code(access_code)
    )
    client.connect_async(ip, 8883, keepalive=60)
    client.loop_start()
    return client


def _load_printers(db_path: str):
    """从 SQLite 拉所有凭证齐全的 Printer 行。"""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT id, ip, serial, access_code FROM printers "
            "WHERE ip IS NOT NULL AND serial IS NOT NULL AND access_code IS NOT NULL"
        )
        return list(cur.fetchall())
    finally:
        conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    db_path = os.path.expanduser(os.environ.get("INFILL_DB_PATH", DEFAULT_DB))
    api_url = os.environ.get("INFILL_API_URL", DEFAULT_API).rstrip("/")
    logger.info("bridge starting db=%s api=%s", db_path, api_url)
    if not Path(db_path).is_file():
        logger.error("DB not found at %s", db_path)
        return 1

    printers = _load_printers(db_path)
    if not printers:
        logger.warning("no configured printers — bridge idle")
    clients = [
        _build_client(pid, ip, serial, code, api_url) for (pid, ip, serial, code) in printers
    ]

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    logger.info("shutdown signaled, stopping %d clients", len(clients))
    for c in clients:
        try:
            c.loop_stop()
            c.disconnect()
        except Exception:
            logger.exception("client shutdown failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
