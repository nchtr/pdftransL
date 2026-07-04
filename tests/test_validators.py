from pdftransl.config import PipelineConfig
from pdftransl.models import Segment
from pdftransl.quality.validators import validate_segment


def make_segment(source: str, translation: str) -> Segment:
    seg = Segment(id="s1", kind="translate", source_text=source)
    seg.masked_text = source
    seg.translation = translation
    return seg


CFG = PipelineConfig()

LONG_EN = (
    "The quick brown fox jumps over the lazy dog. " * 8
)


def test_empty_translation_is_error():
    seg = make_segment("Some source text.", "   ")
    issues = validate_segment(seg, CFG)
    assert any(i.code == "empty_translation" and i.severity == "error" for i in issues)


def test_untranslated_text_detected():
    seg = make_segment(LONG_EN, LONG_EN)  # "translation" identical to source
    issues = validate_segment(seg, CFG)
    assert any(i.code == "untranslated" for i in issues)


def test_good_translation_passes():
    ru = "Быстрая коричневая лиса перепрыгивает через ленивую собаку. " * 8
    seg = make_segment(LONG_EN, ru)
    issues = validate_segment(seg, CFG)
    assert not any(i.severity == "error" for i in issues)


def test_truncation_detected():
    seg = make_segment(LONG_EN, "Коротко.")
    issues = validate_segment(seg, CFG)
    assert any(i.code == "too_short" for i in issues)


def test_table_rows_checked():
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    bad = "| а | б |\n|---|---|"
    seg = make_segment(src, bad)
    issues = validate_segment(seg, CFG)
    assert any(i.code == "table_rows" and i.severity == "error" for i in issues)


def test_heading_mismatch_is_warning():
    seg = make_segment("# One\n\ntext here", "Один\n\nтекст здесь")
    issues = validate_segment(seg, CFG)
    assert any(i.code == "heading_mismatch" for i in issues)
