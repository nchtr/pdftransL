"""Prompt construction for translation, repair and review."""

from __future__ import annotations

LANG_NAMES = {
    "en": "English",
    "ru": "Russian",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "zh": "Chinese",
    "ja": "Japanese",
    "uk": "Ukrainian",
    "kk": "Kazakh",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code.lower(), code)


TRANSLATION_SYSTEM = """\
You are a professional translator of scientific papers from {src} to {tgt}.

STRICT RULES:
1. Translate ONLY natural-language text. Return ONLY the translated markdown, \
no explanations, no preface, no code fences around the whole answer.
2. Placeholder tokens like ⟦PH12⟧ stand for formulas, code, links and images. \
Copy every placeholder EXACTLY as-is, in the position where its content \
belongs. Never translate, alter, merge, drop or invent placeholders.
3. Preserve markdown structure exactly: heading levels (#), lists, table \
layout (same number of rows and columns, | separators), bold/italic markers.
4. Keep any remaining LaTeX untouched: commands, math, \\cite/\\ref keys.
5. Do not translate: author names (transliterate only if standard), \
bibliographic entries' titles inside references, identifiers, dataset/model \
names, units of measurement.
6. Use established {tgt} scientific terminology; keep terminology consistent \
across the document.
{glossary}{examples}"""

GLOSSARY_TEMPLATE = """
TERMINOLOGY (use exactly these translations):
{terms}
"""

SUMMARY_TEMPLATE = """
DOCUMENT CONTEXT (what this paper is about):
{summary}
"""

SOURCE_CONTEXT_TEMPLATE = """
PRECEDING TEXT (context only — do NOT translate or include it):
…{context}
"""

EXAMPLES_TEMPLATE = """
REFERENCE TRANSLATIONS from the translation memory (match their style \
and terminology):
{examples}
"""

REPAIR_USER = """\
Your previous translation has problems that must be fixed:
{issues}

Source text (with placeholders):
---
{source}
---

Your previous translation:
---
{translation}
---

Return the corrected translation ONLY, with every placeholder ⟦PH…⟧ from \
the source present exactly once and markdown structure preserved."""

REVIEW_SYSTEM = """\
You are a meticulous reviewer of scientific translations ({src} -> {tgt}).
Check the candidate translation for: mistranslations, omissions, additions, \
terminology errors, broken markdown, altered placeholder tokens (⟦PH…⟧).
Respond with JSON only: {{"ok": true}} if the translation is good, or \
{{"ok": false, "revised": "<full corrected translation>", "notes": "<short list of fixes>"}}.
Do not wrap the JSON in markdown fences."""

REVIEW_USER = """\
Source:
---
{source}
---
Candidate translation:
---
{translation}
---"""

FIGURE_SYSTEM = """\
You are analyzing a figure from a scientific paper. Describe in {tgt}: \
what the figure shows, axes and their units, key trends or components. \
Transcribe short text labels and translate them to {tgt} in parentheses. \
Be concise (3-6 sentences)."""


def build_translation_system(
    src: str,
    tgt: str,
    glossary_terms: list[dict[str, str]] | None = None,
    tm_examples: list[dict[str, str]] | None = None,
    doc_summary: str | None = None,
) -> str:
    glossary = ""
    if glossary_terms:
        # de-duplicate by term (document glossary + stored glossary)
        seen: set[str] = set()
        lines = []
        for t in glossary_terms:
            key = t["term"].lower()
            if key not in seen:
                seen.add(key)
                lines.append(f"- {t['term']} -> {t['translation']}")
        glossary = GLOSSARY_TEMPLATE.format(terms="\n".join(lines))
    examples = ""
    if tm_examples:
        parts = []
        for ex in tm_examples:
            src_short = ex["source"][:600]
            tgt_short = ex["target"][:600]
            parts.append(f"SOURCE: {src_short}\nTRANSLATION: {tgt_short}")
        examples = EXAMPLES_TEMPLATE.format(examples="\n---\n".join(parts))
    system = TRANSLATION_SYSTEM.format(
        src=lang_name(src), tgt=lang_name(tgt), glossary=glossary, examples=examples
    )
    if doc_summary:
        system += SUMMARY_TEMPLATE.format(summary=doc_summary[:1500])
    return system


def build_user_message(masked_text: str, source_context: str | None = None) -> str:
    if source_context:
        return (
            SOURCE_CONTEXT_TEMPLATE.format(context=source_context)
            + "\nTEXT TO TRANSLATE:\n" + masked_text
        )
    return masked_text
