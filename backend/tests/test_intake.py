"""产品录入（intake）后端测试

覆盖：
- 启发式分类器（heuristic_classify）：真实样例 + 合成边界
- POST /api/intake/upload：multipart 上传 + 分类 + 落盘
- GET /api/intake/provider-status：DEEPSEEK_API_KEY 在场/不在场两条路径
- cleanup_stale_sessions：TTL 过期清理
- DeepSeek provider 错误映射（401/5xx/timeout/parse_failed/schema_invalid）
- POST /api/intake/recognize：happy path（前缀拼接、盘号默认、source_image_id 反查）+ no_api_key 分支
- detect_conflicts 服务（DB 撞名查询）

LLM 调用一律 mock，不依赖网络。"""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Component, PrintConfig
from app.services import intake as intake_service
from app.services import intake_llm
from app.services.intake import (
    INTAKE_TMP_DIR,
    TTL_SECONDS,
    cleanup_stale_sessions,
    detect_conflicts,
    heuristic_classify,
)
from app.services.intake_llm import (
    DeepSeekVisionProvider,
    LLMProviderError,
    _parse_duration_to_minutes,
    _strip_markdown_json,
)


# G1 占位 smoke 测试 — 保留
def test_smoke():
    assert True


# === 合成 fixture ===

def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_white_image(size: tuple[int, int] = (800, 600)) -> bytes:
    """全白图 — 右上区域亮度高 → 应分类为 assembly。"""
    img = Image.new("RGB", size, color=(255, 255, 255))
    return _png_bytes(img)


def _make_image_with_dark_right_panel(size: tuple[int, int] = (800, 600)) -> bytes:
    """主体白底 + 右上区域深色块 — 模拟切片软件耗材面板 → 应分类为 produce。"""
    img = Image.new("RGB", size, color=(255, 255, 255))
    # 在 PRODUCE_PANEL_REGION 范围内涂深色（覆盖整个判定区域）
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    w, h = size
    left = int(w * 0.70)   # 略宽于检测区域
    top = int(h * 0.00)
    right = int(w * 1.00)
    bottom = int(h * 0.32)
    draw.rectangle((left, top, right, bottom), fill=(10, 10, 10))
    return _png_bytes(img)


# === TestHeuristicClassifier ===

# 真实样例路径 — 用户本地 data/intake/床头柜/ 下的样图（gitignored；CI 没有则 skip）
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SAMPLE_ASSEMBLY = _REPO_ROOT / "data" / "intake" / "床头柜" / "assembly" / "assembly.png"
_SAMPLE_PRODUCE_DIR = _REPO_ROOT / "data" / "intake" / "床头柜" / "produce"


@pytest.mark.skipif(
    not _SAMPLE_ASSEMBLY.is_file(),
    reason="缺少真实样例 data/intake/床头柜/assembly/assembly.png（gitignored，仅本地）",
)
def test_classify_real_assembly_sample():
    content = _SAMPLE_ASSEMBLY.read_bytes()
    assert heuristic_classify(content) == "assembly"


def _produce_samples() -> list[Path]:
    if not _SAMPLE_PRODUCE_DIR.is_dir():
        return []
    return sorted(p for p in _SAMPLE_PRODUCE_DIR.glob("*.png"))


@pytest.mark.skipif(
    len(_produce_samples()) < 4,
    reason="缺少真实样例 data/intake/床头柜/produce/*.png ≥ 4 张（gitignored，仅本地）",
)
@pytest.mark.parametrize("sample_path", _produce_samples()[:8])
def test_classify_real_produce_sample(sample_path: Path):
    content = sample_path.read_bytes()
    assert heuristic_classify(content) == "produce", (
        f"样例 {sample_path.name} 期望 produce，实际为 assembly"
    )


def test_classify_synthetic_white_image_is_assembly():
    """合成全白图：右上区域亮度高 → assembly。"""
    content = _make_white_image()
    assert heuristic_classify(content) == "assembly"


def test_classify_synthetic_dark_panel_image_is_produce():
    """合成右上深色面板图：模拟切片软件耗材面板 → produce。"""
    content = _make_image_with_dark_right_panel()
    assert heuristic_classify(content) == "produce"


# === TestProviderStatus ===

@pytest.fixture
def client():
    return TestClient(app)


def test_provider_status_configured(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    resp = client.get("/api/intake/provider-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["provider_name"] == "DeepSeek"


def test_provider_status_not_configured(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    resp = client.get("/api/intake/provider-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["configured"] is False
    assert body["provider_name"] is None


# === TestUploadEndpoint ===

def test_upload_single_png(client, tmp_path, monkeypatch):
    # 重定向 INTAKE_TMP_DIR 到 tmp_path 隔离测试副作用
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")

    content = _make_white_image()
    resp = client.post(
        "/api/intake/upload",
        files=[("files", ("test.png", content, "image/png"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["images"]) == 1
    img = body["images"][0]
    assert img["suggested_class"] in {"assembly", "produce"}
    assert img["filename"] == "test.png"
    assert img["width"] == 800
    assert img["height"] == 600
    assert img["image_id"]
    assert body["session_id"]

    # 落盘文件存在
    session_dir = tmp_path / "intake_tmp" / body["session_id"]
    assert session_dir.is_dir()
    files_in_session = list(session_dir.iterdir())
    assert len(files_in_session) == 1
    assert files_in_session[0].suffix == ".png"
    assert files_in_session[0].stem == img["image_id"]


def test_upload_multiple_mixed_mime(client, tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")

    png_content = _make_white_image()
    jpg_buf = io.BytesIO()
    Image.new("RGB", (640, 480), color=(255, 255, 255)).save(jpg_buf, format="JPEG")
    jpg_content = jpg_buf.getvalue()

    resp = client.post(
        "/api/intake/upload",
        files=[
            ("files", ("a.png", png_content, "image/png")),
            ("files", ("b.jpg", jpg_content, "image/jpeg")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["images"]) == 2
    session_dir = tmp_path / "intake_tmp" / body["session_id"]
    assert len(list(session_dir.iterdir())) == 2


def test_upload_rejects_invalid_mime(client, tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")

    resp = client.post(
        "/api/intake/upload",
        files=[("files", ("evil.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_kind"] == "invalid_mime"
    assert "application/pdf" in body["error"]


def test_upload_rejects_too_large(client, tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")
    # 阈值降到 100 字节方便测试，避免实际造一个 10MB 文件
    monkeypatch.setattr(intake_service, "MAX_UPLOAD_BYTES", 100)
    # router 模块缓存了 MAX_UPLOAD_BYTES 的 import，所以同样得 patch 路由那一份
    from app.routers import intake as intake_router

    monkeypatch.setattr(intake_router, "MAX_UPLOAD_BYTES", 100)

    big_content = _make_white_image(size=(200, 200))  # PNG 通常 > 100 字节
    assert len(big_content) > 100
    resp = client.post(
        "/api/intake/upload",
        files=[("files", ("big.png", big_content, "image/png"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_kind"] == "too_large"


def test_upload_rejects_path_traversal_session_id(client, tmp_path, monkeypatch):
    """非 uuid4 hex 的 session_id（如 ../etc/passwd）必须被拒绝 —— 防路径遍历。"""
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")

    content = _make_white_image()
    resp = client.post(
        "/api/intake/upload",
        files=[("files", ("a.png", content, "image/png"))],
        data={"session_id": "../../../etc/passwd"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_kind"] == "invalid_session_id"


def test_upload_session_id_reuse(client, tmp_path, monkeypatch):
    """传入相同 session_id 时，第二次上传落到同一子目录。"""
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")

    content = _make_white_image()
    resp1 = client.post(
        "/api/intake/upload",
        files=[("files", ("a.png", content, "image/png"))],
    )
    sid = resp1.json()["session_id"]

    resp2 = client.post(
        "/api/intake/upload",
        files=[("files", ("b.png", content, "image/png"))],
        data={"session_id": sid},
    )
    assert resp2.status_code == 200
    assert resp2.json()["session_id"] == sid

    session_dir = tmp_path / "intake_tmp" / sid
    assert len(list(session_dir.iterdir())) == 2


# === TestSessionImageEndpoint (v0.2.3 — /api/intake/session-image) ===

def test_session_image_serves_uploaded_png(client, tmp_path, monkeypatch):
    """快乐路径：上传一张 PNG 后能从 session-image 端点读回相同字节。"""
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")
    # router 模块也持有 INTAKE_TMP_DIR 引用（路由层 import 进来的常量）
    from app.routers import intake as intake_router
    monkeypatch.setattr(intake_router, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")

    content = _make_white_image()
    up = client.post(
        "/api/intake/upload",
        files=[("files", ("test.png", content, "image/png"))],
    ).json()
    sid = up["session_id"]
    iid = up["images"][0]["image_id"]

    resp = client.get(f"/api/intake/session-image/{sid}/{iid}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == content


def test_session_image_rejects_path_traversal_session_id(client):
    """路径遍历防御：非 uuid4 hex 的 session_id 返回 404，不尝试任何文件读取。"""
    resp = client.get("/api/intake/session-image/..%2F..%2Fetc/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert resp.status_code == 404
    # 用合法长度但含非 hex 字符
    resp = client.get(
        "/api/intake/session-image/"
        + "g" * 32 + "/" + "f" * 32
    )
    assert resp.status_code == 404


def test_session_image_returns_404_for_missing(client, tmp_path, monkeypatch):
    """合法 uuid4 hex 但文件不存在 → 404，不抛 500。"""
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")
    from app.routers import intake as intake_router
    monkeypatch.setattr(intake_router, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")
    resp = client.get(
        "/api/intake/session-image/" + "a" * 32 + "/" + "b" * 32
    )
    assert resp.status_code == 404


# === TestCleanupStaleSessions ===

def test_cleanup_stale_sessions_removes_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")
    tmp_root = tmp_path / "intake_tmp"
    tmp_root.mkdir(parents=True)

    fresh = tmp_root / "fresh_session"
    fresh.mkdir()
    (fresh / "x.png").write_bytes(b"x")

    stale = tmp_root / "stale_session"
    stale.mkdir()
    (stale / "y.png").write_bytes(b"y")

    now = time.time()
    # 把 stale 的 mtime 设到 TTL + 一小时之前
    old = now - TTL_SECONDS - 3600
    os.utime(stale, (old, old))

    cleaned = cleanup_stale_sessions(now=now)
    assert cleaned == 1
    assert fresh.is_dir()
    assert not stale.exists()


def test_cleanup_stale_sessions_handles_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "does_not_exist")
    assert cleanup_stale_sessions() == 0


def test_cleanup_stale_sessions_keeps_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_path / "intake_tmp")
    tmp_root = tmp_path / "intake_tmp"
    tmp_root.mkdir()
    recent = tmp_root / "recent"
    recent.mkdir()
    (recent / "z.png").write_bytes(b"z")
    cleaned = cleanup_stale_sessions()
    assert cleaned == 0
    assert recent.is_dir()
# ---------- 测试 fixture：内存 SQLite + intake_tmp 隔离 ----------

@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    """内存 SQLite + 临时 intake_tmp 目录，每个测试独立。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()

    # 路由经由 Depends(get_db)，需 override
    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    # intake_tmp 指向 tmp_path，避免污染真实 data 目录
    tmp_intake = tmp_path / "intake_tmp"
    tmp_intake.mkdir()
    monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_intake)

    yield session

    app.dependency_overrides.clear()
    session.close()


@pytest.fixture()
def client(db_session):
    return TestClient(app)


# ---------- _parse_duration_to_minutes 单测 ----------

class TestParseDurationToMinutes:
    def test_hours_minutes(self):
        assert _parse_duration_to_minutes("2h43m") == 163

    def test_minutes_seconds(self):
        # 17m45s = 17 + ceil(45/60) = 17 + 1 = 18
        assert _parse_duration_to_minutes("17m45s") == 18

    def test_pure_hours(self):
        assert _parse_duration_to_minutes("3h") == 180

    def test_pure_minutes(self):
        assert _parse_duration_to_minutes("45m") == 45

    def test_int_passthrough(self):
        assert _parse_duration_to_minutes(120) == 120

    def test_numeric_string(self):
        assert _parse_duration_to_minutes("90") == 90

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_duration_to_minutes("abc")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _parse_duration_to_minutes("")


class TestStripMarkdownJson:
    def test_strip_json_fence(self):
        text = '```json\n{"a":1}\n```'
        assert _strip_markdown_json(text).strip() == '{"a":1}'

    def test_strip_plain_fence(self):
        text = '```\n{"a":1}\n```'
        assert _strip_markdown_json(text).strip() == '{"a":1}'

    def test_no_fence_passthrough(self):
        assert _strip_markdown_json('{"a":1}') == '{"a":1}'


# ---------- 通用 mock httpx Response ----------

class _MockResponse:
    def __init__(self, status_code: int, text: str = "", json_data: Any = None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


def _llm_json_payload(content_str: str) -> dict:
    """构造 DeepSeek-style 响应包装（choices[0].message.content = 字符串）。"""
    return {
        "id": "test-1",
        "choices": [{"message": {"role": "assistant", "content": content_str}}],
    }


# ---------- DeepSeek provider 错误映射 ----------

class TestDeepSeekProviderErrorMapping:
    def _provider(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        return DeepSeekVisionProvider()

    def test_no_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        provider = DeepSeekVisionProvider()
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "no_api_key"

    def test_http_401_maps_to_http_401(self, monkeypatch):
        provider = self._provider(monkeypatch)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(401, text='{"error":"unauthorized"}')

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "http_401"
        assert ei.value.raw_preview is not None

    def test_http_403_maps_to_http_401(self, monkeypatch):
        provider = self._provider(monkeypatch)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(403, text="forbidden")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "http_401"

    def test_http_500_maps_to_http_5xx(self, monkeypatch):
        provider = self._provider(monkeypatch)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(500, text="internal error")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "http_5xx"

    def test_http_503_maps_to_http_5xx(self, monkeypatch):
        provider = self._provider(monkeypatch)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(503, text="service unavailable")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "http_5xx"

    def test_timeout_maps_to_timeout(self, monkeypatch):
        provider = self._provider(monkeypatch)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "timeout"

    def test_non_json_body_maps_to_parse_failed(self, monkeypatch):
        provider = self._provider(monkeypatch)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            # 200 但 body 不是 JSON
            return _MockResponse(200, text="<html>oh no</html>")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "parse_failed"
        assert ei.value.raw_preview is not None
        assert "html" in ei.value.raw_preview.lower()

    def test_json_with_trailing_garbage_uses_raw_decode_fallback(self, monkeypatch):
        """v0.2.3：模型在合法 JSON 后追加解释文字（"Extra data" 错），raw_decode 应救回首个 JSON。

        实测 qwen3-omni-flash 偶发会输出 `{...完整草稿...}\n以上是识别结果。` 这种格式。
        """
        provider = self._provider(monkeypatch)
        valid_json = (
            '{"product_base_name":"床头柜",'
            '"components":[{"name":"侧板","bom_quantity":2}],'
            '"plates":[{"source_index":0,"component_name":"侧板",'
            '"quantity_per_plate":2,"duration_minutes":120}]}'
        )
        trailing_garbage = "\n以上是识别结果，请审阅。\n额外的中文解释。"
        payload = _llm_json_payload(valid_json + trailing_garbage)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(200, json_data=payload)

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        # 不抛错 — raw_decode 救回首个 JSON
        result = provider.recognize([b"asm"], [b"prd"])
        assert result["product_base_name"] == "床头柜"
        assert result["components"][0]["name"] == "侧板"

    def test_missing_components_key_maps_to_schema_invalid(self, monkeypatch):
        provider = self._provider(monkeypatch)
        # content 是合法 JSON 但缺 components 键
        payload = _llm_json_payload('{"product_base_name":"床头柜","plates":[]}')

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(200, json_data=payload)

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "schema_invalid"

    def test_unknown_plate_component_name_maps_to_schema_invalid(self, monkeypatch):
        provider = self._provider(monkeypatch)
        # plate.component_name 不在 components 中
        content = json.dumps({
            "product_base_name": "床头柜",
            "components": [{"name": "侧板", "bom_quantity": 2}],
            "plates": [{
                "source_index": 0,
                "component_name": "抽屉",   # 未声明
                "quantity_per_plate": 2,
                "duration_minutes": 60,
            }],
        }, ensure_ascii=False)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(200, json_data=_llm_json_payload(content))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "schema_invalid"

    def test_image_too_large_in_body_maps_to_image_too_large(self, monkeypatch):
        provider = self._provider(monkeypatch)

        def fake_post(self, url, json=None, headers=None):  # noqa: A002
            return _MockResponse(400, text='{"error":"image_too_large"}')

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        with pytest.raises(LLMProviderError) as ei:
            provider.recognize([b"asm"], [b"prd"])
        assert ei.value.error_kind == "image_too_large"


# ---------- /api/intake/recognize 端点 happy path ----------

def _seed_session_images(tmp_intake_dir: Path, session_id: str, image_ids: list[str]):
    """在 intake_tmp 下 seed 指定 session 与 image_id 的占位图片。"""
    sd = tmp_intake_dir / session_id
    sd.mkdir(parents=True, exist_ok=True)
    for img_id in image_ids:
        (sd / f"{img_id}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")


class TestRecognizeEndpoint:
    def test_happy_path_prefixes_and_default_plate_names(self, client, db_session, monkeypatch):
        # 1. seed session images — session_id / image_id 必须是 uuid4 hex（intake._is_safe_id 校验）
        import uuid as _uuid
        session_id = _uuid.uuid4().hex
        assembly_ids = [_uuid.uuid4().hex for _ in range(2)]
        produce_ids = [_uuid.uuid4().hex for _ in range(3)]
        _seed_session_images(intake_service.INTAKE_TMP_DIR, session_id, assembly_ids + produce_ids)

        # 2. mock provider
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

        def fake_recognize(self, assembly_images, produce_images, product_base_name=None, component_hints=None):
            return {
                "product_base_name": "床头柜",
                "components": [
                    {"name": "侧板", "bom_quantity": 2},
                    {"name": "抽屉", "bom_quantity": 3},
                ],
                "plates": [
                    {"source_index": 0, "component_name": "侧板", "quantity_per_plate": 2, "duration_minutes": 111},
                    {"source_index": 1, "component_name": "抽屉", "quantity_per_plate": 1, "duration_minutes": 80},
                    {"source_index": 2, "component_name": "抽屉", "quantity_per_plate": 2, "duration_minutes": 150},
                ],
            }

        monkeypatch.setattr(DeepSeekVisionProvider, "recognize", fake_recognize)

        # 3. call endpoint
        resp = client.post("/api/intake/recognize", json={
            "session_id": session_id,
            "assembly_image_ids": assembly_ids,
            "produce_image_ids": produce_ids,
            "product_base_name": "床头柜",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # 4. 断言
        assert body["ok"] is True
        assert body["conflicts"] == []
        draft = body["draft"]
        assert draft["product_base_name"] == "床头柜"
        # 组件名带前缀
        comp_names = [c["name"] for c in draft["components"]]
        assert "床头柜-侧板" in comp_names
        assert "床头柜-抽屉" in comp_names
        # 装配件数透传
        ce = next(c for c in draft["components"] if c["name"] == "床头柜-侧板")
        assert ce["assembly_quantity"] == 2
        # 盘号默认 = 组件全名 + -<件数>
        plates = draft["plates"]
        plate0 = plates[0]
        assert plate0["plate_name"] == "床头柜-侧板-2"
        assert plate0["component_name"] == "床头柜-侧板"
        assert plate0["quantity_per_plate"] == 2
        assert plate0["duration_minutes"] == 111
        # source_image_id 反查 — 应等于按 LLM source_index 取 produce_ids 数组
        assert plate0["source_image_id"] == produce_ids[0]
        assert plates[1]["source_image_id"] == produce_ids[1]
        assert plates[2]["source_image_id"] == produce_ids[2]
        assert plates[2]["plate_name"] == "床头柜-抽屉-2"

    def test_session_expired_returns_error(self, client, db_session, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        # 不 seed 图片，直接调
        resp = client.post("/api/intake/recognize", json={
            "session_id": "missing-sess",
            "assembly_image_ids": ["a"],
            "produce_image_ids": ["b"],
            "product_base_name": "床头柜",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["error_kind"] == "session_expired"

    def test_llm_error_propagates_raw_preview(self, client, db_session, monkeypatch):
        import uuid as _uuid
        session_id = _uuid.uuid4().hex
        a_id = _uuid.uuid4().hex
        p_id = _uuid.uuid4().hex
        _seed_session_images(intake_service.INTAKE_TMP_DIR, session_id, [a_id, p_id])

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

        def fake_recognize(self, assembly_images, produce_images, product_base_name=None, component_hints=None):
            raise LLMProviderError("parse_failed", "bad json", raw_preview="<html>")

        monkeypatch.setattr(DeepSeekVisionProvider, "recognize", fake_recognize)

        resp = client.post("/api/intake/recognize", json={
            "session_id": session_id,
            "assembly_image_ids": [a_id],
            "produce_image_ids": [p_id],
        })
        body = resp.json()
        assert body["ok"] is False
        assert body["error_kind"] == "parse_failed"
        assert body["raw_response_preview"] == "<html>"


class TestRecognizeNoApiKey:
    def test_missing_api_key_returns_no_api_key(self, client, db_session, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        # 即使 seed 了图片，no_api_key 应在调 LLM 前触发
        session_id = "sess-nokey"
        _seed_session_images(intake_service.INTAKE_TMP_DIR, session_id, ["a1", "p1"])

        resp = client.post("/api/intake/recognize", json={
            "session_id": session_id,
            "assembly_image_ids": ["a1"],
            "produce_image_ids": ["p1"],
            "product_base_name": "床头柜",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["error_kind"] == "no_api_key"


# ---------- detect_conflicts 服务单测 ----------

class TestDetectConflicts:
    def test_empty_db_returns_empty(self, db_session):
        result = detect_conflicts(db_session, ["床头柜-侧板"], ["床头柜-侧板-2"], [])
        assert result == []

    def test_component_and_plate_conflict(self, db_session):
        # seed 现有 catalog：组件 + 盘（v0.3.0 SKU 必填）
        comp = Component(sku="C-9001", name="床头柜-把手", description="", colors=[])
        db_session.add(comp)
        db_session.flush()
        pc = PrintConfig(
            sku="P-9001",
            plate_name="床头柜-把手-40",
            component_id=comp.id,
            quantity=40,
            duration_minutes=120,
        )
        db_session.add(pc)
        db_session.commit()

        conflicts = detect_conflicts(
            db_session,
            ["床头柜-把手", "床头柜-侧板"],
            ["床头柜-把手-40", "床头柜-侧板-2"],
            [],
        )
        # 应仅返回撞名的
        kinds_names = {(c.kind, c.name) for c in conflicts}
        assert ("component", "床头柜-把手") in kinds_names
        assert ("plate", "床头柜-把手-40") in kinds_names
        # 未撞名不应出现
        assert ("component", "床头柜-侧板") not in kinds_names
        assert ("plate", "床头柜-侧板-2") not in kinds_names
        assert len(conflicts) == 2

    def test_no_input_returns_empty(self, db_session):
        # 即便 DB 有数据，传空列表也返回空
        db_session.add(Component(sku="C-9001", name="X", description="", colors=[]))
        db_session.commit()
        result = detect_conflicts(db_session, [], [], [])
        assert result == []

    def test_existing_name_field_equals_name(self, db_session):
        db_session.add(Component(sku="C-9001", name="床头柜-门板", description="", colors=[]))
        db_session.commit()
        conflicts = detect_conflicts(db_session, ["床头柜-门板"], [], [])
        assert len(conflicts) == 1
        assert conflicts[0].existing_name == "床头柜-门板"
        assert conflicts[0].kind == "component"


# ============================================================
# CUJ-5 merge：颜色矩阵展开 / append / 5 阶段事务 + 回滚 / recent-logs
# ============================================================

import yaml as _yaml  # noqa: E402

from app.schemas_intake import (  # noqa: E402
    ColorCell,
    DraftComponent,
    DraftPlate,
    FinalDraft,
    Variant,
)
from app.services import catalog as catalog_module  # noqa: E402
from app.services.intake import (  # noqa: E402
    _RECENT_LOGS,
    append_to_catalog,
    backup_catalog,
    do_merge,
    expand_to_yaml_structures,
    get_recent_logs,
    intake_log,
)


def _make_final_draft_3v_4c() -> FinalDraft:
    """构造 3 变体 × 4 组件的 FinalDraft（颜色组合各不相同）。"""
    components = [
        DraftComponent(name="床头柜-侧板", assembly_quantity=2),
        DraftComponent(name="床头柜-抽屉", assembly_quantity=3),
        DraftComponent(name="床头柜-把手", assembly_quantity=4),
        DraftComponent(name="床头柜-门板", assembly_quantity=1),
    ]
    plates = [
        DraftPlate(plate_name="床头柜-侧板-2", component_name="床头柜-侧板",
                   quantity_per_plate=2, duration_minutes=120, source_image_id="i1"),
        DraftPlate(plate_name="床头柜-抽屉-3", component_name="床头柜-抽屉",
                   quantity_per_plate=3, duration_minutes=80, source_image_id="i2"),
        DraftPlate(plate_name="床头柜-把手-4", component_name="床头柜-把手",
                   quantity_per_plate=4, duration_minutes=60, source_image_id="i3"),
        DraftPlate(plate_name="床头柜-门板-1", component_name="床头柜-门板",
                   quantity_per_plate=1, duration_minutes=200, source_image_id="i4"),
    ]
    variants = [
        Variant(variant_name="床头柜 - 灰白", color_cells=[
            ColorCell(component_name="床头柜-侧板", color="灰色"),
            ColorCell(component_name="床头柜-抽屉", color="白色"),
            ColorCell(component_name="床头柜-把手", color="银色"),
            ColorCell(component_name="床头柜-门板", color="白色"),
        ]),
        Variant(variant_name="床头柜 - 黑白", color_cells=[
            ColorCell(component_name="床头柜-侧板", color="黑色"),
            ColorCell(component_name="床头柜-抽屉", color="白色"),
            ColorCell(component_name="床头柜-把手", color="银色"),
            ColorCell(component_name="床头柜-门板", color="黑色"),
        ]),
        Variant(variant_name="床头柜 - 黑粉", color_cells=[
            ColorCell(component_name="床头柜-侧板", color="黑色"),
            ColorCell(component_name="床头柜-抽屉", color="粉色"),
            ColorCell(component_name="床头柜-把手", color="金色"),
            ColorCell(component_name="床头柜-门板", color="粉色"),
        ]),
    ]
    return FinalDraft(
        product_base_name="床头柜",
        components=components,
        plates=plates,
        variants=variants,
    )


# ---------- TestColorMatrixExpansion ----------

class TestColorMatrixExpansion:
    def test_three_variants_produce_three_products(self):
        draft = _make_final_draft_3v_4c()
        comps, plates, products, new_skus = expand_to_yaml_structures(draft)
        assert len(products) == 3
        variant_names = [p["名称"] for p in products]
        assert variant_names == ["床头柜 - 灰白", "床头柜 - 黑白", "床头柜 - 黑粉"]
        # 4 组件 + 4 盘 + 3 产品 = 11 个新 SKU
        assert len(new_skus) == 11

    def test_each_product_has_four_bom_rows(self):
        draft = _make_final_draft_3v_4c()
        _, _, products, _ = expand_to_yaml_structures(draft)
        for p in products:
            assert len(p["BOM"]) == 4

    def test_components_keep_union_of_colors_dedupe(self):
        draft = _make_final_draft_3v_4c()
        comps, _, _, _ = expand_to_yaml_structures(draft)
        comp_map = {c["名称"]: c for c in comps}

        # 侧板：灰色（v1）+ 黑色（v2、v3）→ ["灰色", "黑色"]
        assert comp_map["床头柜-侧板"]["可选颜色"] == ["灰色", "黑色"]
        # 抽屉：白色（v1、v2）+ 粉色（v3）→ ["白色", "粉色"]
        assert comp_map["床头柜-抽屉"]["可选颜色"] == ["白色", "粉色"]
        # 把手：银色（v1、v2）+ 金色（v3） → ["银色", "金色"]
        assert comp_map["床头柜-把手"]["可选颜色"] == ["银色", "金色"]
        # 门板：白色（v1）+ 黑色（v2）+ 粉色（v3）→ ["白色", "黑色", "粉色"]
        assert comp_map["床头柜-门板"]["可选颜色"] == ["白色", "黑色", "粉色"]

    def test_plates_have_no_color_field(self):
        draft = _make_final_draft_3v_4c()
        _, plates, _, _ = expand_to_yaml_structures(draft)
        for p in plates:
            assert "颜色" not in p
            # v0.3.0：组件 → 组件编号
            assert set(p.keys()) == {"编号", "盘号", "组件编号", "数量", "耗时分钟"}

    def test_component_without_any_color_omits_field(self):
        """所有变体都给某组件填空字符串 → 该组件不带 可选颜色 字段。"""
        comp = DraftComponent(name="X-通用件", assembly_quantity=1)
        plate = DraftPlate(
            plate_name="X-通用件-1", component_name="X-通用件",
            quantity_per_plate=1, duration_minutes=30, source_image_id="i",
        )
        variant = Variant(variant_name="X - 默认", color_cells=[
            ColorCell(component_name="X-通用件", color=""),
        ])
        draft = FinalDraft(
            product_base_name="X", components=[comp], plates=[plate], variants=[variant],
        )
        comps, _, _, _ = expand_to_yaml_structures(draft)
        # v0.3.0：组件含 编号 + 名称
        assert comps[0]["名称"] == "X-通用件"
        assert "编号" in comps[0]
        assert "可选颜色" not in comps[0]

    def test_bom_quantity_comes_from_assembly_quantity(self):
        draft = _make_final_draft_3v_4c()
        _, _, products, _ = expand_to_yaml_structures(draft)
        # 第一个变体的 BOM 顺序对应 components 顺序
        bom = products[0]["BOM"]
        # 侧板 assembly_quantity=2 → BOM 数量=2
        assert bom[0]["数量"] == 2
        assert bom[1]["数量"] == 3
        assert bom[2]["数量"] == 4
        assert bom[3]["数量"] == 1

    def test_new_skus_assigned_from_starting_zero(self):
        """没有 existing_skus 时从 C-0001 起步。"""
        draft = _make_final_draft_3v_4c()
        comps, plates, products, new_skus = expand_to_yaml_structures(draft)
        assert comps[0]["编号"] == "C-0001"
        assert plates[0]["编号"] == "P-0001"
        assert products[0]["编号"] == "PR-0001"

    def test_new_skus_skip_existing(self):
        """existing_skus 提供已用集合，新 SKU 从最大+1 起步。"""
        draft = _make_final_draft_3v_4c()
        existing = {"组件": {"C-0001", "C-0005"}, "打印盘": {"P-0003"}, "产品": set()}
        comps, plates, products, _ = expand_to_yaml_structures(draft, existing_skus=existing)
        assert comps[0]["编号"] == "C-0006"
        assert plates[0]["编号"] == "P-0004"
        assert products[0]["编号"] == "PR-0001"

    def test_plate_and_bom_reference_new_component_sku(self):
        """plate.组件编号 / product.BOM[].组件编号 引用本次新分配的组件 SKU。"""
        draft = _make_final_draft_3v_4c()
        comps, plates, products, _ = expand_to_yaml_structures(draft)
        # 第一个组件（侧板）= C-0001
        assert comps[0]["编号"] == "C-0001"
        # 第一个盘也是侧板 → 组件编号 = C-0001
        assert plates[0]["组件编号"] == "C-0001"
        # 第一个产品（灰白）的 BOM 第一项（侧板）
        assert products[0]["BOM"][0]["组件编号"] == "C-0001"


# ---------- TestAppendToCatalog ----------

def _seed_minimal_catalog(path):
    """写入一个最小合法 catalog.yaml（含 3 段空列表 + 1 个现有组件，v0.3.0 SKU-keyed）。"""
    initial = {
        "组件": [{"编号": "C-0001", "名称": "旧组件", "可选颜色": ["白色"]}],
        "打印盘": [{"编号": "P-0001", "盘号": "旧盘-1", "组件编号": "C-0001", "数量": 1, "耗时分钟": 30}],
        "产品": [{"编号": "PR-0001", "名称": "旧产品",
                  "BOM": [{"组件编号": "C-0001", "颜色": "白色", "数量": 1}]}],
    }
    path.write_text(
        _yaml.safe_dump(initial, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class TestAppendToCatalog:
    def test_append_preserves_existing_entries(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        _seed_minimal_catalog(catalog)

        new_comps = [{"编号": "C-0002", "名称": "床头柜-侧板", "可选颜色": ["灰色"]}]
        new_plates = [{"编号": "P-0002", "盘号": "床头柜-侧板-2", "组件编号": "C-0002", "数量": 2, "耗时分钟": 120}]
        new_products = [{"编号": "PR-0002", "名称": "床头柜 - 灰白",
                         "BOM": [{"组件编号": "C-0002", "颜色": "灰色", "数量": 2}]}]

        append_to_catalog(catalog, new_comps, new_plates, new_products)

        loaded = _yaml.safe_load(catalog.read_text(encoding="utf-8"))
        # 现有条目保留
        assert any(c["编号"] == "C-0001" for c in loaded["组件"])
        assert any(p["编号"] == "P-0001" for p in loaded["打印盘"])
        assert any(p["编号"] == "PR-0001" for p in loaded["产品"])
        # 新条目追加在末尾
        assert loaded["组件"][-1]["编号"] == "C-0002"
        assert loaded["打印盘"][-1]["编号"] == "P-0002"
        assert loaded["产品"][-1]["编号"] == "PR-0002"

    def test_round_trip_yaml_valid(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        _seed_minimal_catalog(catalog)
        append_to_catalog(catalog, [{"编号": "C-0009", "名称": "新A"}], [], [])
        _yaml.safe_load(catalog.read_text(encoding="utf-8"))

    def test_allow_unicode_preserves_chinese(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        _seed_minimal_catalog(catalog)
        append_to_catalog(catalog, [{"编号": "C-0009", "名称": "床头柜-把手"}], [], [])
        raw = catalog.read_text(encoding="utf-8")
        assert "床头柜-把手" in raw
        assert "\\u" not in raw

    def test_empty_file_creates_three_sections(self, tmp_path):
        catalog = tmp_path / "catalog.yaml"
        catalog.write_text("", encoding="utf-8")
        append_to_catalog(
            catalog,
            [{"编号": "C-0001", "名称": "新A"}],
            [{"编号": "P-0001", "盘号": "新-1", "组件编号": "C-0001", "数量": 1, "耗时分钟": 30}],
            [{"编号": "PR-0001", "名称": "P",
              "BOM": [{"组件编号": "C-0001", "颜色": "", "数量": 1}]}],
        )
        loaded = _yaml.safe_load(catalog.read_text(encoding="utf-8"))
        assert len(loaded["组件"]) == 1
        assert len(loaded["打印盘"]) == 1
        assert len(loaded["产品"]) == 1


# ---------- merge 5 阶段事务 ----------

@pytest.fixture
def catalog_tmp(tmp_path, monkeypatch):
    """临时 catalog.yaml + 重定向 catalog_module.CATALOG_PATH。"""
    catalog = tmp_path / "catalog.yaml"
    _seed_minimal_catalog(catalog)
    monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog)
    return catalog


def _final_draft_minimal() -> FinalDraft:
    """最小有效 final_draft：1 组件 1 盘 1 变体。"""
    return FinalDraft(
        product_base_name="床头柜",
        components=[DraftComponent(name="床头柜-侧板", assembly_quantity=2)],
        plates=[DraftPlate(
            plate_name="床头柜-侧板-2", component_name="床头柜-侧板",
            quantity_per_plate=2, duration_minutes=120, source_image_id="i1",
        )],
        variants=[Variant(variant_name="床头柜 - 灰白", color_cells=[
            ColorCell(component_name="床头柜-侧板", color="灰色"),
        ])],
    )


class TestMergeSuccess:
    def test_success_full_flow(self, db_session, catalog_tmp, tmp_path, monkeypatch):
        # 用真实 in-memory DB + 真实 load_catalog。SessionLocal 在 do_merge 内 new 一个 session，
        # 也必须指向同一 in-memory engine。
        from app.services.intake import do_merge as _do_merge
        from app import database as db_module
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        # 重建共享 engine，给 do_merge 内部 SessionLocal 用
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        monkeypatch.setattr(db_module, "SessionLocal", TestSession)

        # seed session tmp dir — session_id 必须是 uuid4 hex（intake._is_safe_id 校验）
        import uuid as _uuid
        session_id = _uuid.uuid4().hex
        sd = intake_service.INTAKE_TMP_DIR / session_id
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "x.png").write_bytes(b"x")

        draft = _final_draft_minimal()
        outer_session = TestSession()
        try:
            result = _do_merge(
                db=outer_session,
                final_draft=draft,
                catalog_path=catalog_tmp,
                session_id=session_id,
            )
        finally:
            outer_session.close()

        assert result["ok"] is True, result
        assert result["stats"]["components_added"] == 1
        assert result["stats"]["plates_added"] == 1
        assert result["stats"]["products_added"] == 1
        # v0.3.0：new_skus 列出本次分配的所有 SKU（1 组件 + 1 盘 + 1 产品 = 3）
        assert len(result["stats"]["new_skus"]) == 3
        # 已有 catalog 含 C-0001/P-0001/PR-0001 → 新分配应为 C-0002/P-0002/PR-0002
        assert "C-0002" in result["stats"]["new_skus"]
        assert "P-0002" in result["stats"]["new_skus"]
        assert "PR-0002" in result["stats"]["new_skus"]
        # 备份文件存在
        assert Path(result["backup_path"]).is_file()
        # timing keys present and non-negative
        assert "写入" in result["timing_ms"]
        assert "重新加载" in result["timing_ms"]
        assert result["timing_ms"]["写入"] >= 0
        assert result["timing_ms"]["重新加载"] >= 0
        # tmp dir 已清空
        assert not sd.exists()

        # DB 新增 1 Component / 1 PrintConfig / 1 Product（除了已加载的 1 旧组件 / 1 旧盘 / 1 旧产品）
        verify_session = TestSession()
        try:
            from app.models import Product as P
            comp_count = verify_session.query(Component).count()
            plate_count = verify_session.query(PrintConfig).count()
            prod_count = verify_session.query(P).count()
            assert comp_count == 2   # 旧组件 + 新加
            assert plate_count == 2  # 旧盘 + 新加
            assert prod_count == 2   # 旧产品 + 新加
        finally:
            verify_session.close()


class TestMergeConflict:
    def test_conflict_short_circuits_no_backup(self, db_session, catalog_tmp):
        # 预置同名组件（撞名走 name 字段；SKU 是稳定标识，必填）
        db_session.add(Component(sku="C-9001", name="床头柜-侧板", description="", colors=[]))
        db_session.commit()

        draft = _final_draft_minimal()
        original_bytes = catalog_tmp.read_bytes()

        result = do_merge(
            db=db_session,
            final_draft=draft,
            catalog_path=catalog_tmp,
            session_id="sess-conflict",
        )
        assert result["ok"] is False
        assert result["error_kind"] == "conflict"
        assert result["rolled_back"] is False
        # 文件未被触碰
        assert catalog_tmp.read_bytes() == original_bytes
        # 无 bak 文件产生
        baks = list(catalog_tmp.parent.glob("*.bak.*"))
        assert baks == []
        # details 含冲突信息
        assert any(d["name"] == "床头柜-侧板" for d in result["details"])


class TestMergeWriteFailed:
    def test_write_failed_rolls_back(self, db_session, catalog_tmp, monkeypatch):
        from app.services import intake as svc

        def fake_append(*args, **kwargs):
            # 模拟磁盘满；注意：被调用前 catalog 已被 monkeypatch 模块属性，
            # 实际写到 catalog 的 yaml.safe_dump 没机会执行
            raise OSError("disk full")

        monkeypatch.setattr(svc, "append_to_catalog", fake_append)

        original_bytes = catalog_tmp.read_bytes()
        draft = _final_draft_minimal()

        result = do_merge(
            db=db_session,
            final_draft=draft,
            catalog_path=catalog_tmp,
            session_id="sess-write-fail",
        )
        assert result["ok"] is False
        assert result["error_kind"] == "write_failed"
        assert result["rolled_back"] is True
        # bak 文件存在
        assert Path(result["backup_path"]).is_file()
        # 文件字节级 == 原始（因为 append 在写之前就抛了 → rollback 还原 == 原始）
        assert catalog_tmp.read_bytes() == original_bytes


class TestMergeYamlInvalid:
    def test_yaml_invalid_rolls_back(self, db_session, catalog_tmp, monkeypatch):
        """append 写完后复读校验失败 → rollback。
        模拟方式：让 append_to_catalog 直接写入非法 YAML 字节。
        """
        from app.services import intake as svc

        def fake_append(catalog_path, *args, **kwargs):
            # 写入非法 YAML（无法 safe_load 的字节序列）
            catalog_path.write_bytes(b"\x00invalid: : : :\n  - [")

        monkeypatch.setattr(svc, "append_to_catalog", fake_append)

        original_bytes = catalog_tmp.read_bytes()
        draft = _final_draft_minimal()

        result = do_merge(
            db=db_session,
            final_draft=draft,
            catalog_path=catalog_tmp,
            session_id="sess-yaml-bad",
        )
        assert result["ok"] is False
        assert result["error_kind"] == "yaml_invalid"
        assert result["rolled_back"] is True
        assert Path(result["backup_path"]).is_file()
        # 回滚后内容 == 原始
        assert catalog_tmp.read_bytes() == original_bytes


class TestMergeRollback:
    def test_load_catalog_failure_rolls_back(self, db_session, catalog_tmp, monkeypatch):
        """模拟 load_catalog 抛错 → rollback；catalog.yaml 内容 = 备份。"""
        from app.services import catalog as cat_mod

        def fake_load_catalog(session):
            raise ValueError("simulated reload failure")

        monkeypatch.setattr(cat_mod, "load_catalog", fake_load_catalog)

        original_bytes = catalog_tmp.read_bytes()
        draft = _final_draft_minimal()

        result = do_merge(
            db=db_session,
            final_draft=draft,
            catalog_path=catalog_tmp,
            session_id="sess-load-fail",
        )
        assert result["ok"] is False
        assert result["error_kind"] == "load_failed"
        assert result["rolled_back"] is True
        # bak 文件存在
        bak = Path(result["backup_path"])
        assert bak.is_file()
        # catalog.yaml 字节级 == 备份字节级 == 原始字节级
        assert catalog_tmp.read_bytes() == original_bytes
        assert bak.read_bytes() == original_bytes


# ---------- TestRecentLogs ----------

class TestRecentLogs:
    def test_intake_log_appears_in_recent_logs(self, client):
        _RECENT_LOGS.clear()
        intake_log("hello")
        intake_log("world")
        resp = client.get("/api/intake/recent-logs?lines=2")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["logs"] == "hello\nworld"

    def test_recent_logs_truncates_to_lines_param(self, client):
        _RECENT_LOGS.clear()
        for i in range(10):
            intake_log(f"log {i}")
        resp = client.get("/api/intake/recent-logs?lines=3")
        body = resp.json()
        assert body["logs"] == "log 7\nlog 8\nlog 9"

    def test_recent_logs_default_100(self, client):
        _RECENT_LOGS.clear()
        for i in range(5):
            intake_log(f"line {i}")
        resp = client.get("/api/intake/recent-logs")
        body = resp.json()
        assert body["logs"].split("\n") == [f"line {i}" for i in range(5)]


# ============================================================
# T11 — 端到端集成冒烟测试：upload → recognize → (color) → merge
# 把 T3 / T5 / T9 三个端点串成完整 intake 流程，验证整条链路
# 在 in-memory SQLite + mocked LLM 下打通。
# ============================================================

class TestEndToEndIntakeFlow:
    def test_end_to_end_intake_flow(self, tmp_path, monkeypatch):
        from app import database as db_module
        from app.models import Product, ProductComponent

        # --- 验证步骤 0：搭建隔离环境 ---
        # 隔离的内存 SQLite：do_merge 内部 SessionLocal() 也得指向同一 engine
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        monkeypatch.setattr(db_module, "SessionLocal", TestSession)

        outer_session = TestSession()

        def _override_get_db():
            try:
                yield outer_session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_get_db

        # 重定向 catalog.yaml + intake_tmp 到 tmp_path
        catalog_path = tmp_path / "catalog.yaml"
        catalog_path.write_text(
            "组件: []\n打印盘: []\n产品: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog_path)

        tmp_intake = tmp_path / "intake_tmp"
        tmp_intake.mkdir()
        monkeypatch.setattr(intake_service, "INTAKE_TMP_DIR", tmp_intake)

        # 让 provider 视为已配置
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-key")

        # mock LLM：固定返回（不依赖网络）
        def fake_recognize(self, assembly_images, produce_images, product_base_name=None, component_hints=None):
            return {
                "product_base_name": "床头柜",
                "components": [
                    {"name": "侧板", "bom_quantity": 2},
                    {"name": "把手", "bom_quantity": 4},
                ],
                "plates": [
                    {"source_index": 0, "component_name": "侧板",
                     "quantity_per_plate": 2, "duration_minutes": 111},
                    {"source_index": 1, "component_name": "把手",
                     "quantity_per_plate": 40, "duration_minutes": 18},
                ],
            }

        monkeypatch.setattr(DeepSeekVisionProvider, "recognize", fake_recognize)

        try:
            client = TestClient(app)

            # --- 验证步骤 1：upload — 1 张 assembly 全白 + 2 张 produce 右上深色 ---
            assembly_png = _make_white_image()
            produce_png_a = _make_image_with_dark_right_panel()
            produce_png_b = _make_image_with_dark_right_panel()

            upload_resp = client.post(
                "/api/intake/upload",
                files=[
                    ("files", ("asm.png", assembly_png, "image/png")),
                    ("files", ("prd_a.png", produce_png_a, "image/png")),
                    ("files", ("prd_b.png", produce_png_b, "image/png")),
                ],
            )
            assert upload_resp.status_code == 200, upload_resp.text
            upload_body = upload_resp.json()
            assert upload_body["ok"] is True
            session_id = upload_body["session_id"]
            assert session_id
            assert len(upload_body["images"]) == 3

            assembly_ids = [
                img["image_id"] for img in upload_body["images"]
                if img["suggested_class"] == "assembly"
            ]
            produce_ids = [
                img["image_id"] for img in upload_body["images"]
                if img["suggested_class"] == "produce"
            ]
            assert len(assembly_ids) == 1, f"expected 1 assembly, got {len(assembly_ids)}: {upload_body['images']}"
            assert len(produce_ids) == 2, f"expected 2 produce, got {len(produce_ids)}: {upload_body['images']}"

            # 落盘文件确实存在
            session_dir = tmp_intake / session_id
            assert session_dir.is_dir()
            assert len(list(session_dir.iterdir())) == 3

            # --- 验证步骤 2：recognize — 拿 draft + 撞名空 ---
            recognize_resp = client.post(
                "/api/intake/recognize",
                json={
                    "session_id": session_id,
                    "assembly_image_ids": assembly_ids,
                    "produce_image_ids": produce_ids,
                    "product_base_name": "床头柜",
                },
            )
            assert recognize_resp.status_code == 200, recognize_resp.text
            recognize_body = recognize_resp.json()
            assert recognize_body["ok"] is True, recognize_body
            assert recognize_body["conflicts"] == []

            draft = recognize_body["draft"]
            assert draft["product_base_name"] == "床头柜"
            # 组件名带产品基名前缀
            comp_names = [c["name"] for c in draft["components"]]
            assert comp_names == ["床头柜-侧板", "床头柜-把手"]
            # 盘号默认 = 组件全名 + -<件数>
            plate_names = [p["plate_name"] for p in draft["plates"]]
            assert plate_names == ["床头柜-侧板-2", "床头柜-把手-40"]
            # source_image_id 反查到 produce_ids
            assert draft["plates"][0]["source_image_id"] == produce_ids[0]
            assert draft["plates"][1]["source_image_id"] == produce_ids[1]

            # --- 验证步骤 3：构造 FinalDraft（模拟 color 步骤生成 2 个变体）---
            final_draft = {
                "product_base_name": "床头柜",
                "components": [
                    {"name": "床头柜-侧板", "assembly_quantity": 2},
                    {"name": "床头柜-把手", "assembly_quantity": 4},
                ],
                "plates": [
                    {
                        "plate_name": "床头柜-侧板-2",
                        "component_name": "床头柜-侧板",
                        "quantity_per_plate": 2,
                        "duration_minutes": 111,
                        "source_image_id": produce_ids[0],
                    },
                    {
                        "plate_name": "床头柜-把手-40",
                        "component_name": "床头柜-把手",
                        "quantity_per_plate": 40,
                        "duration_minutes": 18,
                        "source_image_id": produce_ids[1],
                    },
                ],
                "variants": [
                    {
                        "variant_name": "床头柜 - 灰白",
                        "color_cells": [
                            {"component_name": "床头柜-侧板", "color": "灰色"},
                            {"component_name": "床头柜-把手", "color": "白色"},
                        ],
                    },
                    {
                        "variant_name": "床头柜 - 白黑",
                        "color_cells": [
                            {"component_name": "床头柜-侧板", "color": "白色"},
                            {"component_name": "床头柜-把手", "color": "黑色"},
                        ],
                    },
                ],
            }

            # --- 验证步骤 4：merge — 5 阶段事务跑通 ---
            merge_resp = client.post(
                "/api/intake/merge",
                json={"session_id": session_id, "final_draft": final_draft},
            )
            assert merge_resp.status_code == 200, merge_resp.text
            merge_body = merge_resp.json()
            assert merge_body["ok"] is True, merge_body
            stats = merge_body["stats"]
            assert stats["components_added"] == 2
            assert stats["plates_added"] == 2
            assert stats["products_added"] == 2
            # v0.3.0：返回 new_skus（2 组件 + 2 盘 + 2 产品 = 6）
            assert len(stats["new_skus"]) == 6
            # 空 catalog → 从 0001 起步
            assert "C-0001" in stats["new_skus"]
            assert "P-0001" in stats["new_skus"]
            assert "PR-0001" in stats["new_skus"]

            # 备份文件 + 计时键
            assert Path(merge_body["backup_path"]).is_file()
            assert "写入" in merge_body["timing_ms"]
            assert "重新加载" in merge_body["timing_ms"]
            assert merge_body["timing_ms"]["写入"] >= 0
            assert merge_body["timing_ms"]["重新加载"] >= 0

            # --- 验证步骤 5：最终 DB / 文件系统状态 ---
            # DB 端：新表记录通过 do_merge 内部独立 session 写入，
            # 这里用一个全新 session 读，避免 outer_session 缓存
            verify_session = TestSession()
            try:
                components = verify_session.query(Component).order_by(Component.name).all()
                comp_by_name = {c.name: c for c in components}
                assert set(comp_by_name) == {"床头柜-侧板", "床头柜-把手"}
                # 颜色 union 正确
                assert sorted(comp_by_name["床头柜-侧板"].colors) == sorted(["灰色", "白色"])
                assert sorted(comp_by_name["床头柜-把手"].colors) == sorted(["白色", "黑色"])

                plates = verify_session.query(PrintConfig).order_by(PrintConfig.plate_name).all()
                plate_by_name = {p.plate_name: p for p in plates}
                assert set(plate_by_name) == {"床头柜-侧板-2", "床头柜-把手-40"}
                assert plate_by_name["床头柜-侧板-2"].quantity == 2
                assert plate_by_name["床头柜-侧板-2"].duration_minutes == 111
                assert plate_by_name["床头柜-把手-40"].quantity == 40
                assert plate_by_name["床头柜-把手-40"].duration_minutes == 18

                products = verify_session.query(Product).order_by(Product.name).all()
                product_names = {p.name for p in products}
                assert product_names == {"床头柜 - 灰白", "床头柜 - 白黑"}

                pcs = verify_session.query(ProductComponent).all()
                # 2 产品 × 2 组件 = 4 BOM 行
                assert len(pcs) == 4
                # 颜色分布检查
                product_id_to_name = {p.id: p.name for p in products}
                color_set_per_product = {name: set() for name in product_names}
                for pc in pcs:
                    color_set_per_product[product_id_to_name[pc.product_id]].add(pc.color)
                assert color_set_per_product["床头柜 - 灰白"] == {"灰色", "白色"}
                assert color_set_per_product["床头柜 - 白黑"] == {"白色", "黑色"}
            finally:
                verify_session.close()

            # session tmp 已清理
            assert not session_dir.exists()

            # catalog.yaml.bak.<timestamp> 备份存在
            baks = list(tmp_path.glob("catalog.yaml.bak.*"))
            assert len(baks) == 1

            # catalog.yaml 含 3 段 + 各 2 条
            loaded_yaml = _yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
            assert set(loaded_yaml.keys()) >= {"组件", "打印盘", "产品"}
            assert len(loaded_yaml["组件"]) == 2
            assert len(loaded_yaml["打印盘"]) == 2
            assert len(loaded_yaml["产品"]) == 2
            # 中文键 + 内容
            assert loaded_yaml["组件"][0]["名称"] == "床头柜-侧板"
            assert loaded_yaml["打印盘"][0]["盘号"] == "床头柜-侧板-2"
            assert loaded_yaml["产品"][0]["名称"] == "床头柜 - 灰白"
        finally:
            app.dependency_overrides.clear()
            outer_session.close()
