"""Local MinerU CLI backend.

MinerU (https://github.com/opendatalab/MinerU) is the best open-source
option for scientific PDFs: layout analysis, formula recognition to
LaTeX, table extraction and figure export. Requires `pip install
mineru[core]` (or legacy `magic-pdf`).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from pdftransl.exceptions import ParserError
from pdftransl.models import ParsedDocument
from pdftransl.parsing.base import ParserBackend, collect_assets, mineru_cli_available

logger = logging.getLogger(__name__)


class MineruLocalBackend(ParserBackend):
    name = "mineru_local"

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

        logger.info("Running MinerU: %s", " ".join(command))
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise ParserError(f"MinerU CLI not found: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            raise ParserError(
                f"MinerU failed (exit {exc.returncode}): {exc.stderr[-2000:] if exc.stderr else exc}"
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
