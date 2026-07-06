"""Anthropic Messages API client (Claude models).

Accepts OpenAI-style messages and converts them: the system message
becomes the top-level ``system`` field, image parts become Anthropic
content blocks.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from pdftransl.config import ProviderConfig
from pdftransl.exceptions import LLMError
from pdftransl.llm.base import BaseLLMClient, Message

logger = logging.getLogger(__name__)

_RETRIABLE = {429, 500, 502, 503, 529}
_API_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 8192


def _convert_content(content: Any) -> Any:
    """OpenAI content parts -> Anthropic content blocks."""
    if isinstance(content, str):
        return content
    blocks = []
    for part in content:
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            if not url.startswith("data:"):
                raise LLMError("Anthropic client requires base64 data URLs for images")
            header, b64 = url.split(",", 1)
            media_type = header.split(":", 1)[1].split(";", 1)[0]
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            })
    return blocks


class AnthropicClient(BaseLLMClient):
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.model = config.model
        self.supports_vision = True
        key = config.resolve_api_key()
        if not key:
            raise LLMError(
                f"No API key for provider 'anthropic' (set env var {config.api_key_env})."
            )
        self._headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": _API_VERSION,
        }
        self._headers.update(config.extra_headers)

    def chat(
        self,
        messages: list[Message],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,  # not supported; JSON asked in prompt
    ) -> str:
        system = None
        converted = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
            else:
                converted.append(
                    {"role": msg["role"], "content": _convert_content(msg["content"])}
                )
        payload: dict = {
            "model": self.model,
            "messages": converted,
            "temperature": temperature,
            "max_tokens": max_tokens or _DEFAULT_MAX_TOKENS,
        }
        if system:
            payload["system"] = system

        url = f"{self.config.base_url.rstrip('/')}/messages"
        last_error: Optional[str] = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                delay = min(2 ** attempt, 30)
                logger.warning("Anthropic retry %d in %ds (%s)", attempt, delay, last_error)
                time.sleep(delay)
            try:
                resp = requests.post(
                    url, json=payload, headers=self._headers,
                    timeout=self.config.timeout,
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                continue
            if resp.status_code in _RETRIABLE:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                continue
            if resp.status_code != 200:
                raise LLMError(f"anthropic HTTP {resp.status_code}: {resp.text[:500]}")
            try:
                data = resp.json()
                parts = [b["text"] for b in data["content"] if b.get("type") == "text"]
                return "".join(parts)
            except (KeyError, ValueError) as exc:
                raise LLMError(f"anthropic: malformed response: {resp.text[:500]}") from exc
        raise LLMError(f"anthropic: retries exhausted ({last_error})")
