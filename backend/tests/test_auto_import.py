"""Auto-import backend tests (prd-006).

LLM calls are stubbed via monkeypatch of get_active_provider — no network.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Component, Product, ProductComponent
from app.services import auto_import, auto_import_llm
from app.services.auto_import import search_skus
from app.services.auto_import_llm import (
    LLMProviderError,
    match_listing_to_sku,
    parse_xianyu_screenshot,
)


# ---------- fixtures ----------

@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()
    yield session
    session.close()


class _FakeProvider:
    """Stub provider whose chat_completion returns a canned string."""

    def __init__(self, content: str):
        self._content = content
        self.last_call: dict | None = None

    def is_configured(self) -> bool:
        return True

    def chat_completion(self, messages, *, json_object=False, **kwargs):
        self.last_call = {
            "messages": messages,
            "json_object": json_object,
            "kwargs": kwargs,
        }
        return self._content


# ---------- SKU 匹配 ----------

class TestSkuMatch:
    CATALOG = [
        {"sku": "PR-0001", "name": "床头柜", "color": "白色,黑色"},
        {"sku": "PR-0002", "name": "餐桌椅", "color": ""},
    ]

    def test_normal_match_returns_sku_with_confidence(self, monkeypatch):
        fake = _FakeProvider(
            '{"matched_sku_code":"PR-0001","confidence":0.92,"reasoning":"标题里出现床头柜"}'
        )
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        sku, conf, reasoning = match_listing_to_sku("纯色床头柜 白色 现货", self.CATALOG)
        assert sku == "PR-0001"
        assert conf == pytest.approx(0.92)
        assert "床头柜" in reasoning
        # 验证 chat_completion 参数
        assert fake.last_call is not None
        assert fake.last_call["json_object"] is True

    def test_markdown_wrapped_json_strips_correctly(self, monkeypatch):
        wrapped = (
            '```json\n'
            '{"matched_sku_code":"PR-0002","confidence":0.7,"reasoning":"模糊匹配"}\n'
            '```'
        )
        fake = _FakeProvider(wrapped)
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        sku, conf, _ = match_listing_to_sku("餐桌", self.CATALOG)
        assert sku == "PR-0002"
        assert conf == pytest.approx(0.7)

    def test_low_confidence_returns_null_sku(self, monkeypatch):
        fake = _FakeProvider(
            '{"matched_sku_code":null,"confidence":0.3,"reasoning":"无明显匹配"}'
        )
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        sku, conf, _ = match_listing_to_sku("不相关的随机商品", self.CATALOG)
        assert sku is None
        assert conf == pytest.approx(0.3)

    def test_no_api_key_raises_provider_error(self, monkeypatch):
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: None)
        with pytest.raises(LLMProviderError) as ei:
            match_listing_to_sku("床头柜", self.CATALOG)
        assert ei.value.error_kind == "no_api_key"

    def test_json_parse_failure_raises(self, monkeypatch):
        fake = _FakeProvider("this is not json at all {broken")
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        with pytest.raises(LLMProviderError) as ei:
            match_listing_to_sku("床头柜", self.CATALOG)
        assert ei.value.error_kind == "parse_failed"


# ---------- 闲鱼截图解析 ----------

class TestXianyuParse:
    def test_valid_screenshot_returns_orders(self, monkeypatch):
        canned = (
            '{"orders":[{"external_order_id":"X-1","buyer_nickname":"小明",'
            '"external_created_at":"2026-06-15T12:00:00Z",'
            '"products":[{"listing_title":"床头柜 白色","quantity":1}]}]}'
        )
        fake = _FakeProvider(canned)
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        orders = parse_xianyu_screenshot(b"\x89PNG fake")
        assert len(orders) == 1
        assert orders[0]["external_order_id"] == "X-1"
        assert orders[0]["products"][0]["listing_title"] == "床头柜 白色"

    def test_schema_missing_orders_key_raises(self, monkeypatch):
        fake = _FakeProvider('{"unexpected":"shape"}')
        monkeypatch.setattr(auto_import_llm, "get_active_provider", lambda: fake)
        with pytest.raises(LLMProviderError) as ei:
            parse_xianyu_screenshot(b"\x89PNG fake")
        assert ei.value.error_kind == "schema_invalid"


# ---------- SKU 搜索 ----------

def _seed_product(db, sku: str, name: str, colors: list[str] | None = None) -> Product:
    p = Product(sku=sku, name=name)
    db.add(p)
    db.flush()
    if colors:
        # 需要一个 component 供 product_component 引用
        comp = Component(sku=f"C-{sku[-4:]}", name=f"{name}-组件", colors=colors)
        db.add(comp)
        db.flush()
        for color in colors:
            db.add(ProductComponent(
                product_id=p.id,
                component_id=comp.id,
                color=color,
                quantity=1,
            ))
    db.commit()
    return p


class TestSkuSearch:
    def test_chinese_substring_match(self, db):
        _seed_product(db, "PR-0001", "床头柜", ["白色", "黑色"])
        _seed_product(db, "PR-0002", "餐桌椅", [])
        hits = search_skus(db, "床头")
        assert len(hits) == 1
        assert hits[0]["sku"] == "PR-0001"
        assert hits[0]["name"] == "床头柜"
        # 颜色拼接 — 排序后 "白色,黑色" 实际按字典序为 "白色,黑色" 或 "黑色,白色"
        assert hits[0]["color"] is not None
        assert "白色" in hits[0]["color"]
        assert "黑色" in hits[0]["color"]

    def test_sku_code_exact_match(self, db):
        _seed_product(db, "PR-0001", "床头柜")
        _seed_product(db, "PR-0002", "餐桌椅")
        hits = search_skus(db, "PR-0002")
        assert len(hits) == 1
        assert hits[0]["sku"] == "PR-0002"
        assert hits[0]["color"] is None

    def test_empty_query_returns_empty(self, db):
        _seed_product(db, "PR-0001", "床头柜")
        assert search_skus(db, "") == []
        assert search_skus(db, "   ") == []

    def test_limit_caps_results(self, db):
        for i in range(5):
            _seed_product(db, f"PR-000{i + 1}", f"产品{i}")
        hits = search_skus(db, "产品", limit=3)
        assert len(hits) == 3
