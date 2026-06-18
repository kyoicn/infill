"""自动导入订单（prd-006）服务层

按设计分三大段（每段由独立 Task 拥有）：
- Task 2.1 ADB：`diagnose_adb` / `AdbClient` / 配置读写 / 扩展状态
- Task 2.2 LLM SKU 匹配：`match_listing_to_sku` / `parse_xianyu_screenshot` / `search_skus` / `load_catalog_for_llm`
- Task 2.3 路由编排状态：`BATCH_SCREENS` / `BATCH_TASKS` / `_next_redo_suffix`

本 worktree 早于 Task 2.1 / 2.2 落地，先放占位 stub 让 router 可导入 + 让测试可 monkeypatch。
2.1 / 2.2 合入后会替换各自 banner 段内的实现。
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from sqlalchemy.orm import Session


# ==== ADB + Extension Status (Task 2.1) ====
# Task 2.1 owns: diagnose_adb / AdbClient / get_adb_config / set_adb_config / get_extension_status
# 占位 stub —— 让 router 能导入 + 测试可 monkeypatch；Task 2.1 合入后替换。

def diagnose_adb(config) -> list:
    """诊断 ADB 连通：检查 adb client / pc ip / port 三项。返回 list[Diagnostic]。"""
    from ..schemas_auto_import import Diagnostic
    return [Diagnostic(name="stub", label="Task 2.1 占位", ok=False, detail="待 Task 2.1 实现")]


class AdbClient:
    """ADB 客户端封装：list_devices / screencap(config) → bytes。"""

    def list_devices(self) -> list[dict]:
        return []

    def screencap(self, config) -> bytes:
        raise NotImplementedError("Task 2.1 占位")


def get_adb_config(db: Session):
    """读取持久化的 ADB 配置；不存在返回默认值。"""
    from ..schemas_auto_import import AdbConfig
    return AdbConfig()


def set_adb_config(db: Session, config) -> None:
    """持久化 ADB 配置到 system_config 表。"""
    return None


def get_extension_status():
    """返回 Chrome 扩展是否检测到 + 版本号。"""
    from ..schemas_auto_import import ExtensionStatusResponse
    return ExtensionStatusResponse(ok=True, installed=False, version=None, last_probe_at=None)


# ==== LLM SKU Match + Xianyu Parser (Task 2.2) ====
# Task 2.2 owns: match_listing_to_sku / parse_xianyu_screenshot / search_skus / load_catalog_for_llm
# 占位 stub —— 让 router 能导入 + 测试可 monkeypatch；Task 2.2 合入后替换。
#
# 注：本文件 import `auto_import_llm` 模块为子 namespace 给路由调用，
# 但同时也在此暴露同名 wrapper 让早期工作可以使用。

def load_catalog_for_llm(db: Session) -> list[dict]:
    """从 Product 表读出 catalog，组成 [{sku_code, sku_name, description?}, ...]。"""
    from ..models import Product
    return [
        {"sku_code": p.sku, "sku_name": p.name, "description": p.description or ""}
        for p in db.query(Product).all()
    ]


def search_skus(db: Session, q: str, limit: int = 10) -> list[dict]:
    """SKU 搜索（picker 用）：按汉字 / 拼音 / code 模糊匹配。Task 2.2 实现。

    占位实现：纯子串匹配 sku name / code，便于 router / 测试早期可用。
    """
    from ..models import Product
    q_norm = (q or "").strip().lower()
    if not q_norm:
        return []
    hits: list[dict] = []
    for p in db.query(Product).all():
        name = (p.name or "").lower()
        sku = (p.sku or "").lower()
        score = 0.0
        if q_norm in sku:
            score = 1.0
        elif q_norm in name:
            score = 0.8
        if score > 0:
            hits.append({"sku_code": p.sku, "sku_name": p.name, "score": score})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


# ==== Scan + Commit + Batch State (Task 2.3) ====

# 短期 in-memory state：闲鱼截屏 / LLM 解析 / 批次取消
# 设计：CUJ-2 截屏过程是异步累积的，需要短期 batch state 存「N 张截屏 + 各自解析状态」。
# 不持久化（重启即丢；前端态丢失等价于重扫 — 与 PRD 一致）。

BATCH_SCREENS: dict[str, dict] = {}
"""{ batch_id: { "screens": [{seq, status, error}], "parsed_orders": [{...}], "tmp_dir": "..." } }"""

BATCH_TASKS: dict[str, list[asyncio.Task]] = {}
"""{ batch_id: [Task, Task, ...] } —— 取消时统一 .cancel()。"""


_REDO_RE = re.compile(r"-redo(\d+)$")


def _next_redo_suffix(db: Session, platform: str, base_external_id: str) -> str:
    """查同平台下 `<base>-redoN` 已用过的最大 N，返回 `<base>-redo<N+1>`。

    用于 CUJ-3「改判为新单」场景：用户对一条 (platform, external_order_id)
    已存在的订单点 override，commit 时把 external_order_id 改为 `<base>-redo1`/`-redo2`
    避免触发 partial unique index。
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
