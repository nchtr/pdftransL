"""Детерминированные (без LLM) проверки перевода — первая линия
самоконтроля.

Дёшево и быстро ловим типичные провалы LLM: потерянные формулы,
сломанные таблицы, непереведённые куски, пустой ответ, разгон длины.
Ошибки запускают цикл исправлений; предупреждения идут в QA-отчёт.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pdftransl.masking import strip_placeholders
from pdftransl.models import QAIssue

if TYPE_CHECKING:  # pragma: no cover
    from pdftransl.config import PipelineConfig
    from pdftransl.models import Segment

# Script ranges for residual-source-language detection.
_SCRIPTS = {
    "latin": re.compile(r"[A-Za-z]"),
    "cyrillic": re.compile(r"[А-Яа-яЁё]"),
    "cjk": re.compile(r"[一-鿿぀-ヿ]"),
}
_LANG_SCRIPT = {
    "en": "latin", "de": "latin", "fr": "latin", "es": "latin",
    "ru": "cyrillic", "uk": "cyrillic", "kk": "cyrillic",
    "zh": "cjk", "ja": "cjk",
}

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _script_word_ratio(text: str, script: str) -> float:
    """Share of words written in the given script."""
    words = _WORD_RE.findall(text)
    if not words:
        return 0.0
    pattern = _SCRIPTS[script]
    hits = sum(1 for w in words if pattern.search(w))
    return hits / len(words)


def validate_segment(segment: "Segment", config: "PipelineConfig") -> list[QAIssue]:
    """Validate a translated segment against its source. Placeholder
    integrity is checked separately during unmasking."""
    issues: list[QAIssue] = []
    source = segment.source_text
    translation = segment.translation or ""

    if not translation.strip():
        return [QAIssue("empty_translation", "translation is empty", "error")]

    # 1. Length ratio (catches truncation and runaway generation)
    src_len = max(len(strip_placeholders(segment.masked_text or source)), 1)
    ratio = len(translation) / src_len
    if ratio < config.min_length_ratio:
        issues.append(QAIssue(
            "too_short",
            f"translation suspiciously short (ratio {ratio:.2f}); "
            "possible omitted content",
            "error",
        ))
    elif ratio > config.max_length_ratio:
        issues.append(QAIssue(
            "too_long",
            f"translation suspiciously long (ratio {ratio:.2f}); "
            "possible added content or repetition loop",
            "error",
        ))

    # 2. Residual source language (untranslated chunks)
    src_script = _LANG_SCRIPT.get(config.source_lang)
    tgt_script = _LANG_SCRIPT.get(config.target_lang)
    if src_script and tgt_script and src_script != tgt_script and len(source) > 200:
        residual = _script_word_ratio(strip_placeholders(translation), src_script)
        if residual > config.max_residual_source_ratio:
            issues.append(QAIssue(
                "untranslated",
                f"{residual:.0%} of words still in source language; "
                "text appears (partially) untranslated",
                "error",
            ))

    # 3. Markdown structure: heading count
    src_headings = len(re.findall(r"(?m)^#{1,6}\s", source))
    tgt_headings = len(re.findall(r"(?m)^#{1,6}\s", translation))
    if src_headings != tgt_headings:
        issues.append(QAIssue(
            "heading_mismatch",
            f"heading count changed: {src_headings} -> {tgt_headings}",
            "warning",
        ))

    # 4. Table shape: row count and max column count
    src_rows = [l for l in source.splitlines() if l.strip().startswith("|")]
    tgt_rows = [l for l in translation.splitlines() if l.strip().startswith("|")]
    if src_rows:
        if len(src_rows) != len(tgt_rows):
            issues.append(QAIssue(
                "table_rows",
                f"table row count changed: {len(src_rows)} -> {len(tgt_rows)}",
                "error",
            ))
        else:
            src_cols = max(r.count("|") for r in src_rows)
            tgt_cols = max(r.count("|") for r in tgt_rows) if tgt_rows else 0
            if src_cols != tgt_cols:
                issues.append(QAIssue(
                    "table_cols",
                    f"table column count changed: {src_cols} -> {tgt_cols}",
                    "warning",
                ))

    # 5. Unbalanced math delimiters introduced by the model
    if translation.count("$$") % 2 != 0:
        issues.append(QAIssue(
            "math_delimiters",
            "odd number of $$ delimiters in translation",
            "warning",
        ))
    begins = len(re.findall(r"\\begin\{", translation))
    ends = len(re.findall(r"\\end\{", translation))
    if begins != ends:
        issues.append(QAIssue(
            "latex_env",
            f"\\begin/\\end mismatch in translation ({begins}/{ends})",
            "warning",
        ))

    return issues


def document_report(segments: list["Segment"]) -> dict:
    """Aggregate per-segment QA into a document-level report."""
    translated = [s for s in segments if s.kind == "translate"]
    failed = [s for s in translated if not s.ok]
    warnings = sum(
        1 for s in translated for i in s.issues if i.severity == "warning"
    )
    return {
        "segments_total": len(segments),
        "segments_translated": len(translated),
        "segments_failed": len(failed),
        "warnings": warnings,
        "attempts_total": sum(s.attempts for s in translated),
        "failed_segments": [
            {
                "id": s.id,
                "block_indices": s.block_indices,
                "issues": [i.to_dict() for i in s.issues],
                "source_preview": s.source_text[:200],
            }
            for s in failed
        ],
        "segment_issues": {
            s.id: [i.to_dict() for i in s.issues]
            for s in translated
            if s.issues
        },
    }
