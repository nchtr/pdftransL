"""End-to-end pipeline orchestration.

    PDF -> parse (MinerU/PyMuPDF) -> split into blocks -> mask formulas
        -> RAG context -> LLM translate -> validate -> repair loop
        -> LLM review -> assemble markdown -> export assets & report
        -> learn (store pairs into translation memory)
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from pdftransl.config import PipelineConfig
from pdftransl.llm.base import BaseLLMClient
from pdftransl.llm.registry import create_client
from pdftransl.masking import Masker
from pdftransl.models import JobResult, ParsedDocument, Segment, new_id
from pdftransl.parsing.base import get_backend
from pdftransl.parsing.splitter import assemble, split_markdown
from pdftransl.quality.reviewer import Reviewer
from pdftransl.quality.validators import document_report
from pdftransl.rag.embeddings import get_embedder
from pdftransl.rag.glossary import Glossary
from pdftransl.rag.retriever import RAGContextBuilder
from pdftransl.rag.store import TranslationMemory
from pdftransl.translation.figures import describe_figures
from pdftransl.translation.translator import Translator, build_segments

logger = logging.getLogger(__name__)

StageCb = Callable[[str, float], None]  # (stage_name, progress 0..1)


class TranslationPipeline:
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        client: Optional[BaseLLMClient] = None,
        tm: Optional[TranslationMemory] = None,
        glossary: Optional[Glossary] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.client = client or create_client(self.config.provider_config())
        if self.config.use_rag:
            embedder = get_embedder(self.config)
            self.tm = tm or TranslationMemory(self.config.db_path, embedder)
            self.glossary = glossary or Glossary(self.config.db_path)
            self.retriever = RAGContextBuilder(self.config, self.tm, self.glossary)
        else:
            self.tm = tm
            self.glossary = glossary
            self.retriever = None
        self.translator = Translator(self.client, self.config, self.retriever)
        self.reviewer = Reviewer(self.client, self.config) if self.config.review else None

    # ------------------------------------------------------------------
    def run(
        self,
        pdf_path: str | Path,
        output_dir: Optional[str | Path] = None,
        job_id: Optional[str] = None,
        on_stage: Optional[StageCb] = None,
    ) -> JobResult:
        """Full run: PDF in, translated markdown + assets + report out."""
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
    ) -> JobResult:
        """Translate an already-parsed markdown document (skip parsing)."""
        job_id = job_id or new_id("job_")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        parsed = ParsedDocument(source_path="", markdown=markdown, backend="markdown")

        def stage(name: str, progress: float) -> None:
            if on_stage:
                on_stage(name, progress)

        result = self._translate_parsed(
            parsed, output_path.parent, job_id, stage, output_name=output_path.name
        )
        return result

    # ------------------------------------------------------------------
    def _parse(self, pdf_path: Path, workdir: Path) -> ParsedDocument:
        backend = get_backend(self.config)
        logger.info("Parsing %s with backend '%s'", pdf_path.name, backend.name)
        return backend.parse(pdf_path, workdir)

    def _translate_parsed(
        self,
        parsed: ParsedDocument,
        out_dir: Path,
        job_id: str,
        stage: StageCb,
        output_name: str,
    ) -> JobResult:
        cfg = self.config

        # 1. structural split + segmentation with masking
        stage("split", 0.1)
        blocks = split_markdown(parsed.markdown)
        segments = build_segments(blocks, Masker(), cfg.chunk_char_budget)
        logger.info(
            "[%s] %d blocks -> %d segments (%d to translate)",
            job_id, len(blocks), len(segments),
            sum(1 for s in segments if s.kind == "translate"),
        )

        # 2. translate with validation + repair loop
        def progress(done: int, total: int, _seg_id: str) -> None:
            stage("translate", 0.1 + 0.6 * (done / max(total, 1)))

        self.translator.translate_segments(segments, progress=progress)

        # 3. LLM review of flagged segments
        if self.reviewer is not None:
            stage("review", 0.75)
            self.reviewer.review_segments(segments, only_flagged=True)

        # 4. assemble output document
        stage("assemble", 0.85)
        translated_md = assemble([s.final_text() for s in segments])
        output_path = out_dir / output_name
        output_path.write_text(translated_md, encoding="utf-8")

        # 5. export assets next to the translated markdown
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

        # 6. optional VLM figure descriptions
        if cfg.describe_figures and parsed.assets:
            stage("figures", 0.9)
            vision_client = self._vision_client()
            if vision_client is not None:
                describe_figures(
                    parsed.assets, vision_client, cfg,
                    output_json=out_dir / "figures.json",
                )

        # 7. learn: push good pairs into the translation memory
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
                    )
                    learned += 1
            logger.info("[%s] stored %d segments into translation memory", job_id, learned)

        # 8. report
        report = document_report(segments)
        report["assets"] = [a.to_dict() for a in parsed.assets]
        status = "completed" if report["segments_failed"] == 0 else "partial"
        stage("done", 1.0)
        return JobResult(
            job_id=job_id,
            status=status,
            output_markdown_path=str(output_path),
            assets_dir=str(assets_dir) if assets_dir else None,
            report=report,
        )

    def _vision_client(self) -> Optional[BaseLLMClient]:
        if self.client.supports_vision and not self.config.vision_provider:
            return self.client
        try:
            return create_client(self.config.vision_provider_config())
        except Exception as exc:
            logger.warning("Vision client unavailable: %s", exc)
            return None
