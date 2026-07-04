"""Framework-agnostic service facade.

Designed for backend integration (Django, FastAPI, Flask, Celery):

    service = TranslationService(PipelineConfig.from_env())

    # web request thread / view:
    job_id = service.submit("paper.pdf")

    # worker (Celery task, thread, management command):
    service.process(job_id)

    # polling endpoint:
    service.status(job_id)   # {"status": "running", "stage": ..., ...}

Human feedback ("learning"):

    service.add_correction(source, corrected_translation)
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

    def process(self, job_id: str) -> JobResult:
        """Run a previously submitted job (call from a worker)."""
        job = self.repo.get(job_id)
        self.repo.update(job_id, status="running", stage="parse", progress=0.0)

        def on_stage(stage: str, progress: float) -> None:
            try:
                self.repo.update(job_id, stage=stage, progress=progress)
            except Exception:  # never let bookkeeping kill the run
                logger.warning("Failed to persist progress for %s", job_id)

        result = self.pipeline.run(
            job["pdf_path"], job["output_dir"], job_id=job_id, on_stage=on_stage
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
        automatic translations of the same/similar text."""
        self._tm().add(
            source, corrected,
            source_lang or self.config.source_lang,
            target_lang or self.config.target_lang,
            origin="human",
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
