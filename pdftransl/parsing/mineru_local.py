"""MinerU как локальный CLI-подпроцесс.

Лучшее распознавание формул/таблиц; тяжёлый (torch + модели).
Подпроцесс ограничен parser_timeout — зависший MinerU принудительно
останавливается с внятной ошибкой, а не висит вечно.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError
from pdftransl.models import ParsedDocument
from pdftransl.parsing.base import ParserBackend, collect_assets, mineru_cli_available

logger = logging.getLogger(__name__)


class MineruLocalBackend(ParserBackend):
    name = "mineru_local"

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config

    def available(self) -> bool:
        return mineru_cli_available()

    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        exe = shutil.which("mineru")
        if exe:
            command = [exe, "-p", str(pdf_path), "-o", str(workdir), "-b", "pipeline"]
        else:  # legacy CLI name
            command = ["magic-pdf", "-p", str(pdf_path), "-o", str(workdir)]

        timeout = getattr(self.config, "parser_timeout", 1800) if self.config else 1800
        logger.info("Running MinerU (timeout %ss): %s", timeout, " ".join(command))
        try:
            subprocess.run(
                command, check=True, capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise ParserError(f"MinerU CLI not found: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ParserError(
                f"MinerU timed out after {timeout}s — the file is large and MinerU "
                "is slow on CPU (no CUDA). Try a smaller file, raise "
                "PDFTRANSL_PARSER_TIMEOUT, or use a lighter backend "
                "(--backend marker / vlm_ocr / pymupdf)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or "")[-1500:]
            raise ParserError(
                f"MinerU failed (exit {exc.returncode}): {tail or exc}"
            ) from exc

        md_files = sorted(
            workdir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True
        )
        if not md_files:
            raise ParserError(f"MinerU produced no markdown under {workdir}")
        md_path = md_files[0]
        markdown = md_path.read_text(encoding="utf-8")
        assets = collect_assets(md_path.parent, markdown)
        logger.info("MinerU markdown: %s (%d assets)", md_path, len(assets))
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            assets=assets,
            backend=self.name,
        )
