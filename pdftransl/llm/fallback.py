"""Multi-provider fallback chain.

Wraps several clients; each request tries them in order and moves on
when one raises LLMError (rate limit, outage, exhausted retries).
Typical setup: local model first, cloud as a backstop — or the
opposite for quality-first pipelines.
"""

from __future__ import annotations

import logging
from typing import Optional

from pdftransl.exceptions import LLMError
from pdftransl.llm.base import BaseLLMClient, Message

logger = logging.getLogger(__name__)


class FallbackClient(BaseLLMClient):
    def __init__(self, clients: list[BaseLLMClient]):
        if not clients:
            raise LLMError("FallbackClient needs at least one client")
        self.clients = clients
        self.model = clients[0].model
        self.supports_vision = any(c.supports_vision for c in clients)

    def chat(
        self,
        messages: list[Message],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> str:
        last_exc: Optional[Exception] = None
        for client in self.clients:
            try:
                return client.chat(
                    messages, temperature=temperature,
                    max_tokens=max_tokens, response_format=response_format,
                )
            except LLMError as exc:
                logger.warning(
                    "Provider %s failed (%s); falling back to next provider",
                    client.model, exc,
                )
                last_exc = exc
        raise LLMError(f"All providers in the fallback chain failed: {last_exc}")
