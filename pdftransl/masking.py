"""Маскировка непереводимых фрагментов плейсхолдерами.

Научный Markdown полон LaTeX-формул, кода, ссылок на картинки,
цитирований и URL — всё это должно пережить перевод байт-в-байт.
Перед отправкой сегмента в LLM каждый такой фрагмент заменяется на
непрозрачный токен (``⟦PH42⟧``); после перевода токены подставляются
обратно, а их целостность проверяется (потерянный токен = потерянная
формула — это жёсткая ошибка, запускающая цикл исправлений).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER_FMT = "⟦PH{}⟧"
PLACEHOLDER_RE = re.compile(r"⟦PH(\d+)⟧")

# Порядок важен: крупные конструкции маскируются раньше своих частей
# (код-блок раньше инлайн-кода, $$...$$ раньше $...$ и т.д.).
_MASK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("code_fence", re.compile(r"```.*?```", re.DOTALL)),
    ("display_math", re.compile(r"\$\$.*?\$\$", re.DOTALL)),
    (
        "latex_env",
        re.compile(r"\\begin\{([a-zA-Z*]+)\}.*?\\end\{\1\}", re.DOTALL),
    ),
    ("bracket_math", re.compile(r"\\\[.*?\\\]", re.DOTALL)),
    ("paren_math", re.compile(r"\\\(.*?\\\)", re.DOTALL)),
    # $...$ inline math: no space right after opening / before closing $,
    # single line — avoids catching "$5 and $6" style currency text.
    ("inline_math", re.compile(r"\$(?!\s)(?:[^$\n])+?(?<![\s\\])\$")),
    ("inline_code", re.compile(r"`[^`\n]+`")),
    ("image", re.compile(r"!\[[^\]]*\]\([^)]*\)")),
    # Mask "](url)" of markdown links, keeping the link text translatable.
    ("link_target", re.compile(r"\]\([^)\s]+\)")),
    (
        "latex_ref",
        re.compile(r"\\(?:cite[tp]?|ref|eqref|autoref|cref|label|url|href)\*?\{[^}]*\}"),
    ),
    ("citation", re.compile(r"\[\d+(?:\s*[,;–-]\s*\d+)*\]")),
    # ⟦⟧ excluded: a URL/tag must not swallow an already-inserted
    # placeholder token, or the mapping nests (url containing masked math).
    ("url", re.compile(r"https?://[^\s)\]>⟦⟧]+")),
    ("html_tag", re.compile(r"</?[a-zA-Z][^<>\n⟦⟧]*>")),
]


@dataclass
class MaskResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # token -> original


class Masker:
    """Stateful masker producing document-unique placeholder tokens."""

    def __init__(self, start: int = 0):
        self._counter = start

    def mask(self, text: str) -> MaskResult:
        mapping: dict[str, str] = {}

        def _sub(match: re.Match[str]) -> str:
            token = PLACEHOLDER_FMT.format(self._counter)
            self._counter += 1
            mapping[token] = match.group(0)
            return token

        masked = text
        for _name, pattern in _MASK_PATTERNS:
            masked = pattern.sub(_sub, masked)
        return MaskResult(text=masked, mapping=mapping)


def unmask(text: str, mapping: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Restore placeholders.

    Returns ``(restored_text, missing_tokens, unknown_tokens)`` where
    *missing* are expected tokens absent from the translation and
    *unknown* are placeholder-looking tokens the model hallucinated.

    Substitution runs to a fixpoint: when patterns nest (a masked URL or
    LaTeX \\ref whose body itself contains an earlier placeholder), the
    inner token only appears *after* the outer one is expanded — a single
    pass would leave it in the output as literal ``⟦PHn⟧`` junk and
    misreport it as both missing and unknown.
    """
    restored = text
    seen: set[str] = set()
    # each pass can reveal at most one more nesting level; +1 for safety
    for _ in range(len(mapping) + 1):
        replaced_any = False
        for token, original in mapping.items():
            if token in restored:
                restored = restored.replace(token, original)
                seen.add(token)
                replaced_any = True
        if not replaced_any:
            break
    missing = [t for t in mapping if t not in seen]
    unknown = [
        m.group(0)
        for m in PLACEHOLDER_RE.finditer(restored)
        if m.group(0) not in mapping
        # in-mapping tokens can't survive the fixpoint loop above, so
        # whatever placeholder-shaped text remains was hallucinated
    ]
    return restored, missing, unknown


def strip_placeholders(text: str) -> str:
    """Remove placeholder tokens (for language-ratio statistics)."""
    return PLACEHOLDER_RE.sub(" ", text)
