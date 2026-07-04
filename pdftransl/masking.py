"""Placeholder masking for non-translatable fragments.

Scientific Markdown contains LaTeX formulas, code, image links,
citations and URLs that must survive translation byte-for-byte.
Before a segment is sent to the LLM every such fragment is replaced
by an opaque placeholder token (``⟦PH42⟧``); after translation the
placeholders are substituted back and their integrity is verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER_FMT = "⟦PH{}⟧"
PLACEHOLDER_RE = re.compile(r"⟦PH(\d+)⟧")

# Order matters: larger constructs must be masked before their parts.
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
    ("url", re.compile(r"https?://[^\s)\]>]+")),
    ("html_tag", re.compile(r"</?[a-zA-Z][^<>\n]*>")),
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
    """
    restored = text
    missing: list[str] = []
    for token, original in mapping.items():
        if token in restored:
            restored = restored.replace(token, original)
        else:
            missing.append(token)
    unknown = [
        m.group(0)
        for m in PLACEHOLDER_RE.finditer(restored)
        # tokens still present after substitution were never in the mapping
    ]
    return restored, missing, unknown


def strip_placeholders(text: str) -> str:
    """Remove placeholder tokens (for language-ratio statistics)."""
    return PLACEHOLDER_RE.sub(" ", text)
