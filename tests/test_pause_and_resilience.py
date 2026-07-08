"""Tests for: (1) cooperative job pause/resume, (2) the empty-document
bug (a stalled/empty LLM reply used to survive into the output instead
of falling back to source text), and (3) discrete translation — the
document is written to disk right after translation, so a later stage
(review/scoring/backtranslation/export) stalling or raising can't erase
already-finished work.
"""

import re

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.models import Segment
from pdftransl.pipeline import TranslationPipeline
from pdftransl.translation.translator import Translator, build_segments
from pdftransl.parsing.splitter import split_markdown
from pdftransl.masking import Masker


def _fake_ru(masked: str) -> str:
    words = {"Paragraph": "Абзац", "number": "номер", "about": "о",
             "neural": "нейронных", "networks": "сетях", "and": "и",
             "data": "данных", "systems": "систем"}
    return re.sub(r"[A-Za-z]+", lambda m: words.get(m.group(0), m.group(0)), masked)


def _cfg(tmp_path, **kw):
    return PipelineConfig(
        db_path=str(tmp_path / "db.sqlite"), output_dir=str(tmp_path / "out"),
        review=False, doc_summary=False, auto_glossary=False, learn=False,
        export_formats=[], embedder="hashing", chunk_char_budget=40, **kw,
    )


DOC = "\n\n".join(
    f"Paragraph number {i} about neural networks and data systems." for i in range(8)
)


# ---- Segment.final_text() empty-translation fallback ----------------------

def test_final_text_falls_back_on_empty_translation():
    seg = Segment(id="s1", kind="translate", source_text="Hello world")
    seg.translation = ""  # a stalled/overloaded LLM "answered" with nothing
    assert seg.final_text() == "Hello world"


def test_final_text_uses_real_translation():
    seg = Segment(id="s1", kind="translate", source_text="Hello world")
    seg.translation = "Привет мир"
    assert seg.final_text() == "Привет мир"


def test_final_text_pass_through_kind():
    seg = Segment(id="s1", kind="pass", source_text="$$E=mc^2$$")
    assert seg.final_text() == "$$E=mc^2$$"


# ---- empty LLM replies degrade to source instead of a blank document ------

def test_empty_llm_reply_keeps_source_not_blank(tmp_path):
    def blank_then_ok(masked: str) -> str:
        # simulate a stalled/overloaded model: first attempt returns
        # nothing, repair attempts also come back empty (still "hung")
        return ""

    client = FakeLLMClient(transform=blank_then_ok)
    cfg = _cfg(tmp_path, max_repair_attempts=1)
    result = TranslationPipeline(cfg, client=client).translate_markdown(
        DOC, tmp_path / "out" / "doc.md"
    )
    # every segment "failed" validation (empty_translation), but the
    # assembled document must still contain the ORIGINAL text, not blanks
    assert result.status == "partial"
    text = (tmp_path / "out" / "doc.md").read_text(encoding="utf-8")
    assert "Paragraph number 0" in text
    assert text.strip() != ""


# ---- pause: sequential mode -------------------------------------------

def test_sequential_pause_stops_early_and_is_resumable(tmp_path):
    calls = {"n": 0}

    def counting_pause():
        calls["n"] += 1
        return calls["n"] > 2  # let 2 checks pass, then request a stop

    client = FakeLLMClient(transform=_fake_ru)
    cfg = _cfg(tmp_path, max_workers=1, resume=True)
    pipe = TranslationPipeline(cfg, client=client)
    result = pipe.translate_markdown(
        DOC, tmp_path / "out" / "doc.md", should_pause=counting_pause,
    )
    assert result.status == "paused"
    assert result.report["paused"] is True
    assert 0 < result.report["segments_done"] < result.report["segments_translated"]
    assert result.output_markdown_path
    # partial output still holds every paragraph (translated or source-fallback)
    text = open(result.output_markdown_path, encoding="utf-8").read()
    for i in range(8):
        assert f"number {i}" in text or "номер" in text

    # resuming re-enters the pipeline; the checkpoint skips finished
    # segments so the second run only needs to translate the rest
    client2 = FakeLLMClient(transform=_fake_ru)
    pipe2 = TranslationPipeline(cfg, client=client2)
    result2 = pipe2.translate_markdown(DOC, tmp_path / "out" / "doc.md")
    assert result2.status == "completed"
    assert len(client2.calls) < 8


# ---- pause: parallel mode -----------------------------------------------

def test_parallel_pause_marks_pending_segments(tmp_path):
    def always_pause():
        return True  # pause immediately after the very first completion

    client = FakeLLMClient(transform=_fake_ru)
    cfg = _cfg(tmp_path, max_workers=4, resume=True)
    pipe = TranslationPipeline(cfg, client=client)
    result = pipe.translate_markdown(
        DOC, tmp_path / "out" / "doc.md", should_pause=always_pause,
    )
    assert result.status == "paused"
    # not every segment ran (workers are bounded, so most were cancelled)
    assert result.report["segments_pending"] > 0
    pending_segments = [
        s for s in result.segments
        if s["kind"] == "translate" and s["translation"] is None
    ]
    assert pending_segments
    codes = {i["code"] for s in pending_segments for i in s["issues"]}
    assert "paused" in codes


def test_translator_pause_returns_tuple(tmp_path):
    blocks = split_markdown(DOC)
    segments = build_segments(blocks, Masker(), char_budget=40)
    client = FakeLLMClient(transform=_fake_ru)
    translator = Translator(client, _cfg(tmp_path, max_workers=1))

    seen = []

    def pause_after_two():
        seen.append(1)
        return len(seen) > 2

    out, paused = translator.translate_segments(segments, should_pause=pause_after_two)
    assert paused is True
    assert out is segments  # mutated in place, same list returned


# ---- discrete write: a downstream stage failing can't erase the doc ------

def test_downstream_stage_exception_does_not_erase_translation(tmp_path, monkeypatch):
    import pdftransl.pipeline as pipeline_mod

    def boom(*a, **kw):
        raise RuntimeError("simulated backtranslation stall/timeout")

    monkeypatch.setattr(pipeline_mod, "backtranslation_check", boom)

    client = FakeLLMClient(transform=_fake_ru)
    cfg = _cfg(tmp_path, backtranslation_check=True)
    result = TranslationPipeline(cfg, client=client).translate_markdown(
        DOC, tmp_path / "out" / "doc.md"
    )
    # the stage failure is recorded but doesn't turn the run into a
    # bare failure with no output
    assert result.status in ("completed", "partial")
    assert result.report["stage_errors"]["backtranslation"]
    text = open(result.output_markdown_path, encoding="utf-8").read()
    assert "Абзац" in text  # real translation made it to disk
