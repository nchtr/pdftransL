"""Parser fallback chain and MinerU timeout handling."""

import subprocess

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.models import ParsedDocument
from pdftransl.parsing.base import ParserBackend

fitz = pytest.importorskip("fitz", reason="PyMuPDF needed")


class _FailingBackend(ParserBackend):
    name = "failing"

    def available(self):
        return True

    def parse(self, pdf_path, workdir):
        raise ParserError("simulated backend failure (e.g. MinerU timeout)")


def _text_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A short English paragraph to translate here.", fontsize=12)
    doc.save(str(path))
    doc.close()


def test_pipeline_falls_back_when_primary_parser_fails(tmp_path, monkeypatch):
    from pdftransl import pipeline as pipeline_mod

    pdf = tmp_path / "doc.pdf"
    _text_pdf(pdf)

    # primary backend always fails; pipeline should fall back to a real one
    monkeypatch.setattr(pipeline_mod, "get_backend", lambda cfg: _FailingBackend())

    cfg = PipelineConfig(
        parser_backend="failing", db_path=str(tmp_path / "db.sqlite"),
        output_dir=str(tmp_path / "out"), review=False, doc_summary=False,
        auto_glossary=False, learn=False, export_formats=[], ocr_on_scan=False,
        parser_fallback=True,
    )
    result = pipeline_mod.TranslationPipeline(
        cfg, client=FakeLLMClient(transform=lambda t: t)
    ).run(pdf, job_id="fb1")

    assert result.status in ("completed", "partial")
    assert result.report["parser_backend"] == "pymupdf"   # recovered
    assert "parser_fallback" in result.report


def test_fallback_disabled_propagates_error(tmp_path, monkeypatch):
    from pdftransl import pipeline as pipeline_mod

    pdf = tmp_path / "doc.pdf"
    _text_pdf(pdf)
    monkeypatch.setattr(pipeline_mod, "get_backend", lambda cfg: _FailingBackend())

    cfg = PipelineConfig(
        parser_backend="failing", db_path=str(tmp_path / "db.sqlite"),
        output_dir=str(tmp_path / "out"), review=False, doc_summary=False,
        auto_glossary=False, learn=False, export_formats=[], ocr_on_scan=False,
        parser_fallback=False,
    )
    result = pipeline_mod.TranslationPipeline(
        cfg, client=FakeLLMClient(transform=lambda t: t)
    ).run(pdf, job_id="fb2")
    # no fallback allowed -> job fails cleanly (not a raw crash)
    assert result.status == "failed"
    assert result.error


def test_mineru_timeout_raises_parser_error(tmp_path, monkeypatch):
    from pdftransl.parsing.mineru_local import MineruLocalBackend

    pdf = tmp_path / "big.pdf"
    _text_pdf(pdf)

    monkeypatch.setattr(
        "pdftransl.parsing.mineru_local.shutil.which", lambda _: "/usr/bin/mineru"
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="mineru", timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr("pdftransl.parsing.mineru_local.subprocess.run", fake_run)

    backend = MineruLocalBackend(PipelineConfig(parser_timeout=5))
    with pytest.raises(ParserError, match="timed out"):
        backend.parse(pdf, tmp_path / "wd")
