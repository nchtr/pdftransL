import re

from pdftransl.config import PipelineConfig
from pdftransl.llm.fake import FakeLLMClient
from pdftransl.masking import Masker
from pdftransl.parsing.splitter import assemble, split_markdown
from pdftransl.translation.translator import Translator, build_segments

MD = """# Attention Is All You Need

The dominant sequence transduction models use $O(n^2)$ attention.

$$
\\mathrm{Attention}(Q, K, V) = \\mathrm{softmax}\\left(\\frac{QK^T}{\\sqrt{d_k}}\\right)V
$$

We propose the Transformer architecture."""

# Fake "Russian translations" (per segment, placeholders kept intact).
RU_SEGMENT_1 = (
    "# Внимание — всё, что нужно\n\n"
    "Доминирующие модели преобразования последовательностей используют "
    "внимание сложности {ph}."
)
RU_SEGMENT_2 = "Мы предлагаем архитектуру Трансформер."


def make_config(**kw):
    return PipelineConfig(use_rag=False, review=False, learn=False, **kw)


def test_segments_pass_and_translate():
    blocks = split_markdown(MD)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    kinds = [s.kind for s in segments]
    # heading+paragraph merge, math passes through, last paragraph translates
    assert "pass" in kinds and "translate" in kinds
    math_seg = next(s for s in segments if s.kind == "pass")
    assert "\\mathrm{Attention}(Q, K, V)" in math_seg.source_text


def test_placeholder_preserving_translation():
    blocks = split_markdown(MD)
    segments = build_segments(blocks, Masker(), char_budget=4000)

    def fake_translate(masked: str) -> str:
        tokens = re.findall(r"⟦PH\d+⟧", masked)
        if tokens:  # first segment: heading + paragraph with $O(n^2)$
            return RU_SEGMENT_1.format(ph=tokens[0])
        return RU_SEGMENT_2

    client = FakeLLMClient(transform=fake_translate)
    translator = Translator(client, make_config())
    translator.translate_segments(segments)

    out = assemble([s.final_text() for s in segments])
    assert "$O(n^2)$" in out                # formula restored
    assert "\\sqrt{d_k}" in out             # display math untouched
    assert "Трансформер" in out             # text translated
    for segment in segments:
        if segment.kind == "translate":
            assert segment.ok, [i.message for i in segment.issues]


def test_repair_loop_recovers_lost_placeholder():
    md = "The value $x_1$ appears in the model output."
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    seg = segments[0]
    token = next(iter(seg.placeholders))

    bad = "Значение появляется в выводе модели много раз и подробно."  # lost token
    good = f"Значение {token} появляется в выводе модели много раз и подробно."
    client = FakeLLMClient(responses=[bad, good])
    translator = Translator(client, make_config(max_repair_attempts=2))
    translator.translate_segment(seg)

    assert seg.attempts == 2
    assert seg.ok
    assert "$x_1$" in seg.translation


def test_failed_segment_keeps_source_after_attempts():
    md = "The value $x_1$ appears in the output of the model."
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    seg = segments[0]

    bad = "Значение появляется в выводе модели снова и снова."
    client = FakeLLMClient(responses=[bad, bad, bad])
    translator = Translator(client, make_config(max_repair_attempts=2))
    translator.translate_segment(seg)

    assert not seg.ok
    assert any(i.code == "placeholder_missing" for i in seg.issues)


def test_wrapping_fence_stripped():
    md = "Simple text paragraph for translation into Russian language."
    blocks = split_markdown(md)
    segments = build_segments(blocks, Masker(), char_budget=4000)
    client = FakeLLMClient(
        responses=["```markdown\nПростой текстовый абзац для перевода на русский язык.\n```"]
    )
    translator = Translator(client, make_config())
    translator.translate_segment(segments[0])
    assert segments[0].translation.startswith("Простой")
    assert "```" not in segments[0].translation
