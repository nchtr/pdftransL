"""Back-translation semantic check (optional, off by default).

Translate the result back to the source language with the same client
and compare embeddings of the original vs the back-translation. Low
similarity flags meaning loss that structural validators cannot see.
Costs one extra LLM call per checked segment — enable selectively.
"""

from __future__ import annotations

import logging

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.masking import strip_placeholders
from pdftransl.models import QAIssue, Segment
from pdftransl.rag.embeddings import BaseEmbedder, cosine
from pdftransl.translation.prompts import build_translation_system

logger = logging.getLogger(__name__)


def check_segment(
    segment: Segment,
    client: BaseLLMClient,
    embedder: BaseEmbedder,
    config: PipelineConfig,
) -> Segment:
    if segment.kind != "translate" or not segment.translation:
        return segment
    system = build_translation_system(config.target_lang, config.source_lang)
    try:
        back = client.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": segment.translation},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning("Back-translation failed for %s: %s", segment.id, exc)
        return segment

    source_plain = strip_placeholders(segment.source_text)
    vec_src, vec_back = embedder.embed([source_plain, back])
    similarity = cosine(vec_src, vec_back)
    if similarity < config.backtranslation_min_similarity:
        segment.issues.append(QAIssue(
            "backtranslation",
            f"back-translation similarity {similarity:.2f} below "
            f"{config.backtranslation_min_similarity} — possible meaning loss",
            "warning",
        ))
    return segment


def check_segments(
    segments: list[Segment],
    client: BaseLLMClient,
    embedder: BaseEmbedder,
    config: PipelineConfig,
) -> list[Segment]:
    for segment in segments:
        check_segment(segment, client, embedder, config)
    return segments
