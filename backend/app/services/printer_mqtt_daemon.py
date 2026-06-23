"""PRD-007 (T2.1) Bambu Lab MQTT 守护进程。

每台「ip + serial + access_code 三字段齐全」的打印机起一个 paho-mqtt 客户端，
订阅 `device/{serial}/report`，把 `print.gcode_state` 标准化后扔给 Sampler。

约束：
- 单进程内单例（由调用方在 lifespan 维护）。
- 不持久化重连状态：进程崩溃 / 重启即从零订阅。
- 日志严禁输出 access_code 原值，统一走 `_mask_access_code` 掩码。
- 不做硬件层深度遥测（只关心 `print.gcode_state`）。

接口契约（与 T2.2 的 Sampler 对齐）：
- `sampler.on_event(printer_id, state, ts)`：MQTT 收到推送时调（同步、线程安全）。
- `sampler.on_unconfigured(printer_id)`：凭证从齐全变为缺失时调（清理状态 + 写一条 offline）。
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

import paho.mqtt.client as mqtt

from ..database import SessionLocal
from ..models import Printer

logger = logging.getLogger(__name__)


def _mask_access_code(code: Optional[str]) -> str:
    """守护进程日志专用掩码：前 2 + `...` + 后 2。

    - `None` → `"none"`
    - 长度 < 4 → `"****"`（不暴露任何字符）
    - 其余 → `f"{code[:2]}...{code[-2:]}"`
    """
    if code is None:
        return "none"
    if len(code) < 4:
        return "****"
    return f"{code[:2]}...{code[-2:]}"


def _normalize_gcode_state(raw: Optional[str]) -> Literal["running", "pause", "idle"]:
    """Bambu `print.gcode_state` → 标准化状态。

    - `"RUNNING"` → `"running"`
    - `"PAUSE"` → `"pause"`
    - 其他一切（含 `IDLE`/`PREPARE`/`FINISH`/`FAILED`/未知 token/`None`）→ `"idle"`

    `offline` 状态不由本函数产出，由 sampler 的心跳兜底负责。
    """
    if raw == "RUNNING":
        return "running"
    if raw == "PAUSE":
        return "pause"
    return "idle"


class MqttDaemon:
    """每台打印机一个 paho-mqtt client，单例 owner 由 lifespan 持有。

    线程模型：
    - `__init__` / `startup` / `reconcile_*` / `shutdown` 在 asyncio 主线程调用。
    - paho `loop_start()` 起后台线程跑回调；`_on_*` 在后台线程被调。
    - `_clients` 增删用 `_lock` 串行化，避免 reconcile 与 shutdown 竞态。
    """

    def __init__(self, sampler, db_session_factory=SessionLocal):
        self._sampler = sampler
        self._db_session_factory = db_session_factory
        self._clients: dict[int, mqtt.Client] = {}
        self._lock = threading.Lock()

    # ---------- client 构建 ----------

    def _build_client(self, printer: Printer) -> mqtt.Client:
        client_id = f"infill-{printer.id}-{uuid4().hex[:8]}"
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        client.username_pw_set(username="bblp", password=printer.access_code)

        # Bambu 用自签证书：社区共识是开 TLS 但不校验
        tls_ctx = ssl.create_default_context()
        tls_ctx.check_hostname = False
        tls_ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(tls_ctx)
        client.tls_insecure_set(True)

        client.user_data_set({"printer_id": printer.id, "serial": printer.serial})
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        client.connect_async(printer.ip, 8883, keepalive=60)
        client.loop_start()

        logger.info(
            "printer %s @ %s code=%s",
            printer.id,
            printer.ip,
            _mask_access_code(printer.access_code),
        )
        return client

    # ---------- paho 回调 ----------

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        printer_id = userdata.get("printer_id") if userdata else None
        if rc == 0:
            serial = userdata.get("serial") if userdata else None
            client.subscribe(f"device/{serial}/report")
        else:
            logger.warning("printer %s connect failed rc=%s", printer_id, rc)

    def _on_message(self, client, userdata, msg):
        printer_id = userdata.get("printer_id") if userdata else None
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("printer %s: invalid JSON payload", printer_id)
            return
        raw_state = payload.get("print", {}).get("gcode_state") if isinstance(payload, dict) else None
        if not raw_state:
            return
        normalized = _normalize_gcode_state(raw_state)
        self._sampler.on_event(printer_id, normalized, datetime.now())

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        printer_id = userdata.get("printer_id") if userdata else None
        logger.info("printer %s disconnected rc=%s", printer_id, reason_code)

    # ---------- 生命周期 ----------

    def startup(self) -> None:
        """加载所有凭证齐全的打印机并起 client。"""
        with self._db_session_factory() as db:
            printers = (
                db.query(Printer)
                .filter(
                    Printer.ip.isnot(None),
                    Printer.serial.isnot(None),
                    Printer.access_code.isnot(None),
                )
                .all()
            )
            # 在 session 关闭前 detach 出需要的字段
            snapshots = [
                _PrinterSnapshot(
                    id=p.id, ip=p.ip, serial=p.serial, access_code=p.access_code
                )
                for p in printers
            ]
        for snap in snapshots:
            client = self._build_client(snap)
            with self._lock:
                self._clients[snap.id] = client

    def shutdown(self) -> None:
        with self._lock:
            clients = list(self._clients.items())
            self._clients.clear()
        for _pid, client in clients:
            client.loop_stop()
            client.disconnect()

    # ---------- reconcile ----------

    def reconcile_one(self, printer_id: int) -> None:
        """凭证或 IP 变更后触发：先断旧 client，凭证齐全则起新 client，否则通知 sampler。"""
        with self._lock:
            old = self._clients.pop(printer_id, None)
        if old is not None:
            old.loop_stop()
            old.disconnect()

        with self._db_session_factory() as db:
            p = db.get(Printer, printer_id)
            if p is None:
                snapshot = None
                printer_exists = False
            elif p.ip and p.serial and p.access_code:
                snapshot = _PrinterSnapshot(
                    id=p.id, ip=p.ip, serial=p.serial, access_code=p.access_code
                )
                printer_exists = True
            else:
                snapshot = None
                printer_exists = True

        if snapshot is not None:
            client = self._build_client(snapshot)
            with self._lock:
                self._clients[snapshot.id] = client
        elif printer_exists:
            self._sampler.on_unconfigured(printer_id)

    def reconcile_all(self) -> None:
        with self._db_session_factory() as db:
            ids = [p.id for p in db.query(Printer).all()]
        for pid in ids:
            self.reconcile_one(pid)

    def unsubscribe_one(self, printer_id: int) -> None:
        """删打印机时调用：不通知 sampler（FK CASCADE 会清样本）。"""
        with self._lock:
            client = self._clients.pop(printer_id, None)
        if client is not None:
            client.loop_stop()
            client.disconnect()


class _PrinterSnapshot:
    """只读快照，避免 ORM 对象在 session 关闭后 lazy-load 出错。"""

    __slots__ = ("id", "ip", "serial", "access_code")

    def __init__(self, id: int, ip: str, serial: str, access_code: str):
        self.id = id
        self.ip = ip
        self.serial = serial
        self.access_code = access_code
