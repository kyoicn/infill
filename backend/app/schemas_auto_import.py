"""auto-import Pydantic schemas — co-authored across Tasks 2.1 / 2.2 / 2.3.

Section banners isolate each task's contribution.
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


# ==== SKU Match (Task 2.2) ====


class SkuSearchRequest(BaseModel):
    q: str
    limit: int = 10


class SkuSearchHit(BaseModel):
    sku: str
    name: str
    color: Optional[str] = None


class SkuSearchResponse(BaseModel):
    ok: bool
    hits: list[SkuSearchHit]


# ==== Scan + Commit (Task 2.3) ====
# Filled by Task 2.3 merge.
