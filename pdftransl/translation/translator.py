"""Segment building and LLM translation with self-repair loop."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.masking import Masker, unmask
from pdftransl.models import Block, QAIssue, Segment, new_id
from pdftransl.quality.validators import validate_segment
from pdftransl.translation.prompts import REPAIR_USER, build_translation_system

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, str], None]


def build_segments(
    blocks: list[Block],
    masker: Masker,
    char_budget: int = 4000,
) -> list[Segment]:
    """Group blocks into segments.

    Consecutive translatable blocks are merged until ``char_budget`` is
    reached (a block is never split); non-translatable blocks become
    pass-through segments preserving document order.
    """
    segments: list[Segment] = []
    buf: list[Block] = []
    buf_len = 0

    def flush_translate() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        source = "\n\n".join(b.text for b in buf)
        masked = masker.mask(source)
        segments.append(
            Segment(
                id=new_id("seg_"),
                kind="translate",
                source_text=source,
                block_indices=[b.index for b in buf],
                masked_text=masked.text,
                placeholders=masked.mapping,
            )
        )
        buf = []
        buf_len = 0

    for block in blocks:
        if block.translatable and block.text.strip():
            if buf and buf_len + len(block.text) > char_budget:
                flush_translate()
            buf.append(block)
            buf_len += len(block.text) + 2
        else:
            flush_translate()
            segments.append(
                Segment(
                    id=new_id("seg_"),
                    kind="pass",
                    source_text=block.text,
                    block_indices=[block.index],
                )
            )
    flush_translate()
    return segments


class Translator:
    """Translates segments with placeholder protection, validation and
    a bounded repair loop (self-control)."""

    def __init__(
        self,
        client: BaseLLMClient,
        config: PipelineConfig,
        retriever=None,   # rag.retriever.RAGContextBuilder | None
    ):
        self.client = client
        self.config = config
        self.retriever = retriever

    # -- single segment ------------------------------------------------
    def translate_segment(self, segment: Segment) -> Segment:
        if segment.kind != "translate":
            return segment

        cfg = self.config
        if self.retriever is not None:
            context = self.retriever.build(segment.source_text)
            segment.tm_examples = context.get("tm_examples", [])
            segment.glossary_hits = context.get("glossary_hits", [])
            # Exact TM hit: reuse the stored translation, skip the LLM.
            exact = context.get("exact_match")
            if exact:
                segment.translation = exact
                segment.issues.append(
                    QAIssue("tm_exact", "reused exact translation-memory match", "info")
                )
                return segment

        system = build_translation_system(
            cfg.source_lang,
            cfg.target_lang,
            glossary_terms=segment.glossary_hits or None,
            tm_examples=segment.tm_examples or None,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": segment.masked_text},
        ]

        raw = self.client.chat(
            messages, temperature=cfg.temperature, max_tokens=cfg.max_output_tokens
        )
        segment.attempts = 1
        self._finalize(segment, raw)

        # Self-repair loop: feed validation issues back to the model.
        while (
            not segment.ok and segment.attempts <= cfg.max_repair_attempts
        ):
            issues_text = "\n".join(f"- {i.message}" for i in segment.issues)
            repair = REPAIR_USER.format(
                issues=issues_text,
                source=segment.masked_text,
                translation=raw,
            )
            messages_fix = [
                {"role": "system", "content": system},
                {"role": "user", "content": repair},
            ]
            logger.info(
                "Segment %s: repair attempt %d (%s)",
                segment.id, segment.attempts,
                "; ".join(i.code for i in segment.issues),
            )
            raw = self.client.chat(
                messages_fix, temperature=cfg.temperature,
                max_tokens=cfg.max_output_tokens,
            )
            segment.attempts += 1
            self._finalize(segment, raw)

        return segment

    def _finalize(self, segment: Segment, raw_translation: str) -> None:
        """Unmask, validate and store the translation attempt."""
        raw_translation = _strip_wrapping_fence(raw_translation.strip())
        restored, missing, unknown = unmask(raw_translation, segment.placeholders)
        segment.issues = []
        if missing:
            segment.issues.append(
                QAIssue(
                    "placeholder_missing",
                    f"placeholders lost in translation: {', '.join(missing[:10])}",
                    "error",
                )
            )
        if unknown:
            segment.issues.append(
                QAIssue(
                    "placeholder_unknown",
                    f"invented placeholder tokens: {', '.join(unknown[:10])}",
                    "error",
                )
            )
        segment.translation = restored
        segment.issues.extend(validate_segment(segment, self.config))

    # -- whole document --------------------------------------------------
    def translate_segments(
        self,
        segments: list[Segment],
        progress: Optional[ProgressCb] = None,
    ) -> list[Segment]:
        total = sum(1 for s in segments if s.kind == "translate")
        done = 0
        for segment in segments:
            if segment.kind != "translate":
                continue
            self.translate_segment(segment)
            done += 1
            if progress:
                progress(done, total, segment.id)
        return segments


def _strip_wrapping_fence(text: str) -> str:
    """Models sometimes wrap the whole answer in a ``` fence — remove it."""
    if text.startswith("```") and text.endswith("```"):
        body = text[3:-3]
        # drop optional language tag on the first line
        first_nl = body.find("\n")
        if first_nl != -1 and " " not in body[:first_nl].strip():
            body = body[first_nl + 1:]
        return body.strip()
    return text
