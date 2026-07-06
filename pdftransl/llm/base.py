"""Provider-agnostic chat client interface.

Messages use the OpenAI chat format: ``{"role": ..., "content": ...}``
where content is a string or, for vision models, a list of parts
(``text_content`` / ``image_content`` helpers). Individual clients
convert this to their native wire format.
"""

from __future__ import annotations

import base64
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

Message = dict[str, Any]


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


def image_content(image_path: str | Path) -> dict[str, Any]:
    """Build an OpenAI-style image part with a base64 data URL."""
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{data}"},
    }
