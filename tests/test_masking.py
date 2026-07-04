from pdftransl.masking import Masker, strip_placeholders, unmask


def roundtrip(text: str) -> None:
    masker = Masker()
    result = masker.mask(text)
    restored, missing, unknown = unmask(result.text, result.mapping)
    assert restored == text
    assert not missing and not unknown


def test_inline_math_masked():
    masker = Masker()
    res = masker.mask("The energy $E = mc^2$ is famous.")
    assert "$E = mc^2$" not in res.text
    assert "⟦PH" in res.text
    assert "$E = mc^2$" in res.mapping.values()


def test_display_math_roundtrip():
    roundtrip("Before\n$$\n\\int_0^1 f(x)\\,dx = \\frac{1}{2}\n$$\nAfter")


def test_latex_env_roundtrip():
    roundtrip("\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}")


def test_code_fence_roundtrip():
    roundtrip("Text\n```python\nx = '$notmath$'\n```\nMore")


def test_image_and_link():
    masker = Masker()
    text = "See ![fig 1](images/fig1.png) and [the paper](https://arxiv.org/abs/1234)."
    res = masker.mask(text)
    assert "images/fig1.png" not in res.text
    assert "https://arxiv.org" not in res.text
    assert "the paper" in res.text  # link text stays translatable
    restored, missing, unknown = unmask(res.text, res.mapping)
    assert restored == text


def test_citation_masked():
    masker = Masker()
    res = masker.mask("As shown in [12] and [3, 4].")
    assert "[12]" not in res.text
    assert "[3, 4]" not in res.text


def test_currency_not_masked():
    masker = Masker()
    res = masker.mask("It costs $5 and then $6 more.")
    # "$5 and then $6" must not be swallowed as inline math with spaces
    assert "costs" in res.text and "more" in res.text


def test_missing_placeholder_detected():
    masker = Masker()
    res = masker.mask("Value $x$ here.")
    token = next(iter(res.mapping))
    translated = "Значение здесь."  # model dropped the placeholder
    restored, missing, unknown = unmask(translated, res.mapping)
    assert missing == [token]


def test_unknown_placeholder_detected():
    restored, missing, unknown = unmask("Текст ⟦PH999⟧", {})
    assert unknown == ["⟦PH999⟧"]


def test_strip_placeholders():
    assert "⟦PH1⟧" not in strip_placeholders("a ⟦PH1⟧ b")


def test_latex_ref_commands():
    roundtrip("See \\cite{smith2020} and \\ref{eq:main} plus \\eqref{eq:2}.")
