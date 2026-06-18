"""Auto-import request / response schemas (prd-006).

Section banners isolate Tasks 2.1 / 2.2 / 2.3 for parallel merges.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ==== ADB & Config (Task 2.1) ====


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


# ==== Order Persistence (Task 2.3) ====
