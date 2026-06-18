"""ADB client wrapping subprocess calls to a host-side adb binary.

Reads ADB_PATH from env (default "adb"). All subprocess calls are guarded
against FileNotFoundError / TimeoutExpired / CalledProcessError.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass, field


class AdbError(Exception):
    """Raised when an adb subprocess call fails for a known reason.

    `kind` is one of: "not_installed" | "timeout" | "failed".
    """

    def __init__(self, kind: str, message: str = ""):
        self.kind = kind
        self.message = message
        super().__init__(f"{kind}: {message}" if message else kind)


@dataclass
class AdbDevice:
    serial: str
    state: str
    properties: dict[str, str] = field(default_factory=dict)


class AdbClient:
    def __init__(self, adb_path: str | None = None):
        self.adb = adb_path or os.environ.get("ADB_PATH", "adb")

    # ---- internal ----

    def _run(self, args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.adb, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as e:
            raise AdbError("not_installed", str(e))
        except subprocess.TimeoutExpired as e:
            raise AdbError("timeout", str(e))

    # ---- public ----

    def is_installed(self) -> bool:
        try:
            result = self._run(["version"], timeout=5)
        except AdbError as e:
            if e.kind == "not_installed":
                return False
            return False
        return result.returncode == 0

    def connect(self, endpoint: str, timeout_s: float = 5.0) -> tuple[bool, str]:
        try:
            result = self._run(["connect", endpoint], timeout=timeout_s)
        except AdbError as e:
            return False, e.message or e.kind
        out = (result.stdout or "") + (result.stderr or "")
        low = out.lower()
        if "connected to" in low or "already connected" in low:
            return True, out.strip()
        if "failed" in low or "cannot" in low or "refused" in low or "unable" in low:
            return False, out.strip()
        # Default: success only if returncode is 0
        return (result.returncode == 0), out.strip()

    def list_devices(self) -> list[AdbDevice]:
        try:
            result = self._run(["devices", "-l"], timeout=5)
        except AdbError:
            return []
        if result.returncode != 0:
            return []
        devices: list[AdbDevice] = []
        lines = (result.stdout or "").splitlines()
        # Skip header "List of devices attached"
        seen_header = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if not seen_header:
                if line.lower().startswith("list of devices"):
                    seen_header = True
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            props: dict[str, str] = {}
            for token in parts[2:]:
                if ":" in token:
                    k, _, v = token.partition(":")
                    props[k] = v
            devices.append(AdbDevice(serial=serial, state=state, properties=props))
        return devices

    def screencap(self, serial: str, dest_path: str) -> bytes:
        remote_path = f"/sdcard/infill_{uuid.uuid4().hex}.png"
        try:
            r1 = self._run(["-s", serial, "shell", "screencap", "-p", remote_path], timeout=15)
            if r1.returncode != 0:
                raise AdbError("failed", f"screencap failed: {(r1.stderr or r1.stdout or '').strip()}")
            r2 = self._run(["-s", serial, "pull", remote_path, dest_path], timeout=30)
            if r2.returncode != 0:
                raise AdbError("failed", f"pull failed: {(r2.stderr or r2.stdout or '').strip()}")
            # Cleanup remote file (best effort)
            try:
                self._run(["-s", serial, "shell", "rm", remote_path], timeout=5)
            except AdbError:
                pass
            with open(dest_path, "rb") as fh:
                return fh.read()
        except AdbError:
            raise
        except OSError as e:
            raise AdbError("failed", f"read dest failed: {e}")
