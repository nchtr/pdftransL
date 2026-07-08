"""LLM review pass — second line of self-control.

After deterministic validators, segments (all or only flagged ones)
can be re-checked by an LLM acting as a reviewer. The reviewer either
approves the translation or returns a revised version, which is then
re-validated with the same deterministic checks.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.masking import unmask
from pdftransl.models import QAIssue, Segment
from pdftransl.quality.validators import validate_segment
from pdftransl.translation.prompts import REVIEW_SYSTEM, REVIEW_USER, lang_name

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class Reviewer:
    def __init__(self, client: BaseLLMClient, config: PipelineConfig):
        self.client = client
        self.config = config

    def review_segment(self, segment: Segment) -> Segment:
        """Ask the LLM to critique/fix a translated segment in place."""
        if segment.kind != "translate" or not segment.translation:
            return segment
        system = REVIEW_SYSTEM.format(
            src=lang_name(self.config.source_lang),
            tgt=lang_name(self.config.target_lang),
        )
        user = REVIEW_USER.format(
            source=segment.masked_text or segment.source_text,
            translation=segment.translation,
        )
        response_format = (
            {"type": "json_object"} if self.config.structured_outputs else None
        )
        try:
            raw = self.client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                response_format=response_format,
            )
        except Exception as exc:  # review must never break the pipeline
            logger.warning("Review failed for %s: %s", segment.id, exc)
            segment.issues.append(
                QAIssue("review_error", f"review pass failed: {exc}", "warning")
            )
            return segment

        verdict = _parse_review(raw)
        if verdict is None:
            segment.issues.append(
                QAIssue("review_unparsed", "reviewer returned unparsable output", "warning")
            )
            return segment

        if verdict.get("ok"):
            segment.issues.append(QAIssue("reviewed_ok", "approved by LLM reviewer", "info"))
            return segment

        revised = verdict.get("revised")
        if revised:
            restored, missing, unknown = unmask(revised.strip(), segment.placeholders)
            # The reviewer sees the *restored* translation (real formulas,
            # not tokens), so a good revision usually carries the actual
            # content instead of ⟦PH…⟧ tokens. A token is only truly
            # missing if its content is absent too — otherwise every
            # revision of a placeholder-bearing segment would be rejected.
            missing = [
                t for t in missing if segment.placeholders[t] not in restored
            ]
            # accept the revision only if it doesn't break placeholders
            if not missing and not unknown:
                segment.translation = restored
                segment.issues = validate_segment(segment, self.config)
                segment.issues.append(QAIssue(
                    "reviewed_revised",
                    f"revised by LLM reviewer: {verdict.get('notes', '')[:300]}",
                    "info",
                ))
            else:
                segment.issues.append(QAIssue(
                    "review_rejected",
                    "reviewer revision dropped placeholders; kept original",
                    "warning",
                ))
        return segment

    def review_segments(
        self, segments: list[Segment], only_flagged: bool = True
    ) -> list[Segment]:
        for segment in segments:
            if segment.kind != "translate":
                continue
            has_flags = any(i.severity in ("warning", "error") for i in segment.issues)
            if only_flagged and not has_flags:
                continue
            self.review_segment(segment)
        return segments


def _parse_review(raw: str) -> Optional[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?|\n?```$", "", raw)
    try:
        return json.loads(raw)
    except ValueError:
        match = _JSON_RE.search(raw)
        if match:
            try:
                return json.loads(match.group(0))
            except ValueError:
                return None
    return None
