"""PRD-007 (T2.1) MQTT 守护进程单测。

全部用 unittest.mock 替换 paho.mqtt.client.Client 与 ssl.create_default_context，
不真起 MQTT 客户端、不真建网络连接。

覆盖：
- case A: username_pw_set 用 ("bblp", access_code)
- case B: TLS context CERT_NONE + check_hostname=False + tls_insecure_set(True)
- case C: connect_async(ip, 8883, keepalive=60) + loop_start()
- case D: on_connect rc=0 触发 subscribe("device/{serial}/report")
- case E: gcode_state 标准化 5 个分支（RUNNING/PAUSE/IDLE/FINISH/未知）
- case F: JSON parse 失败不抛、sampler.on_event 不被调
- case G: reconcile_one 凭证齐全 → 起 client 进 _clients
- case H: reconcile_one 凭证缺 → 不起 client + sampler.on_unconfigured 被调
- case I: 日志不含 access_code 原值
"""

from __future__ import annotations

import logging
import ssl
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


@contextmanager
def _patched_paho():
    """统一 patch paho.mqtt.client.Client + ssl.create_default_context。

    yields: (mock_client_cls, mock_ctx_factory, mock_client_instance_proxy)
      - mock_client_cls：用来取 call_args 验证构造参数
      - mock_ctx_factory：用来取 create_default_context 的返回 ctx
      - mock_client_instance_proxy：包装方便测试拿到「最后一次创建的实例」
    """
    with patch(
        "app.services.printer_mqtt_daemon.mqtt.Client"
    ) as mock_client_cls, patch(
        "app.services.printer_mqtt_daemon.ssl.create_default_context"
    ) as mock_ctx_factory:
        # 每次调用 mqtt.Client(...) 都返回一个全新 MagicMock 实例
        def _new_client_instance(*_a, **_kw):
            return MagicMock()

        mock_client_cls.side_effect = _new_client_instance

        # ctx mock：要能给测试断言 verify_mode / check_hostname 属性写入
        def _new_ctx(*_a, **_kw):
            ctx = MagicMock()
            ctx.verify_mode = ssl.CERT_REQUIRED  # 默认值，daemon 应把它改成 CERT_NONE
            ctx.check_hostname = True
            return ctx

        mock_ctx_factory.side_effect = _new_ctx

        yield mock_client_cls, mock_ctx_factory


def _make_printer(printer_id=1, ip="192.168.1.50", serial="01P00A123456789", access_code="12345678"):
    p = MagicMock()
    p.id = printer_id
    p.ip = ip
    p.serial = serial
    p.access_code = access_code
    return p


def _build_daemon(sampler=None, db_factory=None):
    """造一个最小依赖的 daemon 实例。"""
    from app.services.printer_mqtt_daemon import MqttDaemon

    if sampler is None:
        sampler = MagicMock()
    if db_factory is None:
        db_factory = MagicMock()
    return MqttDaemon(sampler=sampler, db_session_factory=db_factory)


# ---------- case A: 认证参数 ----------

def test_build_client_sets_username_password_bblp_access_code():
    with _patched_paho() as (mock_client_cls, _):
        daemon = _build_daemon()
        printer = _make_printer(access_code="codeABCD")

        client = daemon._build_client(printer)

        client.username_pw_set.assert_called_once_with(
            username="bblp", password="codeABCD"
        )


# ---------- case B: TLS 配置 ----------

def test_build_client_sets_tls_context_with_cert_none_and_insecure():
    with _patched_paho() as (_, mock_ctx_factory):
        daemon = _build_daemon()
        printer = _make_printer()

        client = daemon._build_client(printer)

        # ctx 工厂被调
        mock_ctx_factory.assert_called_once()

        # tls_set_context 被调一次，参数是同一个 ctx
        assert client.tls_set_context.call_count == 1
        called_ctx = client.tls_set_context.call_args.args[0]
        assert called_ctx.verify_mode == ssl.CERT_NONE
        assert called_ctx.check_hostname is False

        # tls_insecure_set(True) 被调
        client.tls_insecure_set.assert_called_once_with(True)


# ---------- case C: connect_async + loop_start ----------

def test_build_client_calls_connect_async_and_loop_start():
    with _patched_paho() as (_, _):
        daemon = _build_daemon()
        printer = _make_printer(ip="10.0.0.42")

        client = daemon._build_client(printer)

        client.connect_async.assert_called_once_with("10.0.0.42", 8883, keepalive=60)
        client.loop_start.assert_called_once()


# ---------- case D: on_connect 订阅 ----------

def test_on_connect_subscribes_to_device_report_topic_when_rc_zero():
    with _patched_paho() as (_, _):
        daemon = _build_daemon()
        client_mock = MagicMock()

        daemon._on_connect(
            client_mock,
            userdata={"printer_id": 1, "serial": "ZZ-9999"},
            flags={},
            rc=0,
        )

        client_mock.subscribe.assert_called_once_with("device/ZZ-9999/report")


def test_on_connect_does_not_subscribe_when_rc_nonzero():
    with _patched_paho() as (_, _):
        daemon = _build_daemon()
        client_mock = MagicMock()

        daemon._on_connect(
            client_mock,
            userdata={"printer_id": 1, "serial": "ZZ-9999"},
            flags={},
            rc=5,
        )

        client_mock.subscribe.assert_not_called()


# ---------- case E: 状态标准化 5 分支 ----------

@pytest.mark.parametrize(
    "raw_state, expected",
    [
        ("RUNNING", "running"),
        ("PAUSE", "pause"),
        ("IDLE", "idle"),
        ("FINISH", "idle"),
        ("WTF_UNKNOWN", "idle"),
    ],
)
def test_on_message_normalizes_gcode_state(raw_state, expected):
    with _patched_paho() as (_, _):
        sampler = MagicMock()
        daemon = _build_daemon(sampler=sampler)

        msg = MagicMock()
        msg.payload = (
            b'{"print":{"gcode_state":"' + raw_state.encode("ascii") + b'"}}'
        )

        daemon._on_message(MagicMock(), {"printer_id": 7, "serial": "X"}, msg)

        sampler.on_event.assert_called_once()
        args, _kwargs = sampler.on_event.call_args
        assert args[0] == 7
        assert args[1] == expected


# ---------- case F: JSON parse 失败不 raise ----------

def test_on_message_swallows_invalid_json_and_does_not_call_sampler():
    with _patched_paho() as (_, _):
        sampler = MagicMock()
        daemon = _build_daemon(sampler=sampler)

        msg = MagicMock()
        msg.payload = b"not json"

        # 关键：不抛
        daemon._on_message(MagicMock(), {"printer_id": 3, "serial": "Y"}, msg)

        sampler.on_event.assert_not_called()


def test_on_message_skips_when_gcode_state_missing():
    """Bambu 推送是增量字段，不带 gcode_state 的包必须 silent return。"""
    with _patched_paho() as (_, _):
        sampler = MagicMock()
        daemon = _build_daemon(sampler=sampler)

        msg = MagicMock()
        msg.payload = b'{"print":{"bed_temper":60.0}}'

        daemon._on_message(MagicMock(), {"printer_id": 3, "serial": "Y"}, msg)

        sampler.on_event.assert_not_called()


# ---------- case G & H: reconcile_one ----------

class _FakeDBSessionFactory:
    """模拟 SessionLocal()：上下文管理器，返回带 get/query 的伪 db。"""

    def __init__(self, printers_by_id: dict):
        self._by_id = printers_by_id

    def __call__(self):
        return self  # 把自己当 cm 用

    def __enter__(self):
        db = MagicMock()
        db.get = lambda model, pid: self._by_id.get(pid)
        # query(Printer).all() / query(Printer).filter(...).all() 都返回全表
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = list(self._by_id.values())
        db.query.return_value = q
        return db

    def __exit__(self, *_a):
        return False


def test_reconcile_one_starts_client_when_credentials_complete():
    """case G：凭证齐全 → daemon 起 client、放进 _clients。"""
    with _patched_paho() as (_, _):
        sampler = MagicMock()
        printer = _make_printer(printer_id=10, ip="1.1.1.1", serial="S10", access_code="abcd1234")
        db_factory = _FakeDBSessionFactory({10: printer})
        daemon = _build_daemon(sampler=sampler, db_factory=db_factory)

        daemon.reconcile_one(10)

        assert 10 in daemon._clients
        sampler.on_unconfigured.assert_not_called()


def test_reconcile_one_calls_on_unconfigured_when_any_field_missing():
    """case H：三字段任一 None → 不起 client + sampler.on_unconfigured(printer_id)。"""
    with _patched_paho() as (_, _):
        sampler = MagicMock()
        printer = _make_printer(printer_id=11, ip=None, serial="S11", access_code="abcd1234")
        db_factory = _FakeDBSessionFactory({11: printer})
        daemon = _build_daemon(sampler=sampler, db_factory=db_factory)

        daemon.reconcile_one(11)

        assert 11 not in daemon._clients
        sampler.on_unconfigured.assert_called_once_with(11)


def test_reconcile_one_disconnects_old_client_before_starting_new():
    """补充：reconcile 替换 client 时先断旧的。"""
    with _patched_paho() as (_, _):
        sampler = MagicMock()
        printer = _make_printer(printer_id=12, ip="1.1.1.1", serial="S12", access_code="newcode8")
        db_factory = _FakeDBSessionFactory({12: printer})
        daemon = _build_daemon(sampler=sampler, db_factory=db_factory)

        # 先放一个老 client
        old_client = MagicMock()
        daemon._clients[12] = old_client

        daemon.reconcile_one(12)

        old_client.loop_stop.assert_called_once()
        old_client.disconnect.assert_called_once()
        assert daemon._clients[12] is not old_client


def test_reconcile_one_noop_when_printer_not_found():
    """补充：printer 已被删 → 既不起 client 也不调 sampler.on_unconfigured。"""
    with _patched_paho() as (_, _):
        sampler = MagicMock()
        db_factory = _FakeDBSessionFactory({})
        daemon = _build_daemon(sampler=sampler, db_factory=db_factory)

        daemon.reconcile_one(999)

        assert 999 not in daemon._clients
        sampler.on_unconfigured.assert_not_called()


# ---------- case I: 日志不泄露 access_code 原值 ----------

def test_startup_and_reconcile_logs_never_leak_raw_access_code(caplog):
    """case I：startup + reconcile_one 整条链路日志不含 access_code 原值。"""
    secret = "SECRET_CODE_DO_NOT_LEAK"
    with _patched_paho() as (_, _), caplog.at_level(logging.DEBUG):
        sampler = MagicMock()
        printer = _make_printer(printer_id=42, ip="1.2.3.4", serial="SN-42", access_code=secret)
        db_factory = _FakeDBSessionFactory({42: printer})
        daemon = _build_daemon(sampler=sampler, db_factory=db_factory)

        daemon.startup()
        daemon.reconcile_one(42)

        # 触发 on_disconnect 也走一遍（rc != 0 + connect 失败路径）
        daemon._on_connect(MagicMock(), {"printer_id": 42, "serial": "SN-42"}, {}, rc=4)
        daemon._on_disconnect(MagicMock(), {"printer_id": 42, "serial": "SN-42"}, rc=1)

        # 关键断言：原值绝不出现在任何日志里
        assert secret not in caplog.text
        # 同时确认日志确实记录了一些内容（防 caplog 没抓到任何日志的假阴性）
        assert "printer 42" in caplog.text


def test_mask_access_code_helper_branches():
    """单独验证 _mask_access_code 行为，方便上面整体断言失败时定位。"""
    from app.services.printer_mqtt_daemon import _mask_access_code

    assert _mask_access_code(None) == "none"
    assert _mask_access_code("") == "****"
    assert _mask_access_code("abc") == "****"
    assert _mask_access_code("abcd") == "ab...cd"
    assert _mask_access_code("12345678") == "12...78"
    assert _mask_access_code("SECRET_CODE_DO_NOT_LEAK") == "SE...AK"


def test_normalize_gcode_state_helper_branches():
    from app.services.printer_mqtt_daemon import _normalize_gcode_state

    assert _normalize_gcode_state("RUNNING") == "running"
    assert _normalize_gcode_state("PAUSE") == "pause"
    assert _normalize_gcode_state("IDLE") == "idle"
    assert _normalize_gcode_state("PREPARE") == "idle"
    assert _normalize_gcode_state("FINISH") == "idle"
    assert _normalize_gcode_state("FAILED") == "idle"
    assert _normalize_gcode_state("foobar") == "idle"
    assert _normalize_gcode_state(None) == "idle"
