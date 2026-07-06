"""Parser backend interface and backend selection."""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserUnavailableError
from pdftransl.models import Asset, ParsedDocument

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}


class ParserBackend(ABC):
    """Turns a PDF into Markdown (with LaTeX formulas) + exported assets."""

    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """Whether this backend can run in the current environment."""

    @abstractmethod
    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        """Parse ``pdf_path``; write intermediate files under ``workdir``."""


def collect_assets(markdown_dir: Path, markdown: str) -> list[Asset]:
    """Find image files referenced from (or exported next to) the markdown."""
    assets: list[Asset] = []
    seen: set[str] = set()
    for path in sorted(markdown_dir.rglob("*")):
        if path.suffix.lower() in _IMAGE_EXTS and path.is_file():
            rel = path.relative_to(markdown_dir).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            assets.append(Asset(path=str(path), rel_path=rel, kind="image"))
    return assets


def get_backend(config: PipelineConfig) -> ParserBackend:
    """Pick a parsing backend according to config ('auto' probes in order
    of scientific-PDF quality: local MinerU -> MinerU cloud API ->
    marker -> docling -> PyMuPDF fallback)."""
    from pdftransl.parsing.docling_backend import DoclingBackend
    from pdftransl.parsing.marker_backend import MarkerBackend
    from pdftransl.parsing.mineru_api import MineruApiBackend
    from pdftransl.parsing.mineru_local import MineruLocalBackend
    from pdftransl.parsing.pymupdf_backend import PyMuPdfBackend

    name = config.parser_backend
    if name == "auto":
        for backend in (
            MineruLocalBackend(),
            MineruApiBackend(config),
            MarkerBackend(),
            DoclingBackend(),
            PyMuPdfBackend(),
        ):
            if backend.available():
                return backend
        raise ParserUnavailableError(
            "No parsing backend available. Install MinerU (`pip install mineru`), "
            "set MINERU_API_KEY for the cloud API, install marker-pdf/docling, "
            "or install PyMuPDF (`pip install PyMuPDF`) as a fallback."
        )

    backends: dict[str, ParserBackend] = {
        "mineru_local": MineruLocalBackend(),
        "mineru_api": MineruApiBackend(config),
        "marker": MarkerBackend(),
        "docling": DoclingBackend(),
        "pymupdf": PyMuPdfBackend(),
    }
    if name not in backends:
        raise ParserUnavailableError(
            f"Unknown parser backend '{name}'. Known: auto, {', '.join(backends)}."
        )
    backend = backends[name]
    if not backend.available():
        raise ParserUnavailableError(f"Parser backend '{name}' is not available.")
    return backend


def parse_pdf(
    pdf_path: str | Path,
    workdir: str | Path,
    config: PipelineConfig | None = None,
) -> ParsedDocument:
    """Convenience wrapper: select a backend and parse."""
    config = config or PipelineConfig.from_env()
    backend = get_backend(config)
    return backend.parse(pdf_path, workdir)


def mineru_cli_available() -> bool:
    return shutil.which("mineru") is not None or shutil.which("magic-pdf") is not None


def mineru_api_key(config: PipelineConfig) -> str | None:
    return os.environ.get(config.mineru_api_key_env)
