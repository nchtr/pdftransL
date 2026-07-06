"""LLM-judge quality scoring (0-100) per segment.

A cheap numeric signal that goes beyond structural validators: the
judge model rates adequacy and fluency of each translated segment.
Scores below the threshold add a warning issue, which makes the
segment eligible for the LLM review pass. Off by default — it costs
one extra call per segment; enable it for high-stakes documents or
sample it in production.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.models import QAIssue, Segment
from pdftransl.translation.prompts import lang_name

logger = logging.getLogger(__name__)

_SCORE_SYSTEM = """\
You are a strict translation quality judge ({src} -> {tgt}).
Rate the candidate translation for adequacy (meaning preserved) and
fluency (natural {tgt}). Ignore placeholder tokens like ⟦PH12⟧ — they
stand for formulas and must simply be present.
Respond with JSON only: {{"score": <0-100 integer>, "comment": "<one short sentence>"}}."""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def score_segment(
    segment: Segment,
    client: BaseLLMClient,
    config: PipelineConfig,
) -> Optional[float]:
    if segment.kind != "translate" or not segment.translation:
        return None
    system = _SCORE_SYSTEM.format(
        src=lang_name(config.source_lang), tgt=lang_name(config.target_lang)
    )
    user = (
        f"SOURCE:\n{segment.masked_text or segment.source_text}\n\n"
        f"TRANSLATION:\n{segment.translation}"
    )
    response_format = {"type": "json_object"} if config.structured_outputs else None
    try:
        raw = client.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            response_format=response_format,
        )
    except Exception as exc:
        logger.warning("Quality scoring failed for %s: %s", segment.id, exc)
        return None
    match = _JSON_RE.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        score = float(data["score"])
    except (ValueError, KeyError, TypeError):
        return None
    if score < config.quality_score_threshold:
        segment.issues.append(QAIssue(
            "low_quality_score",
            f"judge score {score:.0f}/100 "
            f"(threshold {config.quality_score_threshold:.0f}): "
            f"{str(data.get('comment', ''))[:200]}",
            "warning",
        ))
    return score


def score_segments(
    segments: list[Segment],
    client: BaseLLMClient,
    config: PipelineConfig,
) -> dict:
    """Score all translated segments; returns summary for the report."""
    scores: dict[str, float] = {}
    for segment in segments:
        score = score_segment(segment, client, config)
        if score is not None:
            scores[segment.id] = score
    if not scores:
        return {}
    values = list(scores.values())
    return {
        "mean": round(sum(values) / len(values), 1),
        "min": min(values),
        "below_threshold": sum(
            1 for v in values if v < config.quality_score_threshold
        ),
        "per_segment": scores,
    }
