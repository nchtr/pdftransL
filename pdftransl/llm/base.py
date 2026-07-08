"""Интерфейс чат-клиента, не зависящий от провайдера.

Сообщения — в формате OpenAI: {"role": ..., "content": ...}, где
content — строка или (для vision) список частей; хелперы
text_content/image_content/vision_message собирают мультимодальные
сообщения. Конкретные клиенты переводят это в свой wire-формат.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

Message = dict[str, Any]

# Default limits for images sent to vision models. Full-page scans at
# 200+ DPI are multi-megapixel; base64 inflates them ~33% and can blow
# past provider payload/token limits. Downscaling to a sane resolution
# keeps requests cheap and reliable while staying legible for OCR.
DEFAULT_MAX_IMAGE_DIM = 2200
DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class BaseLLMClient(ABC):
    """Minimal chat-completion interface used by the pipeline."""

    supports_vision: bool = False
    model: str = ""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> str:
        """Send a chat request and return the assistant text.

        ``response_format`` follows the OpenAI convention (e.g.
        ``{"type": "json_object"}``); clients that don't support
        structured outputs are free to ignore it.
        """


def text_content(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def encode_image(
    image_path: str | Path,
    max_dim: int = DEFAULT_MAX_IMAGE_DIM,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> tuple[str, str]:
    """Return ``(mime, base64)`` for an image, downscaled if oversized.

    Uses Pillow when available to cap the longest side at ``max_dim``
    and shrink further until the encoded size fits ``max_bytes``.
    Without Pillow the file is sent as-is (with a warning if large).
    """
    path = Path(image_path)
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"

    try:
        from PIL import Image
    except ImportError:
        if len(raw) > max_bytes:
            logger.warning(
                "Image %s is %.1f MB and Pillow is not installed to downscale it; "
                "the vision request may fail.", path.name, len(raw) / 1e6,
            )
        return mime, base64.b64encode(raw).decode("ascii")

    import io

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            longest = max(img.size)
            if longest > max_dim:
                scale = max_dim / longest
                img = img.resize(
                    (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                    Image.LANCZOS,
                )
            # PNG keeps text/formulas crisp; fall back to JPEG if too heavy
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            fmt_mime = "image/png"
            quality = 90
            while len(data) > max_bytes and quality >= 50:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                data = buf.getvalue()
                fmt_mime = "image/jpeg"
                quality -= 15
            return fmt_mime, base64.b64encode(data).decode("ascii")
    except Exception as exc:  # pragma: no cover - unreadable/odd image
        logger.warning("Could not re-encode %s (%s); sending original", path.name, exc)
        return mime, base64.b64encode(raw).decode("ascii")


def image_content(
    image_path: str | Path,
    max_dim: int = DEFAULT_MAX_IMAGE_DIM,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> dict[str, Any]:
    """Build an OpenAI-style image part with a base64 data URL."""
    mime, data = encode_image(image_path, max_dim=max_dim, max_bytes=max_bytes)
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def vision_message(prompt: str, image_path: str | Path, **image_kwargs: Any) -> Message:
    """A user message pairing a text prompt with one image."""
    return {
        "role": "user",
        "content": [text_content(prompt), image_content(image_path, **image_kwargs)],
    }
