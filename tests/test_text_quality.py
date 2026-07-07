"""Tests for garbled-text detection (the "кракозябры" bug) and its
routing to OCR."""

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.parsing.text_quality import (
    is_garbled,
    language_mismatch,
    text_quality,
)

fitz = pytest.importorskip("fitz", reason="PyMuPDF needed")


# ---- detector ------------------------------------------------------------

REAL_RU = (
    "Программная модель коммуникационной среды нейросупертранспьютера. "
    "В работе рассматривается архитектура вычислительной системы и алгоритмы "
    "маршрутизации сообщений между вычислительными узлами. " * 3
)
REAL_EN = (
    "The software model of the communication environment. This paper considers "
    "the architecture of a computing system and message routing algorithms. " * 3
)
MATH_HEAVY = (
    "Энергия $E = mc^2$ и интеграл $\\int_0^1 f(x)\\,dx$ рассматриваются. "
    "Уравнение движения $\\frac{d^2x}{dt^2} = -\\omega^2 x$ решается. " * 3
)
NUMERIC_TABLE = "| 0.92 | 0.88 | 0.95 | 0.71 |\n" * 30


def test_real_text_not_flagged():
    assert not is_garbled(REAL_RU)
    assert not is_garbled(REAL_EN)
    assert not is_garbled(MATH_HEAVY)
    assert not is_garbled(NUMERIC_TABLE)


def test_pua_garbage_flagged():
    pua = "".join(chr(0xE000 + (i % 300)) for i in range(500))
    q = text_quality(pua)
    assert q["is_garbled"]
    assert q["garbled_ratio"] > 0.5


def test_replacement_chars_flagged():
    assert is_garbled("�" * 400 + " some")


def test_latin1_mojibake_flagged():
    # Cyrillic UTF-8 bytes decoded as latin-1
    moji = "ÐÐ¾Ð´ÐµÐ»ÑÐ½Ð°Ñ Ð¼Ð¾Ð´ÐµÐ»Ñ ÐºÐ¾Ð¼Ð¼ÑÐ½Ð¸ÐºÐ°ÑÐ¸Ð¾Ð½Ð½Ð¾Ð¹ ÑÑÐµÐ´Ñ " * 6
    assert is_garbled(moji)


def test_filler_glyphs_flagged():
    # a wall of the same punctuation glyph — no letters, no digits
    assert is_garbled("·" * 400)


def test_short_text_not_flagged():
    # too little text to judge — don't route a tiny doc to OCR by mistake
    assert not is_garbled("·····")


# ---- language-direction sanity ------------------------------------------

def test_language_mismatch_detects_wrong_direction():
    # Russian document declared as English source -> flagged
    assert language_mismatch(REAL_RU, "en") == "cyrillic"
    assert language_mismatch(REAL_EN, "ru") == "latin"


def test_language_mismatch_correct_direction():
    assert language_mismatch(REAL_RU, "ru") is None
    assert language_mismatch(REAL_EN, "en") is None


def test_language_mismatch_tolerates_terms():
    # Russian text peppered with English terms shouldn't false-positive
    mixed = ("Модель использует протокол TCP и алгоритм Router для маршрутизации "
             "сообщений между вычислительными узлами системы. " * 3)
    assert language_mismatch(mixed, "ru") is None


def _cyrillic_font():
    """A TTF that can render Cyrillic (fitz's built-in Helvetica can't)."""
    try:
        import os

        import matplotlib
        path = os.path.join(
            os.path.dirname(matplotlib.__file__),
            "mpl-data", "fonts", "ttf", "DejaVuSans.ttf",
        )
        return path if os.path.exists(path) else None
    except ImportError:
        return None


def test_pipeline_flags_wrong_direction(tmp_path):
    from pdftransl.pipeline import TranslationPipeline

    font = _cyrillic_font()
    if font is None:
        pytest.skip("no Cyrillic-capable font available")

    # a proper Russian PDF, but the job says source is English
    pdf = tmp_path / "ru.pdf"
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in (REAL_RU[i:i + 70] for i in range(0, 900, 70)):
        page.insert_text((72, y), line, fontsize=11,
                         fontname="F0", fontfile=font)
        y += 16
    doc.save(str(pdf))
    doc.close()

    cfg = PipelineConfig(
        parser_backend="pymupdf", source_lang="en", target_lang="ru",
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], ocr_on_scan=False,
    )
    result = TranslationPipeline(cfg, client=FakeLLMClient(transform=lambda t: t)).run(
        pdf, job_id="dir1"
    )
    assert "language_warning" in result.report


# ---- pipeline routing ----------------------------------------------------

class VisionFake(FakeLLMClient):
    supports_vision = True

    def chat(self, messages, temperature=0.2, max_tokens=None, response_format=None):
        self.calls.append(messages)
        content = messages[-1]["content"]
        if isinstance(content, list):  # OCR
            return "# Программная модель\n\nРаспознанный связный текст статьи."
        return content if isinstance(content, str) else str(content)


def _garbled_pdf(path):
    """PDF whose extracted text is filler glyphs (fitz substitutes PUA)."""
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for _ in range(30):
        page.insert_text((72, y), chr(0xE000) * 60, fontsize=10)
        y += 14
    doc.save(str(path))
    doc.close()


def test_garbled_pdf_warns_without_vision(tmp_path):
    from pdftransl.pipeline import TranslationPipeline

    pdf = tmp_path / "garbled.pdf"
    _garbled_pdf(pdf)
    text_only = FakeLLMClient(transform=lambda t: t)
    cfg = PipelineConfig(
        parser_backend="pymupdf", db_path=str(tmp_path / "db.sqlite"),
        output_dir=str(tmp_path / "out"), review=False, doc_summary=False,
        auto_glossary=False, learn=False, export_formats=[],
    )
    result = TranslationPipeline(cfg, client=text_only).run(pdf, job_id="g1")
    assert result.report["scan"]["is_garbled"] is True
    assert "scan_warning" in result.report
    assert result.status == "partial"      # not a false "completed"


def test_garbled_pdf_routes_to_ocr(tmp_path):
    from pdftransl.pipeline import TranslationPipeline

    pdf = tmp_path / "garbled.pdf"
    _garbled_pdf(pdf)
    cfg = PipelineConfig(
        parser_backend="pymupdf", db_path=str(tmp_path / "db.sqlite"),
        output_dir=str(tmp_path / "out"), review=False, doc_summary=False,
        auto_glossary=False, learn=False, export_formats=[], ocr_dpi=120,
    )
    result = TranslationPipeline(cfg, client=VisionFake()).run(pdf, job_id="g2")
    assert result.report["parser_backend"] == "vlm_ocr"
    assert result.report.get("ocr")
    assert result.status == "completed"
