"""LLM-ревью — вторая линия самоконтроля.

Ревьюер перепроверяет проблемные сегменты: JSON-вердикт
{ok: true} либо {ok: false, revised: ...}. Ревизия принимается
только если не теряет содержимое плейсхолдеров (формулы/ссылки),
иначе остаётся исходный перевод с пометкой. Сбой ревью не фатален —
пайплайн продолжает без него.
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
        # модель может вернуть revised не-строкой (null, объект) — это
        # «ревизии нет», а не повод падать
        if revised and isinstance(revised, str):
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
                # (verdict.get("notes") or ""): при {"notes": null} .get
                # возвращает None (ключ есть!) и срез падал с TypeError,
                # обрывая ревью всего документа
                notes = verdict.get("notes")
                if not isinstance(notes, str):
                    notes = ""
                segment.issues.append(QAIssue(
                    "reviewed_revised",
                    f"revised by LLM reviewer: {notes[:300]}",
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
            # Изоляция: неожиданное исключение на одном сегменте (кривой
            # JSON модели и т.п.) раньше обрывало ревью ВСЕХ оставшихся
            # сегментов — стадия падала целиком.
            try:
                self.review_segment(segment)
            except Exception as exc:  # noqa: BLE001 - ревью не критично
                logger.warning("Review crashed on segment %s: %s", segment.id, exc)
                segment.issues.append(
                    QAIssue("review_error", f"review pass crashed: {exc}", "warning")
                )
        return segments


def _parse_review(raw: str) -> Optional[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?|\n?```$", "", raw)

    def _as_dict(value) -> Optional[dict]:
        # json.loads может вернуть null/строку/список — для вердикта
        # годится только объект, остальное = «не распарсили»
        return value if isinstance(value, dict) else None

    try:
        return _as_dict(json.loads(raw))
    except ValueError:
        match = _JSON_RE.search(raw)
        if match:
            try:
                return _as_dict(json.loads(match.group(0)))
            except ValueError:
                return None
    return None
