"""Nougat (Meta) — сквозной OCR научных статей в Markdown.

Распознаёт формулы прямо в LaTeX. Практически требует GPU; на CPU —
только терпеливым.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError
from pdftransl.models import ParsedDocument
from pdftransl.parsing.base import ParserBackend, collect_assets

logger = logging.getLogger(__name__)


class NougatBackend(ParserBackend):
    name = "nougat"

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config

    def available(self) -> bool:
        if shutil.which("nougat"):
            return True
        try:
            import nougat  # noqa: F401
            return True
        except ImportError:
            return False

    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        exe = shutil.which("nougat")
        if not exe:
            raise ParserError("nougat CLI not found (pip install nougat-ocr)")
        command = [exe, str(pdf_path), "-o", str(workdir), "--markdown"]
        timeout = getattr(self.config, "parser_timeout", 1800) if self.config else 1800
        logger.info("Running Nougat (timeout %ss): %s", timeout, " ".join(command))
        try:
            subprocess.run(command, check=True, capture_output=True, text=True,
                           timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ParserError(f"Nougat timed out after {timeout}s") from exc
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or "")[-1500:]
            raise ParserError(f"Nougat failed (exit {exc.returncode}): {tail or exc}") from exc

        md_files = sorted(workdir.rglob("*.mmd")) + sorted(workdir.rglob("*.md"))
        if not md_files:
            raise ParserError(f"Nougat produced no markdown under {workdir}")
        md_path = md_files[0]
        markdown = md_path.read_text(encoding="utf-8")
        assets = collect_assets(md_path.parent, markdown)
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            assets=assets,
            backend=self.name,
        )
