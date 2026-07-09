"""Tests for: robust placeholder restoration (tolerant unmask),
residual re-translation of source-language chunks, and LLM layout repair.
"""

import re

import pytest

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.masking import Masker, unmask
from pdftransl.models import Segment
from pdftransl.quality.validators import validate_segment


# ---- robust unmask (root cause of "placeholder lost" spam) ----------------

_MAP = {"⟦PH0⟧": "$E=mc^2$", "⟦PH1⟧": "\\cite{smith}", "⟦PH2⟧": "$x$"}


@pytest.mark.parametrize("mangled", [
    "Текст ⟦ PH 0 ⟧ и ⟦PH1⟧ конец ⟦PH2⟧.",     # spaces inside
    "Текст [PH0] и [PH1] конец [PH2].",          # square brackets
    "Текст ⟦⟦PH0⟧⟧ и ⟦PH1⟧ конец ⟦PH2⟧.",       # doubled brackets
    "Текст ⟦РН0⟧ и ⟦PH1⟧ конец ⟦PH2⟧.",         # cyrillic homoglyph РН
    "Текст ⟦ph0⟧ и ⟦PH1⟧ конец ⟦PH2⟧.",         # lowercase
    "Текст «PH0» и ⟦PH1⟧ конец ⟦PH2⟧.",         # guillemets
])
def test_unmask_recovers_mangled_placeholders(mangled):
    restored, missing, unknown = unmask(mangled, dict(_MAP))
    assert "$E=mc^2$" in restored
    assert "\\cite{smith}" in restored and "$x$" in restored
    assert not missing and not unknown


def test_unmask_reports_truly_missing():
    restored, missing, unknown = unmask("Текст ⟦PH1⟧ ⟦PH2⟧.", dict(_MAP))
    assert missing == ["⟦PH0⟧"]


def test_unmask_reports_hallucinated():
    restored, missing, unknown = unmask(
        "Текст ⟦PH0⟧ ⟦PH1⟧ ⟦PH2⟧ ⟦PH99⟧.", dict(_MAP))
    assert unknown == ["⟦PH99⟧"] and not missing


def test_unmask_does_not_touch_chemistry_ph():
    # bracketless "pH 12" must NOT be treated as a placeholder
    restored, missing, unknown = unmask(
        "Раствор при pH 12 ⟦PH0⟧ ⟦PH1⟧ ⟦PH2⟧.", dict(_MAP))
    assert "pH 12" in restored
    assert not missing and not unknown


def test_unmask_still_handles_nesting():
    m = Masker().mask("see https://a.b/$y$/c and $z$")
    restored, missing, unknown = unmask(m.text, m.mapping)
    assert restored == "see https://a.b/$y$/c and $z$"
    assert not missing and not unknown


# ---- residual re-translation ----------------------------------------------

_LONG_EN = (
    "This entire paragraph was left in English by the model because it is a "
    "long sentence about neural networks and data systems that exceeds two "
    "hundred characters so the validator flags it as untranslated residual "
    "source text needing another pass."
)


def _cfg(**kw):
    return PipelineConfig(use_rag=False, review=False, learn=False,
                          source_lang="en", target_lang="ru", **kw)


def _make_untranslated_segment():
    from pdftransl.translation.translator import Translator  # noqa: F401
    m = Masker().mask(_LONG_EN)
    seg = Segment(id="s", kind="translate", source_text=_LONG_EN,
                  masked_text=m.text, placeholders=m.mapping)
    seg.translation = _LONG_EN                      # model left it in English
    cfg = _cfg()
    seg.issues = validate_segment(seg, cfg)
    return seg, cfg


def test_untranslated_flag_present():
    seg, _ = _make_untranslated_segment()
    assert any(i.code == "untranslated" for i in seg.issues)


def test_retranslate_residual_fixes_untranslated():
    from pdftransl.translation.translator import Translator

    seg, cfg = _make_untranslated_segment()
    client = FakeLLMClient(transform=lambda s: re.sub(r"[A-Za-z]+", "текст", s))
    fixed = Translator(client, cfg).retranslate_residual([seg])
    assert fixed == 1
    assert re.search(r"[А-Яа-я]", seg.translation)  # now Russian
    assert not any(i.code == "untranslated" for i in seg.issues)


def test_retranslate_keeps_original_if_not_improved():
    from pdftransl.translation.translator import Translator

    seg, cfg = _make_untranslated_segment()
    original = seg.translation
    # a "re-translation" that still returns English -> rejected
    client = FakeLLMClient(transform=lambda s: s)
    fixed = Translator(client, cfg).retranslate_residual([seg])
    assert fixed == 0
    assert seg.translation == original


def test_retranslate_skips_clean_segments():
    from pdftransl.translation.translator import Translator

    m = Masker().mask("Short text.")
    seg = Segment(id="s", kind="translate", source_text="Short text.",
                  masked_text=m.text, placeholders=m.mapping)
    seg.translation = "Короткий текст."
    seg.issues = []                                 # no untranslated flag
    calls = []
    client = FakeLLMClient(transform=lambda s: calls.append(s) or s)
    Translator(client, _cfg()).retranslate_residual([seg])
    assert not calls                                # never called the LLM


# ---- document layout repair ------------------------------------------------

def test_content_preserved_guard():
    from pdftransl.quality.document_repair import _content_preserved

    assert _content_preserved("abc def ghi jkl", "abc def ghi jkl")
    assert not _content_preserved("a b c d e f g h", "a b")       # lost content
    assert not _content_preserved("x $a$ y $b$", "x $a$ y")       # lost a formula


def test_repair_layout_joins_split_and_guards(tmp_path):
    from pdftransl.quality.document_repair import repair_layout

    md = ("Первая часть предложения\nвторая часть про нейросети и данные.\n\n"
          "## Раздел\n\nОбычный абзац достаточной длины для обработки корректором.")

    class Joiner(FakeLLMClient):
        def chat(self, messages, temperature=0.2, max_tokens=None, response_format=None):
            u = next(m for m in reversed(messages) if m["role"] == "user")["content"]
            return u.replace("предложения\nвторая", "предложения вторая")

    out, fixed = repair_layout(md, Joiner(), PipelineConfig())
    assert "Первая часть предложения вторая" in out
    assert fixed >= 1


def test_repair_layout_rejects_content_loss():
    from pdftransl.quality.document_repair import repair_layout

    md = "Осмысленный абзац про нейронные сети, данные и обучение моделей глубоко."

    class Destroyer(FakeLLMClient):
        def chat(self, *a, **k):
            return "короче"                          # throws away content

    out, fixed = repair_layout(md, Destroyer(), PipelineConfig())
    assert out == md                                # kept original
    assert fixed == 0
