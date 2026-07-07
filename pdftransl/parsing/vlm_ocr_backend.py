"""VLM OCR backend for scanned / image-only PDFs.

Renders every page to an image and asks a vision model to transcribe
it to Markdown with LaTeX math. This is the one parsing path that
handles scans *and* recognizes their formulas, and it runs anywhere a
vision model is reachable — a cloud API (gpt-4o, Claude), a general
local VLM (qwen2.5-vl via Ollama), or a *specialized* document-OCR
model (DeepSeek-OCR, GOT-OCR served via vLLM). The OCR model is chosen
independently of the translation model via ``vision_model`` /
``vision_provider``, so you can pair a strong OCR model for parsing
with a lighter LLM for translation.

Selected explicitly (``--backend vlm_ocr``) or automatically when the
pipeline detects a scan and ``ocr_on_scan`` is enabled.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError, ParserUnavailableError
from pdftransl.llm.base import BaseLLMClient, vision_message
from pdftransl.models import Asset, ParsedDocument
from pdftransl.parsing.base import ParserBackend

logger = logging.getLogger(__name__)

# Full instructions for a general-purpose VLM (gpt-4o, gemma3, qwen-vl).
_OCR_SYSTEM = """\
You are an OCR engine for scanned scientific papers. Transcribe the
page image to clean Markdown. Rules:
- Transcribe text in its ORIGINAL language and script EXACTLY as printed.
  Do NOT translate. Do NOT transliterate or romanize — if the page is in
  Russian, output Cyrillic letters; keep every language in its own script.
- Render every mathematical expression as LaTeX: $...$ inline, $$...$$
  for display equations. Transcribe formulas faithfully.
- Keep the reading order; use # headings for section titles.
- Reproduce tables as Markdown tables.
- Output ONLY the transcription — no commentary, no code fences around
  the whole answer."""

# Model-name substrings marking a purpose-built document-OCR model. These
# are trained to convert a page straight to markdown from a terse
# instruction and get confused by a long system prompt.
_SPECIALIZED_OCR_HINTS = ("deepseek-ocr", "got-ocr", "gotocr", "olmocr",
                          "nanonets-ocr", "docling", "-ocr")
# The instruction such models expect (DeepSeek-OCR "grounding" markdown mode).
_SPECIALIZED_OCR_PROMPT = "<|grounding|>Convert the document to markdown."


def is_specialized_ocr_model(model: Optional[str]) -> bool:
    name = (model or "").lower()
    return any(h in name for h in _SPECIALIZED_OCR_HINTS)


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

    def _build_messages(self, client: BaseLLMClient, img_path) -> list:
        """Page-transcription messages, tuned to the OCR model in use."""
        override = self.config.ocr_prompt
        if is_specialized_ocr_model(getattr(client, "model", "")):
            # specialized OCR model: terse instruction, no heavy system prompt
            prompt = override or _SPECIALIZED_OCR_PROMPT
            return [vision_message(prompt, img_path)]
        prompt = override or "Transcribe this page."
        return [
            {"role": "system", "content": _OCR_SYSTEM},
            vision_message(prompt, img_path),
        ]

    def _transcribe_page(self, client: BaseLLMClient, img_path, page_no: int) -> str:
        """One page → Markdown; one retry, then a visible placeholder."""
        messages = self._build_messages(client, img_path)
        last_exc = None
        for attempt in range(2):
            try:
                return client.chat(messages, temperature=0.0)
            except Exception as exc:  # noqa: BLE001 - keep OCR going per page
                last_exc = exc
                logger.warning("VLM OCR page %d attempt %d failed: %s",
                               page_no, attempt + 1, exc)
        logger.error("VLM OCR gave up on page %d: %s", page_no, last_exc)
        return f"<!-- OCR failed for page {page_no}: {last_exc} -->"

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
            text = self._transcribe_page(client, img_path, page_no + 1)
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
