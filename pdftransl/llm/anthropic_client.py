"""Нативный клиент Anthropic Messages API.

Отличия от OpenAI-формата: system отдельным полем, свой формат
картинок — конвертация происходит здесь. С v0.18 — keep-alive-сессия,
раздельный connect/read-таймаут, джиттер в бэкоффе и ретрай битых
ответов (та же обвязка стабильности, что и у OpenAI-клиента).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

import requests

from pdftransl.config import ProviderConfig
from pdftransl.exceptions import LLMError
from pdftransl.llm.base import BaseLLMClient, Message
from pdftransl.llm.openai_compat import _CONNECT_TIMEOUT, _make_session

logger = logging.getLogger(__name__)

_RETRIABLE = {408, 429, 500, 502, 503, 504, 529}
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
    def __init__(self, config: ProviderConfig, rate_limiter=None, cooldown_gate=None):
        self.config = config
        self.model = config.model
        self.supports_vision = True
        self._rate_limiter = rate_limiter
        self._cooldown_gate = cooldown_gate
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
        self._session = _make_session()

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
                delay = min(2.0 ** attempt, 30.0)
                if self._retry_after:
                    delay = max(delay, min(self._retry_after, 60.0))
                delay *= 0.75 + random.random() * 0.5
                logger.warning("Anthropic retry %d in %.1fs (%s)", attempt, delay, last_error)
                time.sleep(delay)
            if self._cooldown_gate is not None:
                self._cooldown_gate.wait()
            if self._rate_limiter is not None:
                self._rate_limiter.wait()
            read_timeout = self.config.timeout
            try:
                resp = self._session.post(
                    url, json=payload, headers=self._headers,
                    timeout=(min(_CONNECT_TIMEOUT, read_timeout), read_timeout),
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                continue
            if resp.status_code in _RETRIABLE:
                retry_hint = _header_retry_after(resp)
                self._retry_after = retry_hint
                if resp.status_code in (429, 529) and self._cooldown_gate is not None:
                    self._cooldown_gate.trip(retry_hint)
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                continue
            if resp.status_code != 200:
                raise LLMError(f"anthropic HTTP {resp.status_code}: {resp.text[:500]}")
            if self._cooldown_gate is not None:
                self._cooldown_gate.reset()
            try:
                data = resp.json()
                parts = [b["text"] for b in data["content"] if b.get("type") == "text"]
                text = "".join(parts)
            except (KeyError, TypeError, ValueError):
                last_error = f"malformed response: {resp.text[:300]}"
                continue
            if not text.strip():
                last_error = "empty completion"
                continue
            return text
        raise LLMError(f"anthropic: retries exhausted ({last_error})")

    _retry_after: Optional[float] = None


def _header_retry_after(resp: requests.Response) -> Optional[float]:
    value = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    try:
        return float(value) if value else None
    except ValueError:
        return None
