"""auto-import Pydantic schemas — co-authored; sections fenced.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ==== Settings & ADB (Task 2.1) ====

class AdbConfig(BaseModel):
    device_type: str
    pc_ip: str
    port: int


class Diagnostic(BaseModel):
    label: str
    ok: bool
    hint: Optional[str] = None


class TestAdbResponse(BaseModel):
    ok: bool
    connected: bool
    device_serial: Optional[str] = None
    system: Optional[str] = None
    diagnostics: list[Diagnostic]


class ExtensionStatusResponse(BaseModel):
    configured: bool
    env_var_name: str = "VITE_INFILL_EXT_ID"
    expected_version_prefix: str = "0.1"
