"""Segment building and LLM translation with self-repair loop.

Segments are independent by construction (placeholder ids are
document-unique, context comes from the *source* side), so they are
translated in parallel with a thread pool when ``max_workers > 1``.
"""

from __future__ import annotations

import logging
import re
import time as _time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.masking import Masker, unmask
from pdftransl.models import Block, QAIssue, Segment, new_id
from pdftransl.quality.validators import validate_segment
from pdftransl.translation.prompts import (
    REPAIR_USER,
    build_translation_system,
    build_user_message,
)

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
        checkpoint=None,  # translation.checkpoint.Checkpoint | None
    ):
        self.client = client
        self.config = config
        self.retriever = retriever
        self.checkpoint = checkpoint
        # Document-level context, set once per document by the pipeline.
        self.doc_summary: str = ""
        self.doc_terms: list[dict[str, str]] = []

    # -- single segment ------------------------------------------------
    def translate_segment(
        self, segment: Segment, source_context: str = ""
    ) -> Segment:
        if segment.kind != "translate":
            return segment

        started = _time.monotonic()
        cfg = self.config

        # Resume: a previous run of this document already finished this
        # segment — reuse it without touching the LLM.
        if self.checkpoint is not None:
            done = self.checkpoint.get(segment.source_text)
            if done is not None:
                segment.translation = done
                segment.issues.append(
                    QAIssue("resumed", "reused from checkpoint (resumed job)", "info")
                )
                logger.debug("segment %s: resumed from checkpoint", segment.id)
                return segment

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
                logger.debug("segment %s: exact TM hit (%d chars)",
                             segment.id, len(segment.source_text))
                return segment

        # ИСПРАВЛЕНИЕ: Точный поиск терминов с использованием регулярных выражений (\b)
        doc_hits = []
        for t in self.doc_terms:
            pattern = r'\b' + re.escape(t["term"]) + r'\b'
            if re.search(pattern, segment.source_text, re.IGNORECASE):
                doc_hits.append(t)
                
        glossary_terms = doc_hits + (segment.glossary_hits or [])
        system = build_translation_system(
            cfg.source_lang,
            cfg.target_lang,
            glossary_terms=glossary_terms or None,
            tm_examples=segment.tm_examples or None,
            doc_summary=self.doc_summary or None,
        )
        user = build_user_message(segment.masked_text, source_context or None)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
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
            # ИСПРАВЛЕНИЕ: Backoff - экспоненциальная задержка перед повторной попыткой
            sleep_time = 2 ** (segment.attempts - 1)
            logger.info(
                "Segment %s: repair attempt %d sleeping for %ds... (%s)", 
                segment.id, segment.attempts, sleep_time,
                "; ".join(i.code for i in segment.issues)
            )
            _time.sleep(sleep_time)

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
            raw = self.client.chat(
                messages_fix, temperature=cfg.temperature,
                max_tokens=cfg.max_output_tokens,
            )
            segment.attempts += 1
            self._finalize(segment, raw)

        logger.debug(
            "segment %s: %d chars, %d placeholders, %d attempt(s), "
            "%d issue(s), %.1fs%s",
            segment.id, len(segment.source_text), len(segment.placeholders),
            segment.attempts, len(segment.issues),
            _time.monotonic() - started, "" if segment.ok else " [FAILED]",
        )
        # Checkpoint only clean segments so a resumed run re-tries the
        # bad ones instead of caching garbage.
        if self.checkpoint is not None and segment.ok and segment.translation:
            self.checkpoint.put(segment.source_text, segment.translation)
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
        should_pause: Optional[Callable[[], bool]] = None,
        on_batch: Optional[Callable[[int, int], None]] = None,
    ) -> tuple[list[Segment], bool]:
        """Translate every "translate"-kind segment in place.

        Returns ``(segments, paused)``. ``should_pause`` is polled between
        segments (sequential mode) or after each completion (parallel
        mode); once it returns True, no *new* segment translation is
        started — in-flight ones are allowed to finish so their work
        isn't wasted — and the method returns with ``paused=True``. The
        untouched segments stay ``translation=None`` and get picked up by
        the resume checkpoint on the next run.

        Segments are processed in batches of ``translate_batch_size``
        (parallel mode gets a fresh, bounded ``ThreadPoolExecutor`` per
        batch instead of one pool spanning the whole document) so a huge
        document doesn't hold thousands of in-flight requests/threads at
        once. ``on_batch(done, total)`` fires after every batch — the
        pipeline uses it to write the partial document to disk and
        recheck free memory before continuing.
        """
        # Source-side context (parallel-safe): the tail of the previous
        # segment's source text smooths chunk-boundary seams.
        ctx_chars = self.config.source_context_chars
        contexts: dict[str, str] = {}
        prev_source = ""
        for segment in segments:
            if segment.kind == "translate" and ctx_chars > 0 and prev_source:
                contexts[segment.id] = prev_source[-ctx_chars:]
            if segment.source_text.strip():
                prev_source = segment.source_text
        to_translate = [s for s in segments if s.kind == "translate"]
        total = len(to_translate)
        done = 0
        batch_size = self.config.translate_batch_size or total or 1

        workers = max(1, self.config.max_workers)
        if workers == 1 or total <= 1:
            for idx, segment in enumerate(to_translate):
                if should_pause and should_pause():
                    for pending in to_translate[idx:]:
                        pending.issues.append(_paused_issue())
                    logger.info(
                        "translation paused: %d/%d segment(s) done", done, total
                    )
                    if on_batch:
                        on_batch(done, total)
                    return segments, True
                # A single segment blowing up (provider outage) must not sink
                # the whole document — flag it and keep going, same as the
                # parallel path, so finished segments still get checkpointed.
                try:
                    self.translate_segment(segment, contexts.get(segment.id, ""))
                except Exception as exc:
                    logger.error("Segment %s failed: %s", segment.id, exc)
                    segment.issues.append(
                        QAIssue("exception", f"translation call failed: {exc}", "error")
                    )
                done += 1
                if progress:
                    progress(done, total, segment.id)
                if on_batch and done % batch_size == 0:
                    on_batch(done, total)
            if on_batch and done % batch_size != 0:
                on_batch(done, total)
            return segments, False

        paused = False
        for batch_start in range(0, total, batch_size):
            batch = to_translate[batch_start:batch_start + batch_size]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        self.translate_segment, seg, contexts.get(seg.id, "")
                    ): seg
                    for seg in batch
                }
                for future in as_completed(futures):
                    segment = futures[future]
                    try:
                        future.result()
                    except CancelledError:
                        # paused before this one started; leave it for resume
                        segment.issues.append(_paused_issue())
                    except Exception as exc:
                        logger.error("Segment %s failed: %s", segment.id, exc)
                        segment.issues.append(
                            QAIssue("exception", f"translation call failed: {exc}", "error")
                        )
                    done += 1
                    if progress:
                        progress(done, total, segment.id)
                    if not paused and should_pause and should_pause():
                        paused = True
                        cancelled = sum(1 for f in futures if f.cancel())
                        logger.info(
                            "translation paused: %d/%d done, %d not-yet-started "
                            "segment(s) cancelled, waiting for in-flight to finish",
                            done, total, cancelled,
                        )
            # a fresh pool next batch releases this batch's threads now,
            # rather than holding one pool open for the whole document
            if on_batch:
                on_batch(done, total)
            if paused:
                # later batches were never even submitted — tag them too
                for pending in to_translate[batch_start + len(batch):]:
                    if pending.translation is None:
                        pending.issues.append(_paused_issue())
                break
        return segments, paused


def _paused_issue() -> QAIssue:
    """Marks a segment left untranslated by a pause request — a warning,
    not an error: it wasn't attempted, so it didn't fail. Resuming the
    job re-tries it via the checkpoint."""
    return QAIssue(
        "paused", "translation paused before this segment was reached; "
        "resume the job to continue", "warning",
    )


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