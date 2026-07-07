"""Tests for the polished vision helpers and the export-engine honesty
and default-formats fixes."""

import base64

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import encode_image, image_content, vision_message

PIL = pytest.importorskip("PIL", reason="Pillow needed for image tests")


def _png(tmp_path, w, h):
    from PIL import Image

    path = tmp_path / f"img_{w}x{h}.png"
    Image.new("RGB", (w, h), (123, 200, 80)).save(path)
    return path


# ---- image encoding / downscaling ---------------------------------------

def test_encode_image_downscales_large(tmp_path):
    big = _png(tmp_path, 4000, 3000)
    mime, b64 = encode_image(big, max_dim=2200)
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(img.size) <= 2200          # longest side capped
    assert mime in ("image/png", "image/jpeg")


def test_encode_image_keeps_small(tmp_path):
    small = _png(tmp_path, 300, 200)
    mime, b64 = encode_image(small, max_dim=2200)
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.size == (300, 200)         # untouched


def test_encode_image_falls_back_to_jpeg_on_size(tmp_path):
    from PIL import Image
    import io, random

    # noisy image doesn't compress well as PNG -> forces JPEG under a tiny cap
    path = tmp_path / "noise.png"
    img = Image.new("RGB", (1500, 1500))
    img.putdata([(random.randint(0, 255),) * 3 for _ in range(1500 * 1500)])
    img.save(path)
    png_size = path.stat().st_size
    mime, b64 = encode_image(path, max_dim=1500, max_bytes=200_000)
    # over-cap incompressible noise: engine switches to JPEG and shrinks it
    # as far as the quality floor allows (may still exceed a tiny cap)
    assert mime == "image/jpeg"
    assert len(base64.b64decode(b64)) < png_size


def test_vision_message_shape(tmp_path):
    img = _png(tmp_path, 100, 100)
    msg = vision_message("Describe.", img)
    assert msg["role"] == "user"
    parts = msg["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/")


def test_image_content_data_url(tmp_path):
    img = _png(tmp_path, 50, 50)
    part = image_content(img)
    assert part["image_url"]["url"].startswith("data:image/")


# ---- honest export engines ----------------------------------------------

def test_chromium_reported_only_when_present(monkeypatch):
    from pdftransl.export import exporter

    monkeypatch.setattr(exporter, "chromium_executable", lambda: None)
    exporter._playwright_chromium_path.cache_clear()
    assert "chromium" not in exporter.available_engines()["pdf"]

    monkeypatch.setattr(exporter, "chromium_executable", lambda: "/fake/chrome")
    # need playwright importable for the branch; skip if absent
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed")
    assert "chromium" in exporter.available_engines()["pdf"]


def test_pdf_reports_reason_when_no_engine(tmp_path, monkeypatch):
    from pdftransl.export import exporter

    # no engines at all -> pdf file None with a reason
    monkeypatch.setattr(exporter, "_pandoc_path", lambda: None)
    monkeypatch.setattr(exporter, "_chromium_pdf", lambda a, b: False)
    monkeypatch.setattr(exporter, "_weasyprint_pdf", lambda a, b, base_url: False)
    result = exporter.export_document("# T\n\ntext.\n", tmp_path / "d", formats=["pdf"])
    assert result["files"]["pdf"] is None
    assert "unavailable" in result["engines"]["pdf"]


# ---- default formats -----------------------------------------------------

def test_default_export_formats():
    cfg = PipelineConfig()
    assert cfg.export_formats == ["html", "docx", "pdf"]
