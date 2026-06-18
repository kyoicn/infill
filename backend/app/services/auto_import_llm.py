"""自动导入订单 LLM 调用（prd-006，Task 2.2）

owns:
- `match_listing_to_sku(listing_title, catalog) -> dict` —— 把平台原文标题匹配到 catalog SKU
- `parse_xianyu_screenshot(image_bytes) -> list[dict]` —— 把闲鱼订单页截图解析为订单结构

失败统一抛 `intake_llm.LLMProviderError`（与 prd-005 一致）。

本 worktree 早于 Task 2.2 落地，先放占位 stub 让 router / 测试可用 +
让 Task 2.2 合入时只换函数体不动调用方。
"""

from __future__ import annotations

from typing import Optional

from .intake_llm import LLMProviderError  # 复用统一异常


def match_listing_to_sku(listing_title: str, catalog: list[dict]) -> dict:
    """把平台原文标题匹配到 catalog SKU。

    Task 2.2 owns 实际 LLM 实现（DashScope qwen3-omni-flash + 全量 catalog 注入 prompt）。

    返回结构 ::
        {
          "matched_sku_code": "TOTORO-L" | None,
          "confidence": 0.0 ~ 1.0,
          "reasoning": "...",
        }

    失败：抛 LLMProviderError(error_kind, message, raw_preview)。

    占位实现：纯子串匹配 sku_name；找到一个就给 0.9，多个给 0.7，找不到给 0.0。
    便于 router / 测试早期可用；Task 2.2 合入后替换。
    """
    title = (listing_title or "").strip()
    if not title:
        return {"matched_sku_code": None, "confidence": 0.0, "reasoning": "空标题"}

    hits: list[dict] = []
    for entry in catalog or []:
        name = (entry.get("sku_name") or "").strip()
        if name and name in title:
            hits.append(entry)

    if not hits:
        return {
            "matched_sku_code": None,
            "confidence": 0.0,
            "reasoning": "占位 stub 未找到匹配",
        }

    if len(hits) == 1:
        return {
            "matched_sku_code": hits[0]["sku_code"],
            "confidence": 0.9,
            "reasoning": f"占位 stub 单匹配 {hits[0]['sku_name']}",
        }

    # 多匹配 → 取最长 name 的一个，置信度降级
    best = max(hits, key=lambda h: len(h.get("sku_name") or ""))
    return {
        "matched_sku_code": best["sku_code"],
        "confidence": 0.7,
        "reasoning": f"占位 stub 多匹配取最长 {best['sku_name']}",
    }


def parse_xianyu_screenshot(image_bytes: bytes) -> list[dict]:
    """把闲鱼订单页截图解析为订单结构数组。

    Task 2.2 owns 实际 LLM 实现。
    返回结构 ::
        [
          {
            "external_order_id": "...",
            "buyer_nickname": "...",
            "external_created_at": "...",
            "products": [{"listing_title": "...", "quantity": N}, ...],
          },
          ...
        ]

    失败：抛 LLMProviderError。
    占位实现：返回空列表（无 panic）—— Task 2.2 合入后替换。
    """
    return []
