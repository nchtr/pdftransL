"""OCR-бэкенд на vision-модели — для сканов и битых PDF.

Рендерит каждую страницу в картинку и просит vision-модель
транскрибировать её в Markdown с LaTeX-формулами. Единственный путь
парсинга, который справляется со сканами *и* распознаёт их формулы;
работает везде, где доступна vision-модель: облако (gpt-4o, Claude),
локальный VLM (qwen2.5-vl в Ollama) или специализированная OCR-модель
(DeepSeek-OCR, GOT-OCR через vLLM — им даётся короткий
grounding-промпт вместо тяжёлого системного). OCR-модель выбирается
независимо от модели перевода (vision_model / vision_provider).

Включается явно (--backend vlm_ocr) или автоматически, когда детектор
нашёл скан/кракозябры и ocr_on_scan включён.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError, ParserUnavailableError
from pdftransl.llm.base import BaseLLMClient, vision_message
from pdftransl.models import Asset, ParsedDocument
from pdftransl.parsing.base import ParserBackend

logger = logging.getLogger(__name__)

# Локальные квантованные VLM (DeepSeek-OCR, Qwen-VL) иногда «забывают»
# остановиться и выплёвывают свои служебные стоп-токены как обычный текст:
# <|im_end|>, <|endoftext|>, </s> и т.п. В настоящем документе такого не
# бывает, поэтому вычищаем. Осторожно: HTML-теги (<table>, <br>) НЕ трогаем
# — они бывают легитимными; убираем только заведомо служебные последовательности.
_CONTROL_TOKEN_RE = re.compile(r"<\|[^|>]{0,40}\|>")   # <|im_end|>, <|eot_id|>, ...
_SENTINEL_TOKENS = ("</s>", "<s>", "<|endoftext|>", "<pad>", "</angela>")
# Галлюцинация «пустых» ячеек: модель повторяет None, когда не может
# прочитать таблицу. 3+ подряд — гарантированно мусор (одиночное None может
# быть легитимным словом в коде, поэтому его не трогаем).
_NONE_RUN_RE = re.compile(r"(?:None\s*){3,}")


def clean_ocr_artifacts(text: str) -> str:
    """Убрать служебные стоп-токены и галлюцинации локальных VLM."""
    if not text:
        return text
    text = _CONTROL_TOKEN_RE.sub("", text)
    for tok in _SENTINEL_TOKENS:
        text = text.replace(tok, "")
    text = _NONE_RUN_RE.sub("", text)
    return text

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
        """One page → Markdown; one retry, then a visible placeholder.

        Температура жёстко 0.0: любая «креативность» заставляет локальные
        VLM выдумывать таблицы и ячейки (тот самый ``NoneNone``). Ответ
        прогоняется через ``clean_ocr_artifacts`` — чистит утёкшие
        стоп-токены квантованных моделей.
        """
        messages = self._build_messages(client, img_path)
        last_exc = None
        for attempt in range(2):
            try:
                return clean_ocr_artifacts(client.chat(messages, temperature=0.0))
            except Exception as exc:  # noqa: BLE001 - keep OCR going per page
                last_exc = exc
                logger.warning("VLM OCR page %d attempt %d failed: %s",
                               page_no, attempt + 1, exc)
        logger.error("VLM OCR gave up on page %d: %s", page_no, last_exc)
        return f"<!-- OCR failed for page {page_no}: {last_exc} -->"

    def _unload_local_vision(self, client: BaseLLMClient) -> None:
        """Выгрузить локальную vision-модель из памяти после OCR.

        Ollama держит модель в VRAM/RAM ещё несколько минут после
        последнего запроса — а следом пайплайн грузит модель перевода, и
        две большие модели разом дают OOM (ровно тот кейс с «съело 36 ГБ
        и упало»). Просим Ollama выгрузить её сразу: keep_alive=0 на
        нативном эндпоинте. Best-effort: не Ollama/недоступно — тихо
        пропускаем (memory_guard всё равно подстрахует)."""
        if not self.config.vision_unload_after_ocr:
            return
        cfg = getattr(client, "config", None)
        base_url = getattr(cfg, "base_url", None)
        model = getattr(client, "model", None)
        if not base_url or not model or not getattr(cfg, "is_local", False):
            return
        # эндпоинт Ollama: .../v1 -> корень + /api/generate
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        try:
            import requests

            requests.post(
                f"{root}/api/generate",
                json={"model": model, "keep_alive": 0},
                timeout=5,
            )
            logger.info("Requested unload of local vision model '%s' to free memory",
                        model)
        except Exception as exc:  # noqa: BLE001 - выгрузка сугубо best-effort
            logger.debug("Vision-model unload skipped (%s): %s", model, exc)

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

        try:
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
        finally:
            doc.close()
            # Освобождаем память локальной vision-модели до загрузки модели
            # перевода — даже если OCR прервался на середине.
            self._unload_local_vision(client)

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
