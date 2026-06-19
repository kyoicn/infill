"""LLM-backed SKU matching and 闲鱼 screenshot parsing.

Reuses OpenAICompatibleVisionProvider.chat_completion() so auto-import shares
the same provider abstraction as intake.
"""
from __future__ import annotations

import base64
import json

from .intake_llm import (
    LLMProviderError,
    _strip_markdown_json,
    get_active_provider,
)


def _safe_json_loads(cleaned: str) -> object:
    """像 intake_llm 一样兜底：先 json.loads，失败时用 raw_decode 只取首个有效 JSON。

    qwen3-omni-flash 偶尔会在合法 JSON 后附加解释/第二个对象，导致 "Extra data" 错误。
    """
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        try:
            parsed, _consumed = json.JSONDecoder().raw_decode(cleaned.lstrip())
            return parsed
        except (json.JSONDecodeError, ValueError):
            raise LLMProviderError("parse_failed", str(exc), cleaned[:200]) from exc


# ---------- SKU 匹配 ----------

SKU_MATCH_SYSTEM_PROMPT = """你是一个 3D 打印作坊的订单录入助理。你的任务是把一条电商平台（小红书/闲鱼/淘宝/抖音）的商品标题匹配到下面目录中的一个 SKU。

目录表（TSV 格式，列：SKU_CODE / NAME / COLOR_VARIANTS）：
{table_rows}

匹配规则：
- 标题里出现的名词、关键字应与某个 SKU 的 NAME 强相关；颜色字段可作辅助判断。
- 输出严格 JSON（不要 markdown 包装、不要解释文字），schema：
  {{"matched_sku_code": "PR-XXXX" 或 null, "confidence": 0.0~1.0 之间的浮点, "reasoning": "一句中文短句解释匹配依据"}}
- 置信度阈值：
  - ≥ 0.85 高置信，可直接使用
  - 0.55 ~ 0.84 中置信，仍返回 SKU 但提示人工核对
  - < 0.55 视为匹配失败，必须把 matched_sku_code 设为 null
- 如果目录中没有任何合理匹配，宁可输出 null 也不要瞎填。"""


def match_listing_to_sku(
    listing_title: str,
    catalog_skus: list[dict],
    *,
    timeout_seconds: int = 30,
) -> tuple[str | None, float, str]:
    """把单条平台商品标题匹配到 catalog SKU。

    catalog_skus 元素形如 `{"sku": "PR-0001", "name": "床头柜", "color": "白色,黑色"}`，
    其中 color 可为空字符串。返回 `(matched_sku_code, confidence, reasoning)`。
    """
    provider = get_active_provider()
    if provider is None:
        raise LLMProviderError("no_api_key", "未配置 LLM API key", "")

    table_rows = "\n".join(
        f"{item.get('sku', '')}\t{item.get('name', '')}\t{item.get('color', '') or ''}"
        for item in catalog_skus
    )
    prompt = SKU_MATCH_SYSTEM_PROMPT.replace("{table_rows}", table_rows)

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"原文标题：{listing_title}"},
    ]

    content = provider.chat_completion(
        messages,
        json_object=True,
        timeout_seconds=timeout_seconds,
    )

    cleaned = _strip_markdown_json(content)
    data = _safe_json_loads(cleaned)

    if not isinstance(data, dict):
        raise LLMProviderError("schema_invalid", "LLM 输出顶层不是 object", cleaned[:200])
    if "confidence" not in data:
        raise LLMProviderError("schema_invalid", "缺少 confidence 字段", cleaned[:200])

    matched = data.get("matched_sku_code")
    if matched is not None and not isinstance(matched, str):
        raise LLMProviderError(
            "schema_invalid",
            f"matched_sku_code 必须是字符串或 null: {matched!r}",
            cleaned[:200],
        )

    try:
        confidence = float(data["confidence"])
    except (TypeError, ValueError) as exc:
        raise LLMProviderError(
            "schema_invalid",
            f"confidence 非数字: {data.get('confidence')!r}",
            cleaned[:200],
        ) from exc

    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    return (matched, confidence, reasoning)


# ---------- 闲鱼截图解析 ----------

XIANYU_PARSE_SYSTEM_PROMPT = """你是一个 3D 打印作坊的订单录入助理。你的任务是从用户上传的闲鱼订单列表截图中提取每一条订单的结构化信息。

输出严格 JSON（不要 markdown 包装、不要解释文字），schema：
{
  "orders": [
    {
      "external_order_id": "<闲鱼订单号，字符串；如截图未显示填空字符串>",
      "buyer_nickname": "<买家昵称>",
      "external_created_at": "<下单时间 ISO-8601 字符串；不确定填 null>",
      "products": [
        {"listing_title": "<商品标题完整文本>", "quantity": <整数件数>}
      ]
    }
  ]
}

要求：
- 每条订单的商品至少包含一个 product。
- quantity 必须是正整数；如未显示则填 1。
- 若识别不到任何订单，返回 `{"orders": []}`。"""


def _image_bytes_to_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def parse_xianyu_screenshot(
    image_bytes: bytes,
    *,
    timeout_seconds: int = 60,
) -> list[dict]:
    """解析单张闲鱼订单列表截图，返回 orders 列表。"""
    provider = get_active_provider()
    if provider is None:
        raise LLMProviderError("no_api_key", "未配置 LLM API key", "")

    user_content = [
        {"type": "text", "text": "下面是一张闲鱼订单列表截图，请按 schema 输出 JSON。"},
        {
            "type": "image_url",
            "image_url": {"url": _image_bytes_to_data_url(image_bytes)},
        },
    ]
    messages = [
        {"role": "system", "content": XIANYU_PARSE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    content = provider.chat_completion(
        messages,
        json_object=True,
        timeout_seconds=timeout_seconds,
    )

    cleaned = _strip_markdown_json(content)
    data = _safe_json_loads(cleaned)

    if not isinstance(data, dict) or "orders" not in data:
        raise LLMProviderError(
            "schema_invalid",
            "LLM 输出缺少 orders 字段",
            cleaned[:200],
        )
    orders = data["orders"]
    if not isinstance(orders, list):
        raise LLMProviderError(
            "schema_invalid",
            "orders 字段不是 list",
            cleaned[:200],
        )
    return orders
