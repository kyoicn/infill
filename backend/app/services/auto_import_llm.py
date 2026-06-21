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

XIANYU_PARSE_SYSTEM_PROMPT = """你是一个 3D 打印作坊的订单录入助理。任务：从用户上传的**闲鱼订单详情页**截屏中提取单条订单的结构化信息。

详情页布局（参考）：
- 页面顶部标题区域：「买家已付款，请尽快发货」之类
- 收货地址卡片（姓名、电话、地址）
- 商品卡片：商品标题 + 颜色规格 + 单价
- 价格汇总：成交价 / 商品总价 / 运费
- 关键字段区：「订单编号」「支付宝交易号」「买家昵称」「下单时间」「付款时间」

输出严格 JSON（不要 markdown 包装、不要解释文字），schema：
{
  "orders": [
    {
      "external_order_id": "<闲鱼订单编号，纯数字字符串，例如 3309218220653027889；不是支付宝交易号>",
      "buyer_nickname": "<「买家昵称」字段的值>",
      "external_created_at": "<「下单时间」字段，ISO-8601 字符串如 2026-06-18T16:17:44；找不到填 null>",
      "recipient_name": "<收货地址卡片中的姓名（首字段，如「徐」或「江城宁静的山楂」）；找不到填 null>",
      "recipient_phone": "<收货地址卡片中的手机号（纯数字，11 位）；找不到填 null>",
      "recipient_address": "<收货地址卡片中的完整地址（省+市+区+街道+详细，如「湖南省邵阳市武冈市水西门街道丰仁路辰信金城普岭社区驿站」）；找不到填 null>",
      "products": [
        {
          "listing_title": "<商品标题完整文本，包含【】中的分类前缀；末尾追加颜色规格，例如：【微缩娃屋家具】电脑桌转角书桌 白柜体+棕色桌板>",
          "quantity": 1
        }
      ]
    }
  ]
}

要求：
- 单张详情截屏通常对应**单条订单**，所以 `orders` 数组长度通常是 1。
- 商品标题要包含「【...】」分类前缀（如「【微缩娃屋家具】」），末尾把颜色规格（"颜色:白柜体+棕色桌板"中的颜色部分）追加到标题后，方便后续 SKU 匹配。
- quantity：详情页通常不显示件数 → 默认填 1。
- 「订单编号」是纯数字，长度 19 位左右；不要混淆为「支付宝交易号」（更长）或「商品编号」。
- 「下单时间」如果只有日期没有时间，时间补 "00:00:00"。
- 若整张图不是闲鱼订单详情页（比如截到了别的 App / 闲鱼首页等），返回 `{"orders": []}`。"""


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
        {"type": "text", "text": "下面是一张闲鱼**订单详情页**截图，请按 schema 输出 JSON。"},
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


# ---------- 闲鱼自动化：定位 UI 元素 ----------

XIANYU_DETECT_LIST_PROMPT = """你是 Android UI 视觉助理。任务：分析一张**闲鱼 App「卖出/待发货」列表页**截屏，找出当前画面里**所有完整可见的订单卡片**的中心点像素坐标。

闲鱼订单卡片的视觉特征：
- 每张卡片占据屏幕全宽，上下卡片之间有明显灰色分隔。
- 卡片顶部是买家头像 + 昵称 + 「等待卖家发货」状态文字；
- 卡片中部是商品缩略图 + 商品标题 + 价格；
- 卡片底部是「更多 / 求小红花 / 联系买家 / 去发货」四个操作按钮（高危！）。

🚨 **严禁返回的危险区域** 🚨：
- 「去发货」黄色按钮（点了会发起发货流程，无法撤销）
- 「联系买家」按钮（会打开聊天）
- 「求小红花」按钮（会发请求）
- 「更多」按钮（会弹菜单）
- 任何位于卡片**底部 1/3** 的位置都视为按钮高危带，绝对禁止

安全策略：
- 「完整可见」= 卡片的顶部和底部都没被屏幕边缘 / 顶部 tab 栏 / 底部导航 / 被遮挡。半截露出的卡片不算。
- 中心点坐标用**原始截屏像素坐标**（不是缩放后），原点在左上角。
- 中心点必须取**商品缩略图所在那一行的垂直中心**——这是卡片的「商品图区」，离按钮远，点击会进入详情页。
- 宁可错过一张卡（漏扫一单可手动补），也不能让 y 坐标落在卡片底部的按钮行附近。

输出严格 JSON（不要 markdown 包装、不要解释文字），schema：
{
  "card_x": <int，卡片中心列 x 坐标，所有卡片共享同一个 x>,
  "card_centers_y": [<int>, <int>, ...],
  "card_height_px": <int，相邻卡片中心点 y 差值的中位数，用于后续滚动；如果只看见一张卡填 0>
}

如果整张图不是闲鱼待发货列表页（比如截到了详情页 / 其他 App / 闲鱼首页），返回 `{"card_x": 0, "card_centers_y": [], "card_height_px": 0}`。"""


XIANYU_DETECT_EXPAND_PROMPT = """你是 Android UI 视觉助理。任务：分析一张**闲鱼订单详情页**截屏，找出「订单编号」那一行右侧那个**可点击的展开/折叠箭头**的中心点像素坐标。

闲鱼订单详情页的特征：
- 顶部「买家已付款，请尽快发货」/「等待卖家发货」之类的状态条
- 收货地址卡片（姓名 + 电话 + 地址）
- 商品卡片
- 价格汇总
- **关键行**：标签写「订单编号」，紧跟一串 19 位左右的纯数字，最右侧有一个小箭头（^ 或 v），可点击展开 / 收起「交易快照」「支付宝交易号」「买家昵称」「下单时间」「付款时间」几行。
- 屏幕**底部一条操作栏**：「联系买家 / 取消订单 / 去发货」（高危按钮）。

🚨 **严禁返回的危险区域** 🚨：
- 「去发货」黄色按钮（点了会发起发货流程，无法撤销）
- 「取消订单」按钮（点了会取消订单）
- 「联系买家」按钮（会打开聊天）
- 屏幕**底部约 1/8 高度**（约 200~300 px）整条都属于这个操作栏的高危带，绝对禁止把 y 坐标落进去。
- 「免费领」绿色按钮（蚂蚁森林广告，没用且会跳走）

要求：
- 中心点坐标用**原始截屏像素坐标**，原点在左上角。
- 点击坐标尽量靠近箭头本身，但落在「订单编号」整行的 hit-area 里也能展开 — 所以可以**就取整行的几何中心 y、x 取「订单编号」字样和数字之间的空白 x**，最稳。
- 「订单编号」行通常位于详情页**中部**（地址 + 商品 + 价格汇总之后），y 应远离屏幕底部。
- 不要选中支付宝交易号那一行的箭头（如果有的话），只要订单编号那一行。
- 找不到合法的「订单编号」展开行，宁可返回 0/0 让系统报错，也绝对不能返回靠近底部操作栏的坐标。

输出严格 JSON（不要 markdown 包装、不要解释文字），schema：
{
  "x": <int，点击中心 x>,
  "y": <int，「订单编号」一行的中心 y>
}

如果整张图不是闲鱼订单详情页或者没看到「订单编号」标签，返回 `{"x": 0, "y": 0}`。"""


def _vision_json_call(
    image_bytes: bytes,
    system_prompt: str,
    user_hint: str,
    *,
    timeout_seconds: int = 30,
) -> dict:
    """Run a vision LLM call expecting a single JSON object response."""
    provider = get_active_provider()
    if provider is None:
        raise LLMProviderError("no_api_key", "未配置 LLM API key", "")

    user_content = [
        {"type": "text", "text": user_hint},
        {
            "type": "image_url",
            "image_url": {"url": _image_bytes_to_data_url(image_bytes)},
        },
    ]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    content = provider.chat_completion(
        messages,
        json_object=True,
        timeout_seconds=timeout_seconds,
    )
    cleaned = _strip_markdown_json(content)
    data = _safe_json_loads(cleaned)
    if not isinstance(data, dict):
        raise LLMProviderError("schema_invalid", "顶层不是 object", cleaned[:200])
    return data


def _image_size_from_bytes(image_bytes: bytes) -> tuple[int, int]:
    """读 PNG 头部拿到 (W, H)；失败时返回 (0, 0)。"""
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(image_bytes)) as im:
            return im.size  # (W, H)
    except Exception:  # noqa: BLE001
        return (0, 0)


def _maybe_unnormalize(values: list[int], full: int) -> list[int]:
    """Qwen 视觉模型常给 0-1000 归一化坐标——若所有值都 ≤1000 且 full > 1000，
    按 full/1000 比例还原回像素空间。"""
    if not values or full <= 1000:
        return values
    if all(0 <= v <= 1000 for v in values):
        return [int(v * full / 1000) for v in values]
    return values


def detect_xianyu_list_layout(image_bytes: bytes) -> dict:
    """从一张闲鱼列表页截屏推断卡片中心点像素坐标。

    返回 `{"card_x": int, "card_centers_y": list[int], "card_height_px": int}`。
    LLM (Qwen3-Omni-Flash 实测) 一般以 0-1000 归一化空间作答 —— 这里做反归一化。
    """
    data = _vision_json_call(
        image_bytes,
        XIANYU_DETECT_LIST_PROMPT,
        "下面是闲鱼「卖出 / 待发货」列表页截屏，请按 schema 输出 JSON。",
    )

    try:
        card_x_raw = int(data.get("card_x") or 0)
        centers_raw = data.get("card_centers_y") or []
        if not isinstance(centers_raw, list):
            raise ValueError("card_centers_y 不是 list")
        centers_int = [int(v) for v in centers_raw]
        h_raw = int(data.get("card_height_px") or 0)
    except (TypeError, ValueError) as exc:
        raise LLMProviderError(
            "schema_invalid",
            f"detect_xianyu_list_layout 字段类型不对：{exc}",
            json.dumps(data)[:200],
        ) from exc

    img_w, img_h = _image_size_from_bytes(image_bytes)
    # 反归一化：x 用 W，y / 高度差 用 H
    card_x_arr = _maybe_unnormalize([card_x_raw], img_w) if img_w else [card_x_raw]
    centers = _maybe_unnormalize(centers_int, img_h) if img_h else centers_int
    h_arr = _maybe_unnormalize([h_raw], img_h) if img_h else [h_raw]

    return {
        "card_x": card_x_arr[0],
        "card_centers_y": centers,
        "card_height_px": h_arr[0],
    }


def detect_xianyu_expand_button(image_bytes: bytes) -> dict:
    """从一张闲鱼详情页截屏推断「订单编号」展开按钮位置。

    返回 `{"x": int, "y": int}`，未找到时 0/0。
    LLM 输出按需反归一化（0-1000 → 像素）。
    """
    data = _vision_json_call(
        image_bytes,
        XIANYU_DETECT_EXPAND_PROMPT,
        "下面是闲鱼订单详情页截屏，请按 schema 找出「订单编号」展开按钮坐标，输出 JSON。",
    )
    try:
        x_raw = int(data.get("x") or 0)
        y_raw = int(data.get("y") or 0)
    except (TypeError, ValueError) as exc:
        raise LLMProviderError(
            "schema_invalid",
            f"detect_xianyu_expand_button 字段类型不对：{exc}",
            json.dumps(data)[:200],
        ) from exc

    img_w, img_h = _image_size_from_bytes(image_bytes)
    x = _maybe_unnormalize([x_raw], img_w)[0] if img_w else x_raw
    y = _maybe_unnormalize([y_raw], img_h)[0] if img_h else y_raw
    return {"x": x, "y": y}
