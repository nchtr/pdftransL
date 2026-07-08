"""Оркестрация полного цикла перевода.

    PDF -> парсинг (кэш; MinerU/OCR/PyMuPDF) -> блоки
        -> пометка References -> саммари + авто-глоссарий
        -> маскировка формул -> RAG-контекст -> параллельный перевод
        -> валидаторы -> цикл исправлений -> LLM-ревью -> бэк-перевод
        -> проверка LaTeX -> сборка markdown (опц. двуязычная)
        -> экспорт HTML/DOCX/PDF -> ассеты и отчёт -> обучение TM

Плюс: memory guard между парсером и моделью (анти-OOM), дискретная
запись результата после каждой партии, кооперативная пауза,
деградация необязательных стадий по одной (сбой ревью не стирает
готовый перевод).
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from pdftransl.config import PipelineConfig, get_provider_config
from pdftransl.export.exporter import export_document
from pdftransl.llm.base import BaseLLMClient
from pdftransl.llm.fallback import FallbackClient
from pdftransl.llm.registry import create_client
from pdftransl.masking import Masker
from pdftransl.models import JobResult, ParsedDocument, new_id
from pdftransl.parsing.base import get_backend
from pdftransl.parsing.cache import ParseCache
from pdftransl.parsing.splitter import assemble, mark_references, split_markdown
from pdftransl.progress import StageTracker, build_stage_plan
from pdftransl.quality.backtranslation import check_segments as backtranslation_check
from pdftransl.quality.latex_check import check_document as latex_check
from pdftransl.quality.reviewer import Reviewer
from pdftransl.quality.validators import document_report
from pdftransl.rag.embeddings import get_embedder
from pdftransl.rag.glossary import Glossary
from pdftransl.rag.retriever import RAGContextBuilder
from pdftransl.rag.store import TranslationMemory
from pdftransl.translation.doc_context import build_doc_summary, extract_terms
from pdftransl.translation.figures import describe_figures
from pdftransl.translation.translator import Translator, build_segments

logger = logging.getLogger(__name__)

StageCb = Callable[[str, float], None]  # (stage_name, progress 0..1)

# Backends that already handle scanned pages (OCR); others get scan detection.
_OCR_BACKENDS = {"mineru_local", "mineru_api", "vlm_ocr"}


def _is_garbage_markdown(md_text: str) -> bool:
    """Не выдал ли парсер откровенный мусор (кракозябры/заполнители).

    Пост-парсинговая страховка: даже если детектор сканов пропустил
    документ, мусорный результат бэкенда отбрасывается и срабатывает
    фолбэк на следующий (в идеале — VLM-OCR). Переиспользует калиброванный
    детектор из ``parsing.text_quality`` (PUA-глифы, mojibake, доля
    осмысленных символов, минимальная длина): первоначальная встроенная
    эвристика с порогом «<40% букв» ложно браковала страницы, насыщенные
    формулами/таблицами, а пустая альтернатива ``||`` в её regex
    совпадала в каждой позиции — под неё «мусором» был любой текст.
    Короткие документы (<200 значимых символов) не бракуем: однострочный
    PDF — это валидный результат, а не мусор.
    """
    from pdftransl.parsing.text_quality import is_garbled

    if not md_text or not md_text.strip():
        return True
    return is_garbled(md_text)


def _build_client(config: PipelineConfig) -> BaseLLMClient:
    # shared throttles: the whole chain respects one rpm budget and one
    # 429 cooldown — a rate-limited provider pauses every worker at once
    rate_limiter = None
    if config.rpm_limit:
        from pdftransl.llm.ratelimit import RateLimiter

        rate_limiter = RateLimiter(config.rpm_limit)
    cooldown_gate = None
    if config.adaptive_throttle:
        from pdftransl.llm.ratelimit import CooldownGate

        cooldown_gate = CooldownGate()
    primary = create_client(
        config.provider_config(),
        rate_limiter=rate_limiter, cooldown_gate=cooldown_gate,
    )
    if not config.fallback_providers:
        return primary
    chain = [primary]
    for name in config.fallback_providers:
        if name == config.provider:
            continue
        try:
            chain.append(create_client(
                get_provider_config(name),
                rate_limiter=rate_limiter, cooldown_gate=cooldown_gate,
            ))
        except Exception as exc:
            logger.warning("Fallback provider %s unavailable: %s", name, exc)
    return FallbackClient(chain) if len(chain) > 1 else primary


class TranslationPipeline:
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        client: Optional[BaseLLMClient] = None,
        tm: Optional[TranslationMemory] = None,
        glossary: Optional[Glossary] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.client = client or _build_client(self.config)
        self.embedder = get_embedder(self.config)
        if self.config.use_rag:
            self.tm = tm or TranslationMemory(self.config.db_path, self.embedder)
            self.glossary = glossary or Glossary(self.config.db_path)
            self.retriever = RAGContextBuilder(self.config, self.tm, self.glossary)
        else:
            self.tm = tm
            self.glossary = glossary
            self.retriever = None
        self.translator = Translator(self.client, self.config, self.retriever)
        self.reviewer = Reviewer(self.client, self.config) if self.config.review else None
        self._vision_client_cached: Optional[BaseLLMClient] = None
        self._vision_client_built = False
        self._memory_warning: Optional[str] = None
        self._stall_warning: Optional[str] = None # ИСПРАВЛЕНИЕ: отдельная переменная для стагнации сети

    # ------------------------------------------------------------------
    def run(
        self,
        pdf_path: str | Path,
        output_dir: Optional[str | Path] = None,
        job_id: Optional[str] = None,
        on_stage: Optional[StageCb] = None,
        should_pause: Optional[Callable[[], bool]] = None,
    ) -> JobResult:
        """Full run: PDF in; translated markdown/HTML/DOCX/PDF out.

        ``should_pause``, if given, is polled during translation; once it
        returns True the run stops cooperatively (in-flight segments
        finish, no new ones start) and returns a ``JobResult(status="paused")``
        with whatever was translated so far already written to disk. A
        later call with ``resume`` enabled in the config picks up from the
        per-document checkpoint instead of re-translating everything.

        Progress reported via ``on_stage`` is a precise 0..1 number
        computed from a stage plan built for *this* config (see
        ``pdftransl.progress``) — stages this job won't run (review,
        back-translation, export...) contribute nothing to the bar,
        instead of a one-size-fits-all fixed split.
        """
        job_id = job_id or new_id("job_")
        pdf_path = Path(pdf_path)
        out_dir = Path(output_dir or self.config.output_dir) / pdf_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()

        plan = build_stage_plan(self.config)

        def _forward(name: str, progress: float) -> None:
            logger.info("[%s] stage=%s progress=%.0f%%", job_id, name, progress * 100)
            if on_stage:
                on_stage(name, progress)

        tracker = StageTracker(plan, _forward)

        def stage(name: str, fraction: float = 0.0) -> None:
            tracker.enter(name, fraction)

        try:
            self._log_memory("before parse", job_id)
            stage("parse", 0.0)
            parsed = self._parse(pdf_path, out_dir / "parse", tracker)
            source_md_path = out_dir / f"{pdf_path.stem}.md"
            source_md_path.write_text(parsed.markdown, encoding="utf-8")

            # The OOM fix: a heavy parser (MinerU/Nougat runs as a subprocess
            # and holds gigabytes) must have released its memory before we
            # load the translation model — otherwise both coexist and the
            # machine OOMs. Wait for RAM to free, then proceed.
            self._memory_guard(job_id, parsed.backend)

            result = self._translate_parsed(
                parsed, out_dir, job_id, stage, tracker,
                output_name=f"{pdf_path.stem}.{self.config.target_lang}.md",
                should_pause=should_pause,
            )
            result.source_markdown_path = str(source_md_path)
            result.report["duration_sec"] = round(time.time() - started, 1)
            result.report["parser_backend"] = parsed.backend
            result.report["stage_plan"] = [s.to_dict() for s in plan]
            if parsed.meta.get("cache"):
                result.report["parse_cache"] = "hit"
            if self._memory_warning:
                result.report["memory_warning"] = self._memory_warning
            if self._stall_warning: # ИСПРАВЛЕНИЕ: Выводим сетевую ошибку раздельно
                result.report["stall_warning"] = self._stall_warning
                
            report_path = out_dir / "report.json"
            result.report_path = str(report_path)
            result.save_report(report_path)
            return result
        except Exception as exc:
            logger.exception("[%s] pipeline failed", job_id)
            return JobResult(job_id=job_id, status="failed", error=str(exc))

    # -- resource guards -----------------------------------------------
    def _log_memory(self, label: str, job_id: str) -> None:
        if not self.config.memory_guard:
            return
        from pdftransl.resources import memory_stats

        stats = memory_stats()
        if stats is not None:
            logger.info("[%s] memory %s: %.0f MB free / %.0f MB (%.0f%% used)",
                        job_id, label, stats.available_mb, stats.total_mb,
                        stats.used_pct)
            if stats.available_mb < 500:
                self._memory_warning = (
                    f"Very low memory ({stats.available_mb:.0f} MB free) {label}; "
                    "the machine may OOM. Close other apps or use a lighter "
                    "parser/model."
                )

    def _memory_guard(self, job_id: str, backend_name: str) -> None:
        """Between a heavy parser and loading the translation model, wait
        for the parser's RAM to be reclaimed (prevents the MinerU + Ollama
        OOM). Only relevant for local, memory-hungry backends."""
        if not self.config.memory_guard:
            return
        from pdftransl.resources import wait_for_memory

        heavy = backend_name in ("mineru_local", "nougat", "marker", "docling")
        floor = self.config.min_free_memory_mb if heavy else 0
        stats = wait_for_memory(
            floor, self.config.memory_wait_timeout, label=job_id,
        )
        if stats is not None:
            logger.info("[%s] memory before translate: %.0f MB free",
                        job_id, stats.available_mb)
            if floor and stats.available_mb < floor:
                self._memory_warning = (
                    f"Only {stats.available_mb:.0f} MB free before loading the "
                    f"model (wanted {floor} MB). Loading the translation model "
                    "now risks an out-of-memory crash — consider a smaller model, "
                    "the cloud, or a bigger machine."
                )

    def translate_markdown(
        self,
        markdown: str,
        output_path: str | Path,
        job_id: Optional[str] = None,
        on_stage: Optional[StageCb] = None,
        assets_dir: Optional[str | Path] = None,
        should_pause: Optional[Callable[[], bool]] = None,
    ) -> JobResult:
        """Translate an already-parsed markdown document (skip parsing)."""
        job_id = job_id or new_id("job_")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        parsed = ParsedDocument(source_path="", markdown=markdown, backend="markdown")

        plan = build_stage_plan(self.config)
        tracker = StageTracker(plan, on_stage)

        def stage(name: str, fraction: float = 0.0) -> None:
            tracker.enter(name, fraction)

        return self._translate_parsed(
            parsed, output_path.parent, job_id, stage, tracker, output_name=output_path.name,
            should_pause=should_pause,
        )

    # ------------------------------------------------------------------
    def _parse(
        self, pdf_path: Path, workdir: Path, tracker: Optional[StageTracker] = None,
    ) -> ParsedDocument:
        from pdftransl.exceptions import ParserError
        from pdftransl.parsing.base import fallback_backends
        from pdftransl.parsing.scan_detect import scan_stats
        from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

        primary = get_backend(self.config)
        scan: dict = {}

        # A text extractor fails on two kinds of PDF: scanned (no text
        # layer) and garbled (broken font encoding -> "кракозябры"). Both
        # need OCR. Detect and route when a vision model is available.
        if self.config.ocr_on_scan and primary.name not in _OCR_BACKENDS:
            scan = scan_stats(pdf_path)
            if scan.get("needs_ocr"):
                reason = "scanned" if scan.get("is_scanned") else "garbled text layer"
                vclient = self._vision_client()
                if vclient is not None and getattr(vclient, "supports_vision", False):
                    logger.info(
                        "PDF needs OCR (%s, garbled_ratio=%.2f); routing to VLM OCR",
                        reason, scan.get("garbled_ratio", 0),
                    )
                    primary = VlmOcrBackend(self.config, client=vclient)
                else:
                    logger.warning(
                        "PDF needs OCR (%s) but no vision provider is available — "
                        "output will be unreliable.", reason,
                    )

        # Build the attempt order: primary first, then — if enabled — the
        # other available backends so one backend failing (e.g. MinerU
        # timing out on a large file) doesn't sink the whole job.
        attempts: list = [primary]
        if self.config.parser_fallback:
            fbs = fallback_backends(self.config, exclude=primary.name)
            vclient = self._vision_client()
            if (
                getattr(vclient, "supports_vision", False)
                and primary.name != "vlm_ocr"
            ):
                # VLM OCR beats PyMuPDF on scans/broken PDFs — try it first
                ocr = VlmOcrBackend(self.config, client=vclient)
                non_pdf = [b for b in fbs if b.name != "pymupdf"]
                pdf_only = [b for b in fbs if b.name == "pymupdf"]
                fbs = non_pdf + [ocr] + pdf_only
            attempts += fbs

        cache = (
            ParseCache(self.config.output_dir) if self.config.parse_cache else None
        )
        tried: set[str] = set()
        errors: list[str] = []

        # Rough visual progress during a long subprocess-based parse (MinerU
        # etc. give no real completion %): a background tick nudges the
        # "parse" stage forward based on elapsed time vs. the configured
        # timeout, capped short of 100% so it never claims to be done before
        # it actually is. Purely cosmetic — never affects the real result.
        stop_ticker = threading.Event()

        def _tick() -> None:
            started = time.monotonic()
            timeout = self.config.parser_timeout or 1800
            while not stop_ticker.wait(2.0):
                elapsed = time.monotonic() - started
                tracker.enter("parse", min(0.92, elapsed / timeout))

        ticker = None
        if tracker is not None:
            ticker = threading.Thread(target=_tick, daemon=True, name="parse-ticker")
            ticker.start()

        # Лучший из «мусорных» результатов: если ни один бэкенд не дал
        # чистого текста (типично: битый PDF без vision-модели), честнее
        # вернуть его с предупреждением в отчёте, чем провалить всю задачу.
        garbage_fallback = None
        garbage_backend = None
        try:
            for i, backend in enumerate(attempts):
                if backend.name in tried:
                    continue
                tried.add(backend.name)
                if cache is not None:
                    cached = cache.get(pdf_path, backend.name)
                    if cached is not None:
                        return self._annotate_parse(cached, scan, backend, primary)
                logger.info("Parsing %s with backend '%s'%s", pdf_path.name, backend.name,
                            " (fallback)" if i else "")
                try:
                    parsed = backend.parse(pdf_path, workdir / backend.name)

                    # Пост-парсинговая проверка: мусорный Markdown (кракозябры)
                    # отбрасываем и форсируем фолбэк на следующий бэкенд —
                    # в идеале до VLM-OCR, который прочитает страницу заново.
                    if backend.name != "vlm_ocr" and _is_garbage_markdown(parsed.markdown):
                        logger.warning(
                            "Backend '%s' extracted garbage text; forcing fallback.",
                            backend.name,
                        )
                        if garbage_fallback is None:
                            garbage_fallback = parsed
                            garbage_backend = backend
                        raise ParserError(
                            "Extracted markdown failed heuristic quality check "
                            "(garbage text)."
                        )

                except ParserError as exc:
                    errors.append(f"{backend.name}: {exc}")
                    logger.warning("Backend '%s' failed: %s", backend.name, exc)
                    continue
                parsed = self._annotate_parse(parsed, scan, backend, primary, errors)
                if cache is not None:
                    try:
                        cache.put(pdf_path, parsed)
                    except OSError as exc:
                        logger.warning("Parse cache write failed: %s", exc)
                return parsed

            if garbage_fallback is not None:
                # вся цепочка дала только мусор — отдаём его с предупреждением
                # (scan_warning уже помечает документ как ненадёжный), кэш
                # НЕ пишем: следующая попытка с vision-моделью должна парсить
                # заново, а не переиспользовать мусор
                logger.warning(
                    "All backends produced garbage text; returning the '%s' "
                    "result with a warning instead of failing the job",
                    garbage_backend.name,
                )
                return self._annotate_parse(
                    garbage_fallback, scan, garbage_backend, primary, errors
                )

            raise ParserError(
                "All parsing backends failed:\n  " + "\n  ".join(errors)
            )
        finally:
            stop_ticker.set()
            if ticker is not None:
                ticker.join(timeout=1.0)

    def _annotate_parse(self, parsed, scan, backend, primary, errors=None):
        """Attach scan / fallback warnings to a parsed document."""
        if scan:
            parsed.meta.setdefault("scan", scan)
            if scan.get("needs_ocr") and backend.name not in _OCR_BACKENDS:
                if scan.get("is_scanned"):
                    detail = "appears to be scanned (no text layer)"
                else:
                    gr = scan.get("garbled_ratio", 0)
                    pct = f" ({gr:.0%} unreadable glyphs)" if gr >= 0.05 else ""
                    detail = (
                        f"has a garbled text layer{pct} — the embedded fonts "
                        "likely lack a Unicode map"
                    )
                parsed.meta["scan_warning"] = (
                    f"PDF {detail}; extracted text is unreliable and the "
                    "translation will be garbage. Enable OCR: use a multimodal "
                    "model, install MinerU, or pass --backend vlm_ocr."
                )
        if backend.name != primary.name:
            parsed.meta["parser_fallback"] = (
                f"Primary parser '{primary.name}' failed; used '{backend.name}' "
                "instead." + (f" ({errors[-1][:200]})" if errors else "")
            )
        return parsed

    def _translate_parsed(
        self,
        parsed: ParsedDocument,
        out_dir: Path,
        job_id: str,
        stage: StageCb,
        tracker: Optional[StageTracker] = None,
        *,
        output_name: str,
        should_pause: Optional[Callable[[], bool]] = None,
    ) -> JobResult:
        cfg = self.config

        # Sanity check: does the source text match the declared source
        # language? Catches the common "forgot to flip RU<->EN" mistake.
        from pdftransl.parsing.text_quality import language_mismatch

        wrong_script = language_mismatch(parsed.markdown, cfg.source_lang)

        # 1. structural split; keep bibliography untranslated
        stage("split", 0.0)
        blocks = split_markdown(parsed.markdown)
        refs_skipped = mark_references(blocks) if cfg.skip_references else 0
        segments = build_segments(blocks, Masker(), cfg.chunk_char_budget)
        logger.info(
            "[%s] %d blocks -> %d segments (%d to translate, %d reference blocks kept)",
            job_id, len(blocks), len(segments),
            sum(1 for s in segments if s.kind == "translate"), refs_skipped,
        )

        # 2. document-level context: summary + auto-extracted glossary
        if cfg.doc_summary:
            stage("context", 0.0)
            self.translator.doc_summary = build_doc_summary(
                parsed.markdown, self.client, cfg
            )
        if cfg.auto_glossary:
            # halfway through "context" if the summary above also ran
            stage("context", 0.5 if cfg.doc_summary else 0.0)
            self.translator.doc_terms = extract_terms(
                parsed.markdown, self.client, cfg
            )
            logger.info("[%s] auto-glossary: %d terms",
                        job_id, len(self.translator.doc_terms))

        # 3. parallel translation with validation + repair loop.
        # A per-document checkpoint lets a re-run resume finished segments.
        checkpoint = None
        if cfg.resume:
            from pdftransl.translation.checkpoint import Checkpoint

            checkpoint = Checkpoint(
                out_dir / ".checkpoint.jsonl", cfg.source_lang, cfg.target_lang
            )
            if checkpoint.count:
                logger.info("[%s] resuming: %d segment(s) already done",
                            job_id, checkpoint.count)
        self.translator.checkpoint = checkpoint

        # Watchdog: warn if translation makes no progress (a hung/unresponsive
        # LLM) instead of silently waiting forever.
        from pdftransl.resources import Watchdog

        def on_stall(idle: float) -> None:
            logger.warning(
                "[%s] translation stalled: no segment finished for %.0fs — the "
                "LLM may be unresponsive (model loading, overloaded, or hung)",
                job_id, idle,
            )
            # ИСПРАВЛЕНИЕ: Используем выделенный флаг вместо флага памяти
            self._stall_warning = (
                f"Translation stalled for {idle:.0f}s — the model provider seems "
                "unresponsive (loading a large model, out of memory, or hung)."
            )

        watchdog = Watchdog(cfg.stall_warning_seconds, on_stall)

        def progress(done: int, total: int, _seg_id: str) -> None:
            watchdog.beat()
            stage("translate", done / max(total, 1))

        # Discrete write, done incrementally: assemble and save whatever is
        # translated so far to disk after every batch, not just once at the
        # very end. A hiccup in a later enrichment stage (or the process
        # dying mid-translation on a very large document) then loses at
        # most one batch's worth of work instead of everything.
        output_path = out_dir / output_name

        # ИСПРАВЛЕНИЕ: Безопасная атомарная запись файлов
        def write_partial() -> None:
            translated_md = assemble([s.final_text() for s in segments])
            tmp_path = output_path.with_suffix(".tmp")
            tmp_path.write_text(translated_md, encoding="utf-8")
            tmp_path.replace(output_path) # Атомарно!

        def on_batch(done: int, total: int) -> None:
            write_partial()
            # The OOM guard used to run once, between parse and translate.
            # A big document can also build up memory pressure *during* a
            # long translate stage (client buffers, GC lag, another process
            # competing for RAM) — recheck between batches too.
            if cfg.memory_guard and cfg.min_free_memory_mb:
                from pdftransl.resources import wait_for_memory

                stats = wait_for_memory(
                    cfg.min_free_memory_mb, cfg.memory_wait_timeout, label=job_id,
                )
                if stats is not None and stats.available_mb < cfg.min_free_memory_mb:
                    self._memory_warning = self._memory_warning or (
                        f"Low memory during translation ({stats.available_mb:.0f} MB "
                        f"free, {done}/{total} segments done); consider a smaller "
                        "translate_batch_size, fewer workers, or a smaller model."
                    )

        with watchdog:
            segments, paused = self.translator.translate_segments(
                segments, progress=progress, should_pause=should_pause,
                on_batch=on_batch,
            )

        write_partial()

        if paused:
            pending = sum(
                1 for s in segments if s.kind == "translate" and s.translation is None
            )
            report = document_report(segments)
            report["assets"] = [a.to_dict() for a in parsed.assets]
            report["references_blocks_kept"] = refs_skipped
            report["paused"] = True
            report["segments_pending"] = pending
            report["segments_done"] = report["segments_translated"] - pending
            # Freeze at wherever translation actually got to instead of
            # jumping ahead to "assemble"'s slice — the document may be far
            # from fully translated when a pause lands.
            if tracker is not None:
                tracker.freeze("paused")
            return JobResult(
                job_id=job_id,
                status="paused",
                output_markdown_path=str(output_path),
                report=report,
                segments=[s.to_dict() for s in segments],
            )

        # translation genuinely finished (not a pause) — the "assemble"
        # slice of the bar is done, closing out translate's range too.
        stage("assemble", 1.0)

        # Everything from here on is best-effort enrichment: if a stage
        # stalls or raises, log it, record it in the report and move on —
        # the already-written translation above must not be lost because
        # e.g. the reviewer's LLM call timed out.
        stage_errors: dict[str, str] = {}

        # 4. optional LLM-judge quality scoring (flags weak segments)
        quality_scores: dict = {}
        if cfg.quality_score:
            stage("scoring", 0.0)
            try:
                from pdftransl.quality.scoring import score_segments

                quality_scores = score_segments(segments, self.client, cfg)
            except Exception as exc:
                logger.warning("[%s] scoring stage failed, skipping: %s", job_id, exc)
                stage_errors["scoring"] = str(exc)

        # 5. LLM review of flagged segments
        if self.reviewer is not None:
            stage("review", 0.0)
            try:
                self.reviewer.review_segments(segments, only_flagged=True)
            except Exception as exc:
                logger.warning(
                    "[%s] review stage failed, keeping unreviewed translation: %s",
                    job_id, exc,
                )
                stage_errors["review"] = str(exc)

        # 6. optional back-translation semantic check
        if cfg.backtranslation_check:
            stage("backtranslation", 0.0)
            try:
                backtranslation_check(segments, self.client, self.embedder, cfg)
            except Exception as exc:
                logger.warning("[%s] backtranslation check failed, skipping: %s",
                                job_id, exc)
                stage_errors["backtranslation"] = str(exc)

        # 7. re-assemble (review may have revised segments) + LaTeX repair,
        # overwriting the raw translation written above with the polished one.
        translated_md = assemble([s.final_text() for s in segments])
        latex_issues = latex_check(translated_md)
        latex_fixes: list = []
        if cfg.fix_latex and latex_issues:
            stage("latex_fix", 0.0)
            try:
                from pdftransl.quality.latex_fix import fix_document

                translated_md, latex_fixes = fix_document(translated_md, self.client, cfg)
                latex_issues = latex_check(translated_md)
            except Exception as exc:
                logger.warning("[%s] LaTeX repair failed, keeping unfixed formulas: %s",
                                job_id, exc)
                stage_errors["latex_fix"] = str(exc)

        output_path.write_text(translated_md, encoding="utf-8")
        bilingual_path: Optional[Path] = None
        if cfg.bilingual:
            bilingual_md = assemble(_bilingual_texts(segments))
            bilingual_path = output_path.with_suffix(".bilingual.md")
            bilingual_path.write_text(bilingual_md, encoding="utf-8")

        # 8. export assets next to the translated markdown
        assets_dir: Optional[Path] = None
        if parsed.assets:
            assets_dir = out_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            for asset in parsed.assets:
                src = Path(asset.path)
                if src.exists():
                    dst = (assets_dir / (asset.rel_path or src.name)).resolve()
                    
                    # ИСПРАВЛЕНИЕ: Защита ZipSlip (Path Traversal) при работе с путями из парсера
                    if not dst.is_relative_to(assets_dir.resolve()):
                        logger.warning("[%s] prevented path traversal for asset %s", job_id, asset.rel_path)
                        continue
                        
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.resolve() != dst.resolve():
                        try:
                            shutil.copy2(src, dst)
                        except OSError as exc:
                            logger.warning("[%s] could not copy asset %s: %s",
                                            job_id, src.name, exc)

        # 9. optional VLM figure descriptions
        figure_descriptions: dict = {}
        if cfg.describe_figures and parsed.assets:
            stage("figures", 0.0)
            try:
                vision_client = self._vision_client()
                if vision_client is not None:
                    figure_descriptions = describe_figures(
                        parsed.assets, vision_client, cfg,
                        output_json=out_dir / "figures.json",
                    )
                else:
                    logger.warning(
                        "describe_figures requested but no vision provider available"
                    )
            except Exception as exc:
                logger.warning("[%s] figure description failed, skipping: %s",
                                job_id, exc)
                stage_errors["figures"] = str(exc)

        # 10. export to HTML / LaTeX / DOCX / PDF
        export_result = {"files": {}, "engines": {}}
        if cfg.export_formats:
            stage("export", 0.0)
            try:
                title = _first_heading(translated_md) or output_path.stem
                export_result = export_document(
                    translated_md,
                    out_base=output_path.with_suffix(""),
                    formats=cfg.export_formats,
                    assets_dir=assets_dir,
                    title=title,
                )
            except Exception as exc:
                logger.warning(
                    "[%s] export failed; the translated Markdown is still "
                    "available: %s", job_id, exc,
                )
                stage_errors["export"] = str(exc)

        # 10b. optional render check of the exported HTML (KaTeX errors)
        render_issues: list = []
        if cfg.render_check and export_result["files"].get("html"):
            stage("render_check", 0.0)
            try:
                from pdftransl.quality.render_check import check_rendered_html

                render_issues = check_rendered_html(export_result["files"]["html"])
            except Exception as exc:
                logger.warning("[%s] render check failed, skipping: %s", job_id, exc)
                stage_errors["render_check"] = str(exc)

        # 11. learn: push good pairs into the translation memory. Never
        # learn from a garbled/scanned source that wasn't OCR'd — those
        # "translations" are noise and would poison future exact-match
        # reuse (the "retry gives the same garbage" trap).
        if cfg.learn and self.tm is not None and not parsed.meta.get("scan_warning"):
            stage("learn", 0.0)
            try:
                learned = 0
                for segment in segments:
                    if (
                        segment.kind == "translate"
                        and segment.translation
                        and segment.ok
                        and not any(i.code == "tm_exact" for i in segment.issues)
                    ):
                        self.tm.add(
                            segment.source_text, segment.translation,
                            cfg.source_lang, cfg.target_lang,
                            origin="auto", doc_id=job_id,
                            domain=cfg.tm_domain or "",
                        )
                        learned += 1
                logger.info("[%s] stored %d segments into translation memory",
                            job_id, learned)

                # auto-export a fine-tuning dataset each time the TM crosses
                # a new threshold (docs/FINETUNING.md)
                if cfg.tm_autoexport_every > 0:
                    path = cfg.tm_autoexport_path or str(
                        Path(cfg.db_path).with_name("tm_dataset.jsonl")
                    )
                    self.tm.maybe_autoexport(cfg.tm_autoexport_every, path)
            except Exception as exc:
                logger.warning("[%s] learn stage failed, skipping: %s", job_id, exc)
                stage_errors["learn"] = str(exc)

        # 12. report
        report = document_report(segments)
        report["assets"] = [a.to_dict() for a in parsed.assets]
        report["references_blocks_kept"] = refs_skipped
        report["latex_issues"] = [i.to_dict() for i in latex_issues]
        if parsed.meta.get("scan"):
            report["scan"] = parsed.meta["scan"]
        if parsed.meta.get("scan_warning"):
            report["scan_warning"] = parsed.meta["scan_warning"]
        if parsed.meta.get("parser_fallback"):
            report["parser_fallback"] = parsed.meta["parser_fallback"]
        if wrong_script:
            report["language_warning"] = (
                f"The document looks like {wrong_script} text, but the source "
                f"language is set to '{cfg.source_lang}'. Check the translation "
                "direction (source/target languages)."
            )
        if parsed.meta.get("ocr"):
            report["ocr"] = {"pages_transcribed": parsed.meta.get("pages_transcribed")}
        if figure_descriptions:
            report["figures_described"] = len(figure_descriptions)
        if latex_fixes:
            report["latex_fixes"] = latex_fixes
        if quality_scores:
            report["quality_scores"] = quality_scores
        if render_issues:
            report["render_issues"] = [i.to_dict() for i in render_issues]
        if stage_errors:
            report["stage_errors"] = stage_errors
        report["export_engines"] = export_result["engines"]
        if bilingual_path is not None:
            report["bilingual_markdown"] = str(bilingual_path)
        if self.translator.doc_terms:
            report["auto_glossary"] = self.translator.doc_terms
        # A garbled/scanned source that wasn't OCR'd yields nonsense even if
        # every segment "translated" — never report that as a clean success.
        if report["segments_failed"] == 0 and not report.get("scan_warning") and not stage_errors:
            status = "completed"
        else:
            status = "partial"
        # Clean the resume checkpoint on a full success so a later
        # deliberate re-run starts fresh; a partial run keeps it to resume.
        if checkpoint is not None and status == "completed":
            checkpoint.clear()
        stage("done", 1.0)
        return JobResult(
            job_id=job_id,
            status=status,
            output_markdown_path=str(output_path),
            assets_dir=str(assets_dir) if assets_dir else None,
            report=report,
            exports=export_result["files"],
            segments=[s.to_dict() for s in segments],
        )

    def _vision_client(self) -> Optional[BaseLLMClient]:
        """A vision-capable client (built once, reused for OCR + figures)."""
        if self._vision_client_built:
            return self._vision_client_cached
        self._vision_client_built = True
        if self.client.supports_vision and not self.config.vision_provider:
            self._vision_client_cached = self.client
        else:
            try:
                self._vision_client_cached = create_client(
                    self.config.vision_provider_config()
                )
            except Exception as exc:
                logger.warning("Vision client unavailable: %s", exc)
                self._vision_client_cached = None
        return self._vision_client_cached


def _bilingual_texts(segments) -> list[str]:
    """Alternate source/translation for proofreading output."""
    texts = []
    for segment in segments:
        if segment.kind == "translate" and segment.translation:
            quoted = "\n".join("> " + line for line in segment.source_text.splitlines())
            texts.append(quoted)
            texts.append(segment.translation)
        else:
            texts.append(segment.source_text)
    return texts


def _first_heading(markdown: str) -> Optional[str]:
    for line in markdown.splitlines():
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return None