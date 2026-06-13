"""产品录入（intake）LLM provider 抽象

CUJ-2 真实识别实现归后续任务（T5）；本文件先固化抽象 + DeepSeek 占位实现 + provider 注册表。

设计目标：未来再加 provider（如 GPT-4o）时，只在 _REGISTERED 加一项即可；
provider 优先级 = 列表顺序，第一个 is_configured() == True 的就是活跃 provider。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class LLMProviderError(Exception):
    """LLM provider 调用失败的统一异常类型。

    error_kind: 与 schemas_intake.RecognizeResponse.error_kind 对齐
        （"no_api_key" / "http_401" / "http_5xx" / "timeout" / "parse_failed" / ...）
    message: 用户友好错误描述
    raw_preview: parse_failed 等场景下原始响应片段（最多 200 字符）
    """

    def __init__(self, error_kind: str, message: str, raw_preview: str | None = None):
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message
        self.raw_preview = raw_preview


class LLMVisionProvider(ABC):
    """Vision-capable LLM provider 抽象。"""

    name: str = ""

    @abstractmethod
    def is_configured(self) -> bool:
        """是否已就绪可用（通常检查 API key 环境变量）。"""

    @abstractmethod
    def recognize(self, *args, **kwargs) -> dict:
        """对一组截图执行识别，返回结构化草稿数据（具体契约见 design-intake.md）。

        失败时 raise LLMProviderError。
        """


class DeepSeekVisionProvider(LLMVisionProvider):
    """DeepSeek vision provider（MVP 唯一支持的 provider）。"""

    name = "DeepSeek"

    def is_configured(self) -> bool:
        return bool(os.environ.get("DEEPSEEK_API_KEY"))

    def recognize(self, *args, **kwargs) -> dict:
        raise NotImplementedError("CUJ-2 任务 T5 实现")


# Provider 注册表 — 顺序即优先级，第一个 is_configured() 的即活跃 provider
_REGISTERED: list[type[LLMVisionProvider]] = [DeepSeekVisionProvider]


def get_active_provider() -> LLMVisionProvider | None:
    """返回第一个 is_configured() == True 的 provider 实例；都未就绪则返回 None。"""
    for provider_cls in _REGISTERED:
        provider = provider_cls()
        if provider.is_configured():
            return provider
    return None
