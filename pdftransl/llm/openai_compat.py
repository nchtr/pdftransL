"""Client for any OpenAI-compatible /chat/completions endpoint.

Covers OpenAI, OpenRouter, DeepSeek and local servers (Ollama, vLLM,
LM Studio, llama.cpp server, LocalAI) — they all speak the same
protocol, so local and cloud inference are interchangeable here.

At DEBUG level every call is logged with sizes, duration and (when the
provider reports it) token usage — set PDFTRANSL_LOG_LEVEL=DEBUG to
watch the pipeline work in real time.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from pdftransl.config import ProviderConfig
from pdftransl.exceptions import LLMError
from pdftransl.llm.base import BaseLLMClient, Message

logger = logging.getLogger(__name__)

_RETRIABLE = {429, 500, 502, 503, 504}


def _content_chars(messages: list[Message]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total += len(part.get("text", ""))
                else:
                    total += 64  # count an image part as a token-ish stub
    return total


def _retry_after_seconds(resp: requests.Response) -> Optional[float]:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # HTTP-date form — let the gate use its own penalty


class OpenAICompatClient(BaseLLMClient):
    def __init__(self, config: ProviderConfig, rate_limiter=None, cooldown_gate=None):
        self.config = config
        self.model = config.model
        self.supports_vision = config.supports_vision
        self.rate_limiter = rate_limiter
        self.cooldown_gate = cooldown_gate
        key = config.resolve_api_key()
        if not key and not config.is_local:
            raise LLMError(
                f"No API key for provider '{config.name}' "
                f"(set env var {config.api_key_env})."
            )
        self._headers = {"Content-Type": "application/json"}
        if key:
            self._headers["Authorization"] = f"Bearer {key}"
        self._headers.update(config.extra_headers)

    def chat(
        self,
        messages: list[Message],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format

        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        chars_in = _content_chars(messages)
        last_error: Optional[str] = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                delay = min(2 ** attempt, 30)
                logger.warning("LLM retry %d in %ds (%s)", attempt, delay, last_error)
                time.sleep(delay)
            if self.cooldown_gate is not None:
                self.cooldown_gate.wait()   # global pause after someone's 429
            if self.rate_limiter is not None:
                self.rate_limiter.wait()
            started = time.monotonic()
            try:
                resp = requests.post(
                    url, json=payload, headers=self._headers,
                    timeout=self.config.timeout,
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                logger.debug("LLM %s attempt %d network error after %.1fs: %s",
                             self.model, attempt + 1, time.monotonic() - started, exc)
                continue
            duration = time.monotonic() - started
            if resp.status_code == 429 and self.cooldown_gate is not None:
                self.cooldown_gate.trip(_retry_after_seconds(resp))
            if resp.status_code in _RETRIABLE:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                logger.debug("LLM %s attempt %d -> HTTP %d in %.1fs",
                             self.model, attempt + 1, resp.status_code, duration)
                continue
            if resp.status_code != 200:
                raise LLMError(
                    f"{self.config.name} HTTP {resp.status_code}: {resp.text[:500]}"
                )
            try:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(
                    f"{self.config.name}: malformed response: {resp.text[:500]}"
                ) from exc
            if content is None:
                raise LLMError(f"{self.config.name}: empty completion")
            if self.cooldown_gate is not None:
                self.cooldown_gate.reset()
            if logger.isEnabledFor(logging.DEBUG):
                usage = data.get("usage") or {}
                logger.debug(
                    "LLM %s: %d msgs / %d chars in -> %d chars out, %.1fs%s",
                    self.model, len(messages), chars_in, len(content), duration,
                    (f", tokens {usage.get('prompt_tokens')}+"
                     f"{usage.get('completion_tokens')}") if usage else "",
                )
            return content
        raise LLMError(f"{self.config.name}: retries exhausted ({last_error})")
