"""产品录入（intake）后端测试 — T3

覆盖：
- 启发式分类器（heuristic_classify）：真实样例 + 合成边界
- POST /api/intake/upload：multipart 上传 + 分类 + 落盘
- GET /api/intake/provider-status：DEEPSEEK_API_KEY 在场/不在场两条路径
- cleanup_stale_sessions：TTL 过期清理
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import intake as intake_service
from app.services.intake import (
    INTAKE_TMP_DIR,
    TTL_SECONDS,
    cleanup_stale_sessions,
    heuristic_classify,
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
