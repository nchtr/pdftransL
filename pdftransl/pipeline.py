"""End-to-end pipeline orchestration.

    PDF -> parse (cached; MinerU/PyMuPDF) -> split into blocks
        -> mark References section -> document summary + auto-glossary
        -> mask formulas -> RAG context -> parallel LLM translation
        -> validate -> repair loop -> LLM review -> back-translation
        -> LaTeX syntax check -> assemble markdown (optionally bilingual)
        -> export HTML/DOCX/PDF -> assets & report -> learn (TM)
"""

from __future__ import annotations

import logging
import shutil
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


def _build_client(config: PipelineConfig) -> BaseLLMClient:
    # one shared limiter: the whole chain respects a single rpm budget
    rate_limiter = None
    if config.rpm_limit:
        from pdftransl.llm.ratelimit import RateLimiter

        rate_limiter = RateLimiter(config.rpm_limit)
    primary = create_client(config.provider_config(), rate_limiter=rate_limiter)
    if not config.fallback_providers:
        return primary
    chain = [primary]
    for name in config.fallback_providers:
        if name == config.provider:
            continue
        try:
            chain.append(
                create_client(get_provider_config(name), rate_limiter=rate_limiter)
            )
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

    # ------------------------------------------------------------------
    def run(
        self,
        pdf_path: str | Path,
        output_dir: Optional[str | Path] = None,
        job_id: Optional[str] = None,
        on_stage: Optional[StageCb] = None,
    ) -> JobResult:
        """Full run: PDF in; translated markdown/HTML/DOCX/PDF out."""
        job_id = job_id or new_id("job_")
        pdf_path = Path(pdf_path)
        out_dir = Path(output_dir or self.config.output_dir) / pdf_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()

        def stage(name: str, progress: float) -> None:
            logger.info("[%s] stage=%s progress=%.0f%%", job_id, name, progress * 100)
            if on_stage:
                on_stage(name, progress)

        try:
            stage("parse", 0.0)
            parsed = self._parse(pdf_path, out_dir / "parse")
            source_md_path = out_dir / f"{pdf_path.stem}.md"
            source_md_path.write_text(parsed.markdown, encoding="utf-8")

            result = self._translate_parsed(
                parsed, out_dir, job_id, stage,
                output_name=f"{pdf_path.stem}.{self.config.target_lang}.md",
            )
            result.source_markdown_path = str(source_md_path)
            result.report["duration_sec"] = round(time.time() - started, 1)
            result.report["parser_backend"] = parsed.backend
            if parsed.meta.get("cache"):
                result.report["parse_cache"] = "hit"
            report_path = out_dir / "report.json"
            result.report_path = str(report_path)
            result.save_report(report_path)
            return result
        except Exception as exc:
            logger.exception("[%s] pipeline failed", job_id)
            return JobResult(job_id=job_id, status="failed", error=str(exc))

    def translate_markdown(
        self,
        markdown: str,
        output_path: str | Path,
        job_id: Optional[str] = None,
        on_stage: Optional[StageCb] = None,
        assets_dir: Optional[str | Path] = None,
    ) -> JobResult:
        """Translate an already-parsed markdown document (skip parsing)."""
        job_id = job_id or new_id("job_")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        parsed = ParsedDocument(source_path="", markdown=markdown, backend="markdown")

        def stage(name: str, progress: float) -> None:
            if on_stage:
                on_stage(name, progress)

        return self._translate_parsed(
            parsed, output_path.parent, job_id, stage, output_name=output_path.name
        )

    # ------------------------------------------------------------------
    def _parse(self, pdf_path: Path, workdir: Path) -> ParsedDocument:
        backend = get_backend(self.config)
        scan: dict = {}

        # A text extractor fails on two kinds of PDF: scanned (no text
        # layer) and garbled (broken font encoding -> "кракозябры"). Both
        # need OCR. Detect and route when a vision model is available.
        if self.config.ocr_on_scan and backend.name not in _OCR_BACKENDS:
            from pdftransl.parsing.scan_detect import scan_stats
            from pdftransl.parsing.vlm_ocr_backend import VlmOcrBackend

            scan = scan_stats(pdf_path)
            if scan.get("needs_ocr"):
                reason = "scanned" if scan.get("is_scanned") else "garbled text layer"
                # Auto-route only to a genuinely vision-capable client, so we
                # never send page images to a text-only model. For a local VLM
                # (marked non-vision in presets) select --backend vlm_ocr.
                vclient = self._vision_client()
                if vclient is not None and getattr(vclient, "supports_vision", False):
                    logger.info(
                        "PDF needs OCR (%s, garbled_ratio=%.2f); routing to VLM OCR",
                        reason, scan.get("garbled_ratio", 0),
                    )
                    backend = VlmOcrBackend(self.config, client=vclient)
                else:
                    logger.warning(
                        "PDF needs OCR (%s) but no vision provider is available — "
                        "output will be unreliable. Set a vision provider "
                        "(e.g. Ollama + qwen2.5-vl), install MinerU, or pass "
                        "--backend vlm_ocr.", reason,
                    )

        cache = (
            ParseCache(self.config.output_dir) if self.config.parse_cache else None
        )
        if cache is not None:
            cached = cache.get(pdf_path, backend.name)
            if cached is not None:
                if scan:
                    cached.meta.setdefault("scan", scan)
                return cached
        logger.info("Parsing %s with backend '%s'", pdf_path.name, backend.name)
        parsed = backend.parse(pdf_path, workdir)
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
                    "translation will be garbage. Enable OCR: set a vision "
                    "provider (e.g. Ollama qwen2.5-vl), install MinerU, or "
                    "pass --backend vlm_ocr."
                )
        if cache is not None:
            try:
                cache.put(pdf_path, parsed)
            except OSError as exc:
                logger.warning("Parse cache write failed: %s", exc)
        return parsed

    def _translate_parsed(
        self,
        parsed: ParsedDocument,
        out_dir: Path,
        job_id: str,
        stage: StageCb,
        output_name: str,
    ) -> JobResult:
        cfg = self.config

        # Sanity check: does the source text match the declared source
        # language? Catches the common "forgot to flip RU<->EN" mistake.
        from pdftransl.parsing.text_quality import language_mismatch

        wrong_script = language_mismatch(parsed.markdown, cfg.source_lang)

        # 1. structural split; keep bibliography untranslated
        stage("split", 0.05)
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
            stage("context", 0.08)
            self.translator.doc_summary = build_doc_summary(
                parsed.markdown, self.client, cfg
            )
        if cfg.auto_glossary:
            stage("context", 0.1)
            self.translator.doc_terms = extract_terms(
                parsed.markdown, self.client, cfg
            )
            logger.info("[%s] auto-glossary: %d terms",
                        job_id, len(self.translator.doc_terms))

        # 3. parallel translation with validation + repair loop
        def progress(done: int, total: int, _seg_id: str) -> None:
            stage("translate", 0.1 + 0.5 * (done / max(total, 1)))

        self.translator.translate_segments(segments, progress=progress)

        # 4. optional LLM-judge quality scoring (flags weak segments)
        quality_scores: dict = {}
        if cfg.quality_score:
            stage("scoring", 0.62)
            from pdftransl.quality.scoring import score_segments

            quality_scores = score_segments(segments, self.client, cfg)

        # 5. LLM review of flagged segments
        if self.reviewer is not None:
            stage("review", 0.65)
            self.reviewer.review_segments(segments, only_flagged=True)

        # 6. optional back-translation semantic check
        if cfg.backtranslation_check:
            stage("backtranslation", 0.72)
            backtranslation_check(segments, self.client, self.embedder, cfg)

        # 7. assemble; repair broken LaTeX before writing files
        stage("assemble", 0.78)
        translated_md = assemble([s.final_text() for s in segments])
        latex_issues = latex_check(translated_md)
        latex_fixes: list = []
        if cfg.fix_latex and latex_issues:
            stage("latex_fix", 0.8)
            from pdftransl.quality.latex_fix import fix_document

            translated_md, latex_fixes = fix_document(translated_md, self.client, cfg)
            latex_issues = latex_check(translated_md)

        output_path = out_dir / output_name
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
                    dst = assets_dir / (asset.rel_path or src.name)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.resolve() != dst.resolve():
                        shutil.copy2(src, dst)

        # 9. optional VLM figure descriptions
        figure_descriptions: dict = {}
        if cfg.describe_figures and parsed.assets:
            stage("figures", 0.82)
            vision_client = self._vision_client()
            if vision_client is not None:
                figure_descriptions = describe_figures(
                    parsed.assets, vision_client, cfg,
                    output_json=out_dir / "figures.json",
                )
            else:
                figure_descriptions = {}
                logger.warning(
                    "describe_figures requested but no vision provider available"
                )

        # 10. export to HTML / LaTeX / DOCX / PDF
        export_result = {"files": {}, "engines": {}}
        if cfg.export_formats:
            stage("export", 0.88)
            title = _first_heading(translated_md) or output_path.stem
            export_result = export_document(
                translated_md,
                out_base=output_path.with_suffix(""),
                formats=cfg.export_formats,
                assets_dir=assets_dir,
                title=title,
            )

        # 10b. optional render check of the exported HTML (KaTeX errors)
        render_issues: list = []
        if cfg.render_check and export_result["files"].get("html"):
            stage("render_check", 0.92)
            from pdftransl.quality.render_check import check_rendered_html

            render_issues = check_rendered_html(export_result["files"]["html"])

        # 11. learn: push good pairs into the translation memory
        if cfg.learn and self.tm is not None:
            stage("learn", 0.95)
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
            logger.info("[%s] stored %d segments into translation memory", job_id, learned)

        # 12. report
        report = document_report(segments)
        report["assets"] = [a.to_dict() for a in parsed.assets]
        report["references_blocks_kept"] = refs_skipped
        report["latex_issues"] = [i.to_dict() for i in latex_issues]
        if parsed.meta.get("scan"):
            report["scan"] = parsed.meta["scan"]
        if parsed.meta.get("scan_warning"):
            report["scan_warning"] = parsed.meta["scan_warning"]
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
        report["export_engines"] = export_result["engines"]
        if bilingual_path is not None:
            report["bilingual_markdown"] = str(bilingual_path)
        if self.translator.doc_terms:
            report["auto_glossary"] = self.translator.doc_terms
        # A garbled/scanned source that wasn't OCR'd yields nonsense even if
        # every segment "translated" — never report that as a clean success.
        if report["segments_failed"] == 0 and not report.get("scan_warning"):
            status = "completed"
        else:
            status = "partial"
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
