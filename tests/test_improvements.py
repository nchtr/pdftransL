"""Tests for the roadmap features: references skip, fallback chain,
LaTeX check, doc context extraction, bilingual assembly, parse cache,
parallel translation."""

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import LLMError
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.llm.fallback import FallbackClient
from pdftransl.masking import Masker
from pdftransl.models import Asset, BlockType, ParsedDocument
from pdftransl.parsing.cache import ParseCache
from pdftransl.parsing.splitter import mark_references, split_markdown
from pdftransl.quality.latex_check import check_document, check_formula
from pdftransl.translation.doc_context import extract_terms
from pdftransl.translation.translator import Translator, build_segments


# --- references detection -------------------------------------------------

REFS_MD = """# Paper

Some intro text about methods.

## References

[1] Smith J. et al. A great paper. Nature, 2020.

[2] Doe A. Another paper. Science, 2021.

## Appendix A

Appendix text to translate.
"""


def test_references_not_translated():
    blocks = split_markdown(REFS_MD)
    marked = mark_references(blocks)
    assert marked == 2
    ref_blocks = [b for b in blocks if b.meta.get("skipped") == "references"]
    assert all("Smith" in b.text or "Doe" in b.text for b in ref_blocks)
    # appendix after references is translatable again
    appendix = next(b for b in blocks if "Appendix text" in b.text)
    assert appendix.translatable


def test_russian_references_heading():
    blocks = split_markdown("## Список литературы\n\n[1] Иванов И. Статья. 2020.")
    assert mark_references(blocks) == 1


# --- fallback chain --------------------------------------------------------

class FailingClient(FakeLLMClient):
    def chat(self, messages, temperature=0.2, max_tokens=None, response_format=None):
        raise LLMError("simulated outage")


def test_fallback_client_switches_provider():
    ok = FakeLLMClient(responses=["перевод"])
    chain = FallbackClient([FailingClient(), ok])
    assert chain.chat([{"role": "user", "content": "hi"}]) == "перевод"


def test_fallback_client_exhausted():
    chain = FallbackClient([FailingClient(), FailingClient()])
    with pytest.raises(LLMError, match="All providers"):
        chain.chat([{"role": "user", "content": "hi"}])


# --- latex check ------------------------------------------------------------

def test_check_formula_ok():
    assert check_formula(r"\frac{a}{b} + \sqrt{x}") == []


def test_check_formula_unclosed_brace():
    assert any("unclosed" in p for p in check_formula(r"\frac{a}{b"))


def test_check_document_env_mismatch():
    issues = check_document("Text\n\n$$\\frac{a}{b$$\n\nmore")
    assert any(i.code == "latex_syntax" for i in issues)


def test_check_document_clean():
    assert check_document("Text $a+b$ and\n\n$$c = d^2$$\n") == []


# --- doc context (auto glossary) ---------------------------------------------

def test_extract_terms_parses_json():
    client = FakeLLMClient(responses=[
        '[{"term": "attention head", "translation": "головка внимания"},'
        ' {"term": "loss", "translation": "функция потерь"}]'
    ])
    cfg = PipelineConfig()
    terms = extract_terms("Some paper text", client, cfg)
    assert terms[0]["term"] == "attention head"
    assert len(terms) == 2


def test_extract_terms_survives_garbage():
    client = FakeLLMClient(responses=["I cannot do that"])
    assert extract_terms("text", client, PipelineConfig()) == []


def test_doc_terms_injected_into_prompt():
    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         doc_summary=False, max_workers=1)
    client = FakeLLMClient(
        responses=["Головка внимания вычисляет веса для всех токенов последовательности."]
    )
    translator = Translator(client, cfg)
    translator.doc_terms = [{"term": "attention head", "translation": "головка внимания"}]
    blocks = split_markdown("The attention head computes weights for all sequence tokens.")
    segments = build_segments(blocks, Masker())
    translator.translate_segments(segments)
    system = client.calls[0][0]["content"]
    assert "головка внимания" in system


# --- parallel translation ------------------------------------------------------

def test_parallel_translation_preserves_order():
    md = "\n\n".join(
        f"Paragraph number {i} talks about neural networks and data." for i in range(12)
    )
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=60)

    def fake(masked: str) -> str:
        # extract paragraph number to build a deterministic "translation"
        import re
        nums = re.findall(r"number (\d+)", masked)
        return " ".join(
            f"Абзац номер {n} рассказывает о нейронных сетях и данных." for n in nums
        )

    cfg = PipelineConfig(use_rag=False, review=False, learn=False,
                         doc_summary=False, auto_glossary=False, max_workers=6)
    translator = Translator(FakeLLMClient(transform=fake), cfg)
    translator.translate_segments(segments)
    from pdftransl.parsing.splitter import assemble

    out = assemble([s.final_text() for s in segments])
    positions = [out.find(f"Абзац номер {i} ") for i in range(12)]
    assert all(p >= 0 for p in positions)
    assert positions == sorted(positions)  # document order preserved


# --- parse cache -----------------------------------------------------------------

def test_parse_cache_roundtrip(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")
    asset_file = tmp_path / "img.png"
    asset_file.write_bytes(b"\x89PNG fake")
    parsed = ParsedDocument(
        source_path=str(pdf),
        markdown="# Cached\n\nBody.",
        backend="pymupdf",
        assets=[Asset(path=str(asset_file), rel_path="images/img.png")],
    )
    cache = ParseCache(tmp_path / "out")
    assert cache.get(pdf, "pymupdf") is None
    cache.put(pdf, parsed)
    hit = cache.get(pdf, "pymupdf")
    assert hit is not None
    assert hit.markdown == parsed.markdown
    assert hit.assets[0].rel_path == "images/img.png"
    # different backend -> miss
    assert cache.get(pdf, "mineru_local") is None
