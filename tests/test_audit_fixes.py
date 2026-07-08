"""Regression tests for bugs found in the manual project audit:

1. Nested placeholders (a masked URL/\\href whose body contains an
   earlier-masked formula) left literal ⟦PHn⟧ junk in the output and
   misreported it as missing+unknown.
2. mark_references() disabled translation for EVERY section after the
   bibliography unless the next heading matched a small allow-list —
   a Conclusion/Nomenclature after References stayed untranslated.
3. Segment.final_text() shipped translations whose placeholder tokens
   were destroyed by the model — silently dropping formulas/citations.
4. The reviewer compared revisions against *tokens* while showing the
   model *restored* text, so every revision of a placeholder-bearing
   segment was rejected.
5. export_document() left a stray .html file when only PDF was
   requested (it is just the chromium-PDF input).
"""

import json

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.masking import Masker, unmask
from pdftransl.models import QAIssue, Segment
from pdftransl.parsing.splitter import mark_references, split_markdown
from pdftransl.quality.reviewer import Reviewer


# ---- 1. nested placeholders ------------------------------------------------

def test_unmask_expands_nested_url_math():
    text = "As shown at https://example.com/$e=mc^2$/page the value $x$ grows."
    m = Masker().mask(text)
    restored, missing, unknown = unmask(m.text, m.mapping)
    assert restored == text
    assert not missing and not unknown


def test_unmask_expands_nested_href():
    text = r"See \href{https://x.com}{$y$} end."
    m = Masker().mask(text)
    restored, missing, unknown = unmask(m.text, m.mapping)
    assert restored == text
    assert not missing and not unknown


def test_unmask_still_reports_hallucinated_tokens():
    m = Masker().mask("Value $q$ here.")
    translated = m.text.replace("⟦PH0⟧", "⟦PH0⟧ ⟦PH99⟧")
    restored, missing, unknown = unmask(translated, m.mapping)
    assert unknown == ["⟦PH99⟧"]
    assert not missing
    assert "$q$" in restored


def test_url_mask_does_not_swallow_placeholder():
    m = Masker().mask("URL https://a.b/$x$/c here")
    # the math token must not end up nested inside the url's original
    assert all("⟦PH" not in orig for orig in m.mapping.values())


# ---- 2. references section ends at any heading ------------------------------

MD_REFS = """# Title

Intro paragraph.

# References

[1] Smith J. Some paper. 2020.

# Conclusion

This must be translated.

# Список литературы

[2] Иванов И. Статья. 2021.

# Nomenclature

Symbols used in this paper.
"""


def test_any_heading_ends_references():
    blocks = split_markdown(MD_REFS)
    mark_references(blocks)
    by_text = {b.text: b for b in blocks}
    assert by_text["Intro paragraph."].translatable
    assert not by_text["[1] Smith J. Some paper. 2020."].translatable
    assert by_text["This must be translated."].translatable      # the old bug
    assert not by_text["[2] Иванов И. Статья. 2021."].translatable
    assert by_text["Symbols used in this paper."].translatable


# ---- 3. final_text falls back when placeholders were destroyed --------------

def test_final_text_falls_back_on_lost_placeholders():
    seg = Segment(id="s", kind="translate",
                  source_text="Loss $L=x^2$ decreases, see [1].")
    seg.translation = "битый ⟦хх0⟧ текст без формулы"
    seg.issues = [QAIssue("placeholder_missing", "lost ⟦PH0⟧", "error")]
    assert seg.final_text() == seg.source_text


def test_final_text_keeps_translation_on_other_errors():
    seg = Segment(id="s", kind="translate", source_text="Some text.")
    seg.translation = "Какой-то текст."
    seg.issues = [QAIssue("too_short", "suspicious", "error")]
    assert seg.final_text() == seg.translation


# ---- 4. reviewer accepts content-bearing revisions --------------------------

def test_reviewer_accepts_revision_with_real_content():
    src = "The loss $L=x^2$ decreases."
    m = Masker().mask(src)
    seg = Segment(id="s", kind="translate", source_text=src,
                  masked_text=m.text, placeholders=m.mapping)
    seg.translation = "Лосс $L=x^2$ падает."
    seg.issues = [QAIssue("too_short", "flagged", "error")]
    revised = "Функция потерь $L=x^2$ убывает."  # real formula, no tokens
    client = FakeLLMClient(responses=[
        json.dumps({"ok": False, "revised": revised, "notes": "style"})
    ])
    Reviewer(client, PipelineConfig(review=True, use_rag=False)).review_segment(seg)
    assert seg.translation == revised
    assert any(i.code == "reviewed_revised" for i in seg.issues)


def test_reviewer_still_rejects_revision_losing_content():
    src = "The loss $L=x^2$ decreases."
    m = Masker().mask(src)
    seg = Segment(id="s", kind="translate", source_text=src,
                  masked_text=m.text, placeholders=m.mapping)
    seg.translation = "Лосс $L=x^2$ падает."
    seg.issues = [QAIssue("too_short", "flagged", "error")]
    original_translation = seg.translation
    client = FakeLLMClient(responses=[
        json.dumps({"ok": False, "revised": "Потеря уменьшается.", "notes": ""})
    ])
    Reviewer(client, PipelineConfig(review=True, use_rag=False)).review_segment(seg)
    assert seg.translation == original_translation
    assert any(i.code == "review_rejected" for i in seg.issues)


# ---- 5. no stray .html when only other formats requested --------------------

def test_export_no_stray_html(tmp_path):
    from pdftransl.export.exporter import export_document

    out = export_document("# T\n\nBody.", tmp_path / "doc", formats=["pdf"])
    assert not (tmp_path / "doc.html").exists()
    # and requesting html keeps it, of course
    out = export_document("# T\n\nBody.", tmp_path / "doc2", formats=["html"])
    assert (tmp_path / "doc2.html").exists()
    assert out["files"]["html"]
