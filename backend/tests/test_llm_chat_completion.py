"""OpenAICompatibleVisionProvider.chat_completion() 单元测试

覆盖：
- 正常 200 返回 message.content
- 401 → http_401
- TimeoutException → timeout
- json_object=True / False 时 payload 是否包含 response_format
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.intake_llm import (
    LLMProviderError,
    OpenAICompatibleVisionProvider,
)


class _MockResponse:
    def __init__(self, status_code: int, text: str = "", json_data: Any = None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is not None:
            return self._json_data
        import json as _json
        return _json.loads(self.text)


def _make_provider() -> OpenAICompatibleVisionProvider:
    return OpenAICompatibleVisionProvider(
        name="TestProvider",
        api_key="sk-test",
        base_url="https://example.test/v1",
        model="test-model",
    )


def test_chat_completion_returns_content_on_200(monkeypatch):
    provider = _make_provider()
    payload = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}

    def fake_post(self, url, json=None, headers=None):  # noqa: A002
        return _MockResponse(200, json_data=payload)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = provider.chat_completion([{"role": "user", "content": "hi"}])
    assert result == "hello"


def test_chat_completion_http_401_raises(monkeypatch):
    provider = _make_provider()

    def fake_post(self, url, json=None, headers=None):  # noqa: A002
        return _MockResponse(401, text='{"error":"unauthorized"}')

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    with pytest.raises(LLMProviderError) as ei:
        provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.error_kind == "http_401"
    assert ei.value.raw_preview is not None


def test_chat_completion_timeout_raises(monkeypatch):
    provider = _make_provider()

    def fake_post(self, url, json=None, headers=None):  # noqa: A002
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    with pytest.raises(LLMProviderError) as ei:
        provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.error_kind == "timeout"


def test_chat_completion_json_object_true_sends_response_format(monkeypatch):
    provider = _make_provider()
    captured: dict = {}
    payload_resp = {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(self, url, json=None, headers=None):  # noqa: A002
        captured["body"] = json
        return _MockResponse(200, json_data=payload_resp)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    provider.chat_completion(
        [{"role": "user", "content": "hi"}],
        json_object=True,
    )
    assert captured["body"].get("response_format") == {"type": "json_object"}


def test_chat_completion_json_object_false_omits_response_format(monkeypatch):
    provider = _make_provider()
    captured: dict = {}
    payload_resp = {"choices": [{"message": {"content": "free text"}}]}

    def fake_post(self, url, json=None, headers=None):  # noqa: A002
        captured["body"] = json
        return _MockResponse(200, json_data=payload_resp)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    provider.chat_completion(
        [{"role": "user", "content": "hi"}],
        json_object=False,
    )
    assert "response_format" not in captured["body"]


def test_chat_completion_no_api_key_raises():
    provider = OpenAICompatibleVisionProvider(
        name="TestProvider",
        api_key=None,
        base_url="https://example.test/v1",
        model="test-model",
    )
    with pytest.raises(LLMProviderError) as ei:
        provider.chat_completion([{"role": "user", "content": "hi"}])
    assert ei.value.error_kind == "no_api_key"
