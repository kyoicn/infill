"""auto-import services tests — co-authored; classes fenced by task.
"""
from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import SystemConfig
from app.services import adb_client as adb_client_mod
from app.services import auto_import as auto_import_mod
from app.services.adb_client import AdbClient, AdbDevice
from app.services.auto_import import (
    diagnose_adb,
    get_adb_config,
    set_adb_config,
)


# ---------- shared fixtures ----------

@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()
    yield session
    session.close()


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    cp = subprocess.CompletedProcess(args=["adb"], returncode=returncode)
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


# ==== Task 2.1 ====

class TestAdbClient:
    """Mock subprocess.run via monkeypatch."""

    def test_is_installed_ok(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            return _make_completed(0, "Android Debug Bridge version 1.0.41\n")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        assert client.is_installed() is True

    def test_is_installed_missing_binary(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            raise FileNotFoundError("adb not found")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        assert client.is_installed() is False

    def test_connect_success(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            return _make_completed(0, "connected to 127.0.0.1:7555\n")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        ok, msg = client.connect("127.0.0.1:7555")
        assert ok is True
        assert "connected to" in msg

    def test_connect_refused(self, monkeypatch):
        def fake_run(args, capture_output, text, timeout):
            return _make_completed(1, "", "failed to connect to 127.0.0.1:7555: Connection refused\n")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        ok, msg = client.connect("127.0.0.1:7555")
        assert ok is False
        assert "failed" in msg.lower() or "refused" in msg.lower()

    def test_list_devices_parses_rows(self, monkeypatch):
        stdout = (
            "List of devices attached\n"
            "127.0.0.1:7555  device product:MuMu model:MuMu transport_id:1\n"
            "emulator-5554  offline\n"
            "\n"
        )

        def fake_run(args, capture_output, text, timeout):
            return _make_completed(0, stdout)

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)
        client = AdbClient()
        devices = client.list_devices()
        assert len(devices) == 2
        assert devices[0].serial == "127.0.0.1:7555"
        assert devices[0].state == "device"
        assert devices[0].properties.get("product") == "MuMu"
        assert devices[1].serial == "emulator-5554"
        assert devices[1].state == "offline"

    def test_screencap_pipes_pull_then_reads(self, monkeypatch, tmp_path):
        calls: list[list[str]] = []
        dest = tmp_path / "shot.png"
        png_payload = b"\x89PNG\r\n\x1a\nFAKE"

        def fake_run(args, capture_output, text, timeout):
            calls.append(list(args))
            # `args` looks like ["adb", "-s", <serial>, <subcmd>, ...]
            if len(args) >= 4 and args[3] == "pull":
                dest.write_bytes(png_payload)
            return _make_completed(0, "")

        monkeypatch.setattr(adb_client_mod.subprocess, "run", fake_run)

        client = AdbClient()
        data = client.screencap("127.0.0.1:7555", str(dest))
        assert data == png_payload
        # Flatten all tokens — screencap is `shell screencap -p ...`, pull is `pull ...`.
        all_tokens = [tok for c in calls for tok in c]
        assert "screencap" in all_tokens
        assert "pull" in all_tokens


class TestDiagnoseAdb:
    """Mock AdbClient + socket + ping."""

    def test_all_ok(self, monkeypatch):
        # ADB installed
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, "connected"))
        monkeypatch.setattr(
            AdbClient,
            "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="device")],
        )
        # ping ok
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        # tcp ok
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert len(diags) == 4
        assert all(d["ok"] for d in diags), diags

    def test_adb_missing(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: False)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (False, ""))
        monkeypatch.setattr(AdbClient, "list_devices", lambda self: [])

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[0]["ok"] is False
        # Device check fails too (since installed=False guards it)
        assert diags[3]["ok"] is False
        assert diags[0]["hint"]

    def test_ping_fails(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: False)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, ""))
        monkeypatch.setattr(
            AdbClient,
            "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="device")],
        )

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[1]["ok"] is False
        assert diags[1]["hint"]

    def test_port_closed(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: False)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (False, ""))
        monkeypatch.setattr(AdbClient, "list_devices", lambda self: [])

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        assert diags[2]["ok"] is False
        assert diags[2]["hint"]

    def test_device_offline(self, monkeypatch):
        monkeypatch.setattr(AdbClient, "is_installed", lambda self: True)
        monkeypatch.setattr(auto_import_mod, "_ping", lambda host, timeout_s=2: True)
        monkeypatch.setattr(auto_import_mod, "_tcp_open", lambda host, port, timeout_s=2.0: True)
        monkeypatch.setattr(AdbClient, "connect", lambda self, ep, timeout_s=5.0: (True, "connected"))
        monkeypatch.setattr(
            AdbClient,
            "list_devices",
            lambda self: [AdbDevice(serial="127.0.0.1:7555", state="offline")],
        )

        diags = diagnose_adb("mumu", "127.0.0.1", 7555)
        # First three pass, device check fails
        assert diags[0]["ok"] is True
        assert diags[1]["ok"] is True
        assert diags[2]["ok"] is True
        assert diags[3]["ok"] is False


class TestAdbConfig:
    """In-memory SQLite via existing fixture."""

    def test_get_config_returns_defaults_when_unset(self, db_session):
        cfg = get_adb_config(db_session)
        assert cfg == {"device_type": "mumu", "pc_ip": "", "port": 7555}

    def test_set_then_get_roundtrip(self, db_session):
        set_adb_config(db_session, "bluestacks", "192.168.1.100", 5555)
        cfg = get_adb_config(db_session)
        assert cfg == {"device_type": "bluestacks", "pc_ip": "192.168.1.100", "port": 5555}

    def test_set_config_upserts_three_rows(self, db_session):
        set_adb_config(db_session, "ldplayer", "10.0.0.5", 5555)
        # Update — should not duplicate rows
        set_adb_config(db_session, "mumu", "127.0.0.1", 7555)
        rows = db_session.query(SystemConfig).filter(
            SystemConfig.key.in_([
                "auto_import_adb_device_type",
                "auto_import_adb_pc_ip",
                "auto_import_adb_port",
            ])
        ).all()
        assert len(rows) == 3
        as_dict = {r.key: r.value for r in rows}
        assert as_dict["auto_import_adb_device_type"] == "mumu"
        assert as_dict["auto_import_adb_pc_ip"] == "127.0.0.1"
        assert as_dict["auto_import_adb_port"] == "7555"
