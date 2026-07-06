"""VLM OCR backend for scanned / image-only PDFs.

Renders every page to an image and asks a vision model to transcribe
it to Markdown with LaTeX math. This is the one parsing path that
handles scans *and* recognizes their formulas, and it runs anywhere a
vision model is reachable — a cloud API (gpt-4o, Claude) or a local
one (qwen2.5-vl via Ollama), so it works fully offline too.

Selected explicitly (``--backend vlm_ocr``) or automatically when the
pipeline detects a scan and ``ocr_on_scan`` is enabled.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError, ParserUnavailableError
from pdftransl.llm.base import BaseLLMClient, image_content, text_content
from pdftransl.models import Asset, ParsedDocument
from pdftransl.parsing.base import ParserBackend

logger = logging.getLogger(__name__)

_OCR_SYSTEM = """\
You are an OCR engine for scanned scientific papers. Transcribe the
page image to clean Markdown. Rules:
- Render every mathematical expression as LaTeX: $...$ inline, $$...$$
  for display equations. Transcribe formulas faithfully.
- Keep the reading order; use # headings for section titles.
- Reproduce tables as Markdown tables.
- Do NOT translate; transcribe in the original language.
- Output ONLY the transcription — no commentary, no code fences around
  the whole answer."""


class VlmOcrBackend(ParserBackend):
    name = "vlm_ocr"

    def __init__(self, config: PipelineConfig, client: Optional[BaseLLMClient] = None):
        self.config = config
        self._client = client

    def _get_client(self) -> BaseLLMClient:
        if self._client is None:
            from pdftransl.llm.registry import create_client

            self._client = create_client(self.config.vision_provider_config())
        return self._client

    def available(self) -> bool:
        try:
            import fitz  # noqa: F401  (need to rasterize pages)
        except ImportError:
            return False
        if self._client is not None:
            return True
        try:
            cfg = self.config.vision_provider_config()
        except Exception:
            return False
        return bool(cfg.resolve_api_key()) or cfg.is_local

    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        try:
            import fitz
        except ImportError as exc:
            raise ParserUnavailableError(
                "VLM OCR needs PyMuPDF to rasterize pages (`pip install PyMuPDF`)."
            ) from exc

        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        pages_dir = workdir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        client = self._get_client()
        if not client.supports_vision:
            logger.warning(
                "OCR provider '%s' is not marked vision-capable; attempting anyway",
                getattr(client, "model", "?"),
            )

        doc = fitz.open(str(pdf_path))
        total_pages = doc.page_count
        zoom = self.config.ocr_dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        page_count = min(total_pages, self.config.max_ocr_pages)
        parts: list[str] = []
        assets: list[Asset] = []

        for page_no in range(page_count):
            page = doc[page_no]
            pixmap = page.get_pixmap(matrix=matrix)
            img_path = pages_dir / f"page_{page_no + 1:03d}.png"
            pixmap.save(str(img_path))
            assets.append(Asset(path=str(img_path),
                                rel_path=f"pages/{img_path.name}",
                                kind="page", page=page_no + 1))
            logger.info("VLM OCR: page %d/%d", page_no + 1, page_count)
            try:
                text = client.chat(
                    [
                        {"role": "system", "content": _OCR_SYSTEM},
                        {"role": "user", "content": [
                            text_content("Transcribe this page."),
                            image_content(img_path),
                        ]},
                    ],
                    temperature=0.0,
                )
            except Exception as exc:
                logger.error("VLM OCR failed on page %d: %s", page_no + 1, exc)
                text = f"<!-- OCR failed for page {page_no + 1}: {exc} -->"
            parts.append(_strip_fence(text.strip()))
        doc.close()

        markdown = "\n\n".join(parts) + "\n"
        md_path = workdir / f"{pdf_path.stem}.md"
        md_path.write_text(markdown, encoding="utf-8")
        meta = {"ocr": True, "pages_transcribed": page_count, "total_pages": total_pages}
        if page_count < total_pages:
            meta["truncated"] = True
            logger.warning("VLM OCR: only first %d of %d pages transcribed "
                           "(max_ocr_pages)", page_count, total_pages)
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            assets=assets,
            backend=self.name,
            meta=meta,
        )


def _strip_fence(text: str) -> str:
    """Remove a ``` fence wrapping the whole page transcription."""
    if text.startswith("```") and text.endswith("```"):
        body = text[3:-3]
        nl = body.find("\n")
        if nl != -1 and " " not in body[:nl].strip():
            body = body[nl + 1:]
        return body.strip()
    return text
