"""Multi-provider vision LLM 抽象 + OpenAI 兼容协议实现

设计参考：docs/prd/prd-005-intake.md CUJ-2 / docs/design/design-intake.md §4-§5

架构：
- `OpenAICompatibleVisionProvider` 是通用 provider，接受 name/api_key/base_url/model 四元参数
- `PROVIDERS` 注册表登记每家已知 provider 的默认 base_url / model 与环境变量前缀
- `get_active_provider()` 读 `LLM_PROVIDER` env 变量选择激活的 provider，回退到对应前缀
  的 `<PREFIX>_API_KEY/_BASE_URL/_MODEL` 读取实际值；未配置 key 时返回 None

向后兼容：`LLM_PROVIDER` 未设置时默认 "deepseek"，仍读取 `DEEPSEEK_*` 环境变量，
旧测试和旧 .env 继续可用。`DeepSeekVisionProvider` 类保留为薄子类，便于测试 monkeypatch。
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
from typing import Optional

import httpx


# ---------- 异常 ----------

class LLMProviderError(Exception):
    """LLM provider 调用失败的统一异常。

    error_kind 与 RecognizeResponse.error_kind 的可选枚举一一对应，
    便于路由层直接序列化为 JSON 响应。
    """

    def __init__(self, error_kind: str, message: str, raw_preview: Optional[str] = None):
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message
        self.raw_preview = raw_preview


# ---------- SYSTEM PROMPT ----------

# 中文 system prompt — 严格要求 JSON 输出 + BOM 件数交叉验证 + 总时间字段定位
SYSTEM_PROMPT = """你是一个 3D 打印作坊的录入助理。你的任务是从拓竹切片软件导出的截图中提取产品结构化信息。

输入分两类图片：
1. **组装图（assembly）**：产品的多角度装配示意或爆炸图。你需要从多张组装图交叉验证 BOM 中每个组件的件数（同一组件在不同视角下可能可见数不同，取你最确信的数）。
2. **打印盘截图（produce）**：拓竹切片软件每个打印盘的预览。每张图右侧约 1/3 区域是耗材面板，**「总时间」**字段是你需要读取的耗时（不是「预计打印时间」，那个是不同字段）。**只读右侧面板里写着「总时间」三个字旁边的值。** 同时你需要数出这一盘里有几件同种组件。

输出严格的 JSON（不要 markdown 包装、不要解释文字），schema 如下：
{
  "product_base_name": "<产品中文基名，如『床头柜』；如果用户已指定，使用用户的值>",
  "components": [
    {"name": "<组件中文短名，不带产品基名前缀，如『侧板』『抽屉』>", "bom_quantity": <整数，每套产品需要的件数>}
  ],
  "plates": [
    {
      "source_index": <整数，对应 produce 图片的数组下标，从 0 开始>,
      "component_name": "<必须出现在 components[].name 中>",
      "quantity_per_plate": <整数，本盘内同种组件件数>,
      "duration_minutes": <整数，把 H/M/S 解析后换算到分钟，向上取整>
    }
  ]
}

要求：
- `components[].name` **不带产品基名前缀**（前缀由后处理拼接，避免你重复写）。
- 每个 `plate.component_name` 都必须能在 `components[].name` 中找到（严格相等）。
- 如果某个组件只出现在打印盘而你未在 BOM 中列出，也要把它补到 `components` 里。
- 输出必须是有效 JSON，键名严格匹配上面 schema，**不要**包 markdown 代码块。"""


# ---------- helpers ----------

_DURATION_RE = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.IGNORECASE)


def _parse_duration_to_minutes(value) -> int:
    """把 H/M/S 字符串或整数解析为分钟，向上取整。

    接受形如 `"2h43m"` `"17m45s"` `"1h"` `"45m"` `"90"` 或直接整数。
    解析失败抛 ValueError。
    """
    if isinstance(value, (int, float)):
        return int(math.ceil(float(value)))
    if not isinstance(value, str):
        raise ValueError(f"unsupported duration type: {type(value)}")
    s = value.strip().lower()
    if not s:
        raise ValueError("empty duration")
    # 纯数字字符串当分钟
    if s.isdigit():
        return int(s)
    m = _DURATION_RE.fullmatch(s.replace(" ", ""))
    if not m or not any(m.groups()):
        raise ValueError(f"unparseable duration: {value!r}")
    h = int(m.group(1) or 0)
    mm = int(m.group(2) or 0)
    ss = int(m.group(3) or 0)
    total = h * 60 + mm + math.ceil(ss / 60)
    return int(total)


def _strip_markdown_json(text: str) -> str:
    """剥可能的 ```json ... ``` 包裹。"""
    s = text.strip()
    if s.startswith("```"):
        # 找第一个换行，跳过首行 ``` 或 ```json
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        # 去掉末尾的 ```
        if s.rstrip().endswith("```"):
            s = s.rstrip()[: -3].rstrip()
    return s


def _image_bytes_to_data_url(image_bytes: bytes) -> str:
    """把图片字节转 base64 data URL（PNG 后端，LLM 不区分）。"""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------- Provider 注册表 ----------

# 每家 provider 的元信息：env 变量前缀 + 默认 base_url + 默认推荐 vision 模型
# 新增 provider 时在此追加一项，无需改其它代码。
PROVIDERS: dict[str, dict] = {
    "qwen": {
        "name": "Qwen (DashScope)",
        "env_prefix": "QWEN",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-vl-ocr",
    },
    "deepseek": {
        "name": "DeepSeek",
        "env_prefix": "DEEPSEEK",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-pro",
        "note": "DeepSeek 公网 API 实测不支持 vision（2026-06-14）— 仅留作历史/兜底",
    },
    "doubao": {
        "name": "Doubao (火山方舟)",
        "env_prefix": "DOUBAO",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-1-5-vision-pro-32k-250115",
    },
    "siliconflow": {
        "name": "SiliconFlow (硅基流动)",
        "env_prefix": "SILICONFLOW",
        "default_base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-VL-72B-Instruct",
        "note": "DeepSeek-VL2 超过 2 张图会自动降采样到 384×384 — 慎用",
    },
    "kimi": {
        "name": "Kimi (Moonshot)",
        "env_prefix": "KIMI",
        "default_base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-32k-vision-preview",
    },
    "openai": {
        "name": "OpenAI",
        "env_prefix": "OPENAI",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
}


# ---------- OpenAI 兼容 Vision Provider ----------

class OpenAICompatibleVisionProvider:
    """通用 OpenAI Chat Completions 兼容 vision provider。

    构造参数：
    - `name`：用于错误文案与 provider-status 响应显示
    - `api_key`：None 表示未配置（`is_configured()` 返回 False）
    - `base_url`：不含尾斜杠
    - `model`：模型 ID
    """

    def __init__(self, *, name: str, api_key: Optional[str], base_url: str, model: str):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def recognize(
        self,
        assembly_images: list[bytes],
        produce_images: list[bytes],
        product_base_name: Optional[str] = None,
    ) -> dict:
        """调用 vision API，返回 LLM 解析后的 dict（schema 见 SYSTEM_PROMPT）。

        失败时抛 LLMProviderError(error_kind, message, raw_preview)。
        """
        if not self.is_configured():
            raise LLMProviderError("no_api_key", f"{self.name} API key 未配置")

        # 构造 USER 消息内容
        n_assembly = len(assembly_images)
        n_produce = len(produce_images)
        text_parts = [
            f"下面共发送 {n_assembly} 张组装图 + {n_produce} 张打印盘截图。",
        ]
        if product_base_name:
            text_parts.append(f"用户指定的产品基名：{product_base_name}")
        text_parts.append(f"组装图索引 0 ~ {max(n_assembly - 1, 0)}（共 {n_assembly} 张）")
        text_parts.append(
            f"打印盘截图索引 0 ~ {max(n_produce - 1, 0)}（共 {n_produce} 张，"
            "plates[].source_index 即对应此索引）"
        )

        user_content: list[dict] = [{"type": "text", "text": "\n".join(text_parts)}]
        for img in assembly_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": _image_bytes_to_data_url(img)},
            })
        for img in produce_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": _image_bytes_to_data_url(img)},
            })

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMProviderError("timeout", f"{self.name} 请求超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError("http_5xx", f"{self.name} 网络错误：{exc}") from exc

        # 检查 HTTP 状态码
        status = resp.status_code
        if status in (401, 403):
            raw_preview = (resp.text or "")[:200]
            raise LLMProviderError(
                "http_401",
                f"{self.name} 拒绝请求 (HTTP {status}) — API key 无效或额度耗尽",
                raw_preview,
            )
        if 500 <= status < 600:
            raw_preview = (resp.text or "")[:200]
            raise LLMProviderError(
                "http_5xx",
                f"{self.name} 服务异常 (HTTP {status})",
                raw_preview,
            )
        if status >= 400:
            raw_preview = (resp.text or "")[:200]
            # 图片过大特殊处理
            if "image_too_large" in (resp.text or "").lower():
                raise LLMProviderError(
                    "image_too_large",
                    f"图片过大被 {self.name} 拒绝 (HTTP {status})",
                    raw_preview,
                )
            raise LLMProviderError(
                "http_5xx",
                f"{self.name} 返回 HTTP {status}",
                raw_preview,
            )

        body_text = resp.text or ""
        # 检测 image_too_large 子串（可能 200 但内嵌错误）
        if "image_too_large" in body_text.lower():
            raise LLMProviderError(
                "image_too_large",
                f"{self.name} 报告图片过大",
                body_text[:200],
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMProviderError(
                "parse_failed",
                f"{self.name} 响应非 JSON：{exc}",
                body_text[:200],
            ) from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                "schema_invalid",
                f"{self.name} 响应结构异常：缺少 choices[0].message.content",
                body_text[:200],
            ) from exc

        if not isinstance(content, str):
            raise LLMProviderError(
                "schema_invalid",
                f"{self.name} message.content 非字符串",
                body_text[:200],
            )

        cleaned = _strip_markdown_json(content)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                "parse_failed",
                f"LLM 输出非合法 JSON：{exc}",
                cleaned[:200],
            ) from exc

        # ---- schema 校验 ----
        if not isinstance(parsed, dict):
            raise LLMProviderError(
                "schema_invalid",
                "LLM 输出顶层不是 object",
                cleaned[:200],
            )

        components = parsed.get("components")
        plates = parsed.get("plates")
        if not isinstance(components, list) or not isinstance(plates, list):
            raise LLMProviderError(
                "schema_invalid",
                "LLM 输出缺少 components / plates 列表",
                cleaned[:200],
            )

        # 校验 components
        component_names: set[str] = set()
        for comp in components:
            if not isinstance(comp, dict):
                raise LLMProviderError(
                    "schema_invalid",
                    "components 元素不是 object",
                    cleaned[:200],
                )
            name = comp.get("name")
            qty = comp.get("bom_quantity")
            if not isinstance(name, str) or not name.strip():
                raise LLMProviderError(
                    "schema_invalid",
                    "component.name 非合法字符串",
                    cleaned[:200],
                )
            if not isinstance(qty, int) or isinstance(qty, bool):
                # 允许 LLM 返回 float 整数值
                if isinstance(qty, float) and qty.is_integer():
                    comp["bom_quantity"] = int(qty)
                else:
                    raise LLMProviderError(
                        "schema_invalid",
                        f"component.bom_quantity 非整数: {qty!r}",
                        cleaned[:200],
                    )
            component_names.add(name)

        # 校验 plates
        for plate in plates:
            if not isinstance(plate, dict):
                raise LLMProviderError(
                    "schema_invalid",
                    "plates 元素不是 object",
                    cleaned[:200],
                )
            si = plate.get("source_index")
            cn = plate.get("component_name")
            qpp = plate.get("quantity_per_plate")
            dur = plate.get("duration_minutes")
            if not isinstance(si, int) or isinstance(si, bool):
                raise LLMProviderError(
                    "schema_invalid",
                    f"plate.source_index 非整数: {si!r}",
                    cleaned[:200],
                )
            if not isinstance(cn, str) or not cn.strip():
                raise LLMProviderError(
                    "schema_invalid",
                    "plate.component_name 非合法字符串",
                    cleaned[:200],
                )
            if cn not in component_names:
                raise LLMProviderError(
                    "schema_invalid",
                    f"plate.component_name {cn!r} 未出现在 components 中",
                    cleaned[:200],
                )
            if not isinstance(qpp, int) or isinstance(qpp, bool):
                if isinstance(qpp, float) and qpp.is_integer():
                    plate["quantity_per_plate"] = int(qpp)
                else:
                    raise LLMProviderError(
                        "schema_invalid",
                        f"plate.quantity_per_plate 非整数: {qpp!r}",
                        cleaned[:200],
                    )
            # duration_minutes 允许 LLM 返回字符串或整数，统一转分钟
            try:
                plate["duration_minutes"] = _parse_duration_to_minutes(dur)
            except (ValueError, TypeError) as exc:
                raise LLMProviderError(
                    "schema_invalid",
                    f"plate.duration_minutes 解析失败: {dur!r} ({exc})",
                    cleaned[:200],
                ) from exc

        return parsed


# ---------- provider 选择 ----------

# 当 LLM_PROVIDER 未设置时使用的默认 provider key（向后兼容旧 .env）
_DEFAULT_PROVIDER_KEY = "deepseek"


def _build_from_spec(spec: dict) -> "OpenAICompatibleVisionProvider":
    """根据 PROVIDERS 注册表的元信息构造 provider，读取对应前缀的 env 变量。"""
    prefix = spec["env_prefix"]
    return OpenAICompatibleVisionProvider(
        name=spec["name"],
        api_key=os.environ.get(f"{prefix}_API_KEY"),
        base_url=os.environ.get(f"{prefix}_BASE_URL") or spec["default_base_url"],
        model=os.environ.get(f"{prefix}_MODEL") or spec["default_model"],
    )


def get_active_provider() -> Optional[OpenAICompatibleVisionProvider]:
    """返回当前激活的 provider，未配置 API key 或 LLM_PROVIDER 无效时返回 None。

    优先级：
    1. 读取环境变量 `LLM_PROVIDER`（例如 `qwen` / `doubao` / `deepseek` / ...）
    2. 未设置时回退 `_DEFAULT_PROVIDER_KEY`，保证旧 .env（只配 DEEPSEEK_*）继续可用
    3. 找到对应 PROVIDERS 注册项后读 `<PREFIX>_API_KEY/_BASE_URL/_MODEL`
    4. 特殊情况：deepseek 走 `DeepSeekVisionProvider` 子类（便于测试 monkeypatch）
    """
    active_key = (os.environ.get("LLM_PROVIDER") or _DEFAULT_PROVIDER_KEY).strip().lower()
    if active_key not in PROVIDERS:
        return None

    if active_key == "deepseek":
        provider = DeepSeekVisionProvider()
    else:
        provider = _build_from_spec(PROVIDERS[active_key])

    if not provider.is_configured():
        return None
    return provider


# ---------- 向后兼容：DeepSeekVisionProvider ----------

class DeepSeekVisionProvider(OpenAICompatibleVisionProvider):
    """[已弃用] 旧测试和旧 .env 入口 — 自动从 DEEPSEEK_* 环境变量构造。

    新代码请用 `get_active_provider()` 或直接实例化
    `OpenAICompatibleVisionProvider(name=, api_key=, base_url=, model=)`。

    保留此类是为了让旧测试中 `monkeypatch.setattr(DeepSeekVisionProvider, "recognize", ...)`
    模式继续工作 — 不能简单做成函数或 alias。
    """

    def __init__(self):
        spec = PROVIDERS["deepseek"]
        super().__init__(
            name=spec["name"],
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url=os.environ.get("DEEPSEEK_BASE_URL") or spec["default_base_url"],
            # 兼容旧变量名 DEEPSEEK_VISION_MODEL，同时支持新统一变量名 DEEPSEEK_MODEL
            model=(
                os.environ.get("DEEPSEEK_MODEL")
                or os.environ.get("DEEPSEEK_VISION_MODEL")
                or spec["default_model"]
            ),
        )
