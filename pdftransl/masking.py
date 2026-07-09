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

# Толерантный матчер плейсхолдера, который модель могла покорёжить:
# другие скобки ([ 【 〚 «), лишние пробелы, регистр, кириллические
# гомоглифы Р/Н вместо P/H, удвоенные скобки. Захватывает номер. Скобки
# обязательны с ОБЕИХ сторон — иначе «pH 12» из химии дало бы ложное
# срабатывание. Это восстанавливает искажённые токены, из-за которых
# сыпались постоянные варнинги «placeholders lost».
_FUZZY_PH_RE = re.compile(
    r"[⟦\[【〚〔｢«‹<]{1,2}\s*"
    r"[PpРр]\s*[HhНн]\s*(\d+)\s*"
    r"[⟧\]】〛〕｣»›>]{1,2}"
)

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

    After the exact pass, a *tolerant* pass recovers tokens the model
    lightly mangled (``⟦ PH 12 ⟧``, ``[PH12]``, ``⟦РН12⟧``…). Small local
    models corrupt the fancy ``⟦⟧`` brackets constantly, which used to
    flood the QA report with false "placeholder lost" errors and trigger
    pointless repair loops.
    """
    restored = text
    seen: set[str] = set()

    def _sub_exact() -> bool:
        nonlocal restored
        replaced_any = False
        for token, original in mapping.items():
            if token in restored:
                restored = restored.replace(token, original)
                seen.add(token)
                replaced_any = True
        return replaced_any

    def _sub_fuzzy() -> bool:
        """Replace mangled placeholder tokens by their number. Unrecoverable
        matches (number not in the mapping) are left untouched for the
        'unknown' report."""
        nonlocal restored
        changed = False

        def repl(m: "re.Match[str]") -> str:
            nonlocal changed
            token = PLACEHOLDER_FMT.format(m.group(1))
            original = mapping.get(token)
            if original is None:
                return m.group(0)  # hallucinated / stray — leave as-is
            seen.add(token)
            changed = True
            return original

        restored = _FUZZY_PH_RE.sub(repl, restored)
        return changed

    # each pass can reveal at most one more nesting level; +1 for safety
    for _ in range(len(mapping) + 2):
        if not (_sub_exact() or _sub_fuzzy()):
            break

    missing = [t for t in mapping if t not in seen]
    # leftover placeholder-shaped text the model invented (a real token
    # can't survive the loop above); match tolerantly so a mangled but
    # unknown token is still reported once.
    unknown: list[str] = []
    for m in _FUZZY_PH_RE.finditer(restored):
        token = PLACEHOLDER_FMT.format(m.group(1))
        if token not in mapping:
            unknown.append(m.group(0))
    return restored, missing, unknown


def strip_placeholders(text: str) -> str:
    """Remove placeholder tokens (for language-ratio statistics)."""
    return PLACEHOLDER_RE.sub(" ", text)
