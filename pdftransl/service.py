"""Фасад движка без привязки к фреймворку.

Для интеграции в любой бэкенд (Django, FastAPI, Celery):

    service = TranslationService(PipelineConfig.from_env())
    job_id = service.submit("paper.pdf")   # из веб-запроса
    service.process(job_id)               # из воркера
    service.status(job_id)                # из поллинг-эндпоинта

Обратная связь человека («обучение»):

    service.add_correction(source, corrected)
    service.add_glossary_term("attention head", "головка внимания")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from pdftransl.config import PipelineConfig
from pdftransl.models import JobResult
from pdftransl.pipeline import TranslationPipeline
from pdftransl.rag.embeddings import get_embedder
from pdftransl.rag.glossary import Glossary
from pdftransl.rag.store import TranslationMemory
from pdftransl.storage.repository import JobRepository

logger = logging.getLogger(__name__)


def looks_like_term(text: str) -> bool:
    """Heuristic: short, single-line, few words, no sentence ending —
    a corrected fragment like this is a terminology entry, not prose."""
    text = text.strip()
    return (
        0 < len(text) <= 60
        and "\n" not in text
        and len(text.split()) <= 5
        and not text.endswith((".", "!", "?"))
    )


class TranslationService:
    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        pipeline: Optional[TranslationPipeline] = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.repo = JobRepository(self.config.db_path)
        self._pipeline = pipeline  # lazily created: LLM client needs keys

    @property
    def pipeline(self) -> TranslationPipeline:
        if self._pipeline is None:
            self._pipeline = TranslationPipeline(self.config)
        return self._pipeline

    # -- job lifecycle -------------------------------------------------
    def submit(self, pdf_path: str | Path, output_dir: Optional[str] = None) -> str:
        """Register a job (e.g. from a web request). Does NOT run it."""
        pdf_path = str(pdf_path)
        return self.repo.create(
            pdf_path=pdf_path,
            output_dir=output_dir or self.config.output_dir,
            source_lang=self.config.source_lang,
            target_lang=self.config.target_lang,
        )

    def process(self, job_id: str, on_stage=None) -> JobResult:
        """Run a previously submitted job (call from a worker).

        ``on_stage(stage, progress)`` — optional extra progress callback
        (e.g. a Telegram status message); repository bookkeeping happens
        either way, so job rows never linger as "queued" while a caller
        drives the pipeline itself.
        """
        job = self.repo.get(job_id)
        self.repo.update(job_id, status="running", stage="parse", progress=0.0)

        def _on_stage(stage: str, progress: float) -> None:
            try:
                self.repo.update(job_id, stage=stage, progress=progress)
            except Exception:  # never let bookkeeping kill the run
                logger.warning("Failed to persist progress for %s", job_id)
            if on_stage is not None:
                try:
                    on_stage(stage, progress)
                except Exception:
                    logger.warning("External on_stage callback failed for %s", job_id)

        result = self.pipeline.run(
            job["pdf_path"], job["output_dir"], job_id=job_id, on_stage=_on_stage
        )
        self.repo.update(
            job_id,
            status=result.status,
            progress=1.0 if result.status != "failed" else None,
            result=result.to_dict(),
            error=result.error,
        )
        return result

    def translate(self, pdf_path: str | Path, output_dir: Optional[str] = None) -> JobResult:
        """Synchronous convenience: submit + process in one call."""
        job_id = self.submit(pdf_path, output_dir)
        return self.process(job_id)

    def status(self, job_id: str) -> dict[str, Any]:
        return self.repo.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repo.list(limit)

    # -- learning / feedback --------------------------------------------
    def _tm(self) -> TranslationMemory:
        if self._pipeline is not None and self._pipeline.tm is not None:
            return self._pipeline.tm
        return TranslationMemory(self.config.db_path, get_embedder(self.config))

    def add_correction(
        self,
        source: str,
        corrected: str,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> None:
        """Store a human-corrected translation; it will override future
        automatic translations of the same/similar text. Corrections
        that look like a single term also grow the glossary."""
        src_lang = source_lang or self.config.source_lang
        tgt_lang = target_lang or self.config.target_lang
        self._tm().add(source, corrected, src_lang, tgt_lang, origin="human")
        if looks_like_term(source) and looks_like_term(corrected):
            Glossary(self.config.db_path).add(
                source.strip(), corrected.strip(), src_lang, tgt_lang,
                notes="from human correction",
            )

    def add_glossary_term(
        self,
        term: str,
        translation: str,
        source_lang: Optional[str] = None,
        target_lang: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        Glossary(self.config.db_path).add(
            term, translation,
            source_lang or self.config.source_lang,
            target_lang or self.config.target_lang,
            notes,
        )

    def tm_stats(self) -> dict[str, Any]:
        return self._tm().stats()
