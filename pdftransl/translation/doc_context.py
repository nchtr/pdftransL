"""Document-level context: rolling summary and auto-extracted glossary.

Both run once per document before segment translation:
- the summary tells the model what the paper is about, improving
  disambiguation of terms in every segment;
- extracted term pairs act as a per-document glossary, keeping
  terminology consistent across independently translated segments.
Failures are non-fatal — the pipeline degrades to plain translation.
"""

from __future__ import annotations

import json
import logging
import re

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.translation.prompts import lang_name

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = """\
You are preparing context for a translator. Summarize the scientific
paper excerpt in 3-5 sentences in {tgt}: research field, problem,
approach, key objects/models. No formulas, no markdown."""

_TERMS_SYSTEM = """\
You extract terminology from scientific papers for a translation
glossary ({src} -> {tgt}). From the given excerpt select up to
{limit} domain-specific terms (multi-word where appropriate) that
must be translated consistently. Respond with a JSON array only:
[{{"term": "...", "translation": "..."}}]. No comments, no fences."""

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def build_doc_summary(
    markdown: str, client: BaseLLMClient, config: PipelineConfig,
    max_chars: int = 6000,
) -> str:
    """Summary of the paper head (title/abstract/intro) for prompts."""
    excerpt = markdown[:max_chars]
    try:
        return client.chat(
            [
                {"role": "system",
                 "content": _SUMMARY_SYSTEM.format(tgt=lang_name(config.target_lang))},
                {"role": "user", "content": excerpt},
            ],
            temperature=0.2,
        ).strip()
    except Exception as exc:
        logger.warning("Document summary failed: %s", exc)
        return ""


def extract_terms(
    markdown: str, client: BaseLLMClient, config: PipelineConfig,
    max_chars: int = 8000, limit: int = 25,
) -> list[dict[str, str]]:
    """LLM-extracted per-document term glossary."""
    excerpt = markdown[:max_chars]
    response_format = (
        {"type": "json_object"} if config.structured_outputs else None
    )
    try:
        raw = client.chat(
            [
                {"role": "system",
                 "content": _TERMS_SYSTEM.format(
                     src=lang_name(config.source_lang),
                     tgt=lang_name(config.target_lang),
                     limit=limit,
                 )},
                {"role": "user", "content": excerpt},
            ],
            temperature=0.0,
            response_format=response_format,
        )
    except Exception as exc:
        logger.warning("Term extraction failed: %s", exc)
        return []
    match = _JSON_ARRAY_RE.search(raw)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    terms = []
    for item in items[:limit]:
        if isinstance(item, dict) and item.get("term") and item.get("translation"):
            terms.append({"term": str(item["term"]).strip(),
                          "translation": str(item["translation"]).strip()})
    return terms
