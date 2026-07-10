"""Клиент любого OpenAI-совместимого /chat/completions.

OpenAI, OpenRouter, DeepSeek и локальные серверы (Ollama, vLLM,
LM Studio, llama.cpp) говорят на одном протоколе — локальный и
облачный инференс взаимозаменяемы.

Переработан в v0.18 (оптимизация и стабильность):

* один ``requests.Session`` с пулом соединений на клиент — keep-alive
  вместо нового TCP/TLS-рукопожатия на каждый сегмент (на облачных
  провайдерах это сотни лишних миллисекунд на запрос);
* раздельный таймаут соединения и чтения: мёртвый хост обнаруживается
  за секунды, а не по истечении полного таймаута генерации;
* Retry-After уважается и без общего кулдаун-гейта;
* джиттер в бэкоффе — N параллельных воркеров не бьют повторами в одну
  и ту же миллисекунду;
* битый JSON и пустой ответ (обрезанный стрим перегруженного сервера)
  ретраятся, а не мгновенно валят сегмент.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from pdftransl.config import ProviderConfig
from pdftransl.exceptions import LLMError
from pdftransl.llm.base import BaseLLMClient, Message

logger = logging.getLogger(__name__)

_RETRIABLE = {408, 429, 500, 502, 503, 504}
# Соединение должно устанавливаться быстро даже когда генерация долгая:
# отдельный небольшой лимит на connect против «мёртвый сервер держит
# сегмент все 300 секунд таймаута чтения».
_CONNECT_TIMEOUT = 15.0


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


def _make_session(pool_size: int = 32) -> requests.Session:
    """Session с расширенным пулом keep-alive-соединений: параллельные
    воркеры переиспользуют TCP/TLS вместо рукопожатия на каждый запрос."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=pool_size)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


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
        self._session = _make_session()

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
        retry_after_hint: Optional[float] = None
        # config.timeout читается на КАЖДОЙ попытке, не кэшируется на
        # клиенте: OCR-бэкенд временно ужимает его через _ocr_client_budget.
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                delay = min(2.0 ** attempt, 30.0)
                if retry_after_hint and retry_after_hint > 0:
                    # провайдер сам сказал, когда возвращаться — уважаем
                    delay = max(delay, min(retry_after_hint, 60.0))
                    retry_after_hint = None
                # джиттер ±25%: параллельные воркеры не ретраят синхронно
                delay *= 0.75 + random.random() * 0.5
                logger.warning("LLM retry %d in %.1fs (%s)", attempt, delay, last_error)
                time.sleep(delay)
            if self.cooldown_gate is not None:
                self.cooldown_gate.wait()   # global pause after someone's 429
            if self.rate_limiter is not None:
                self.rate_limiter.wait()
            started = time.monotonic()
            read_timeout = self.config.timeout
            try:
                resp = self._session.post(
                    url, json=payload, headers=self._headers,
                    timeout=(min(_CONNECT_TIMEOUT, read_timeout), read_timeout),
                )
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                logger.debug("LLM %s attempt %d network error after %.1fs: %s",
                             self.model, attempt + 1, time.monotonic() - started, exc)
                continue
            duration = time.monotonic() - started
            if resp.status_code == 429:
                retry_after_hint = _retry_after_seconds(resp)
                if self.cooldown_gate is not None:
                    self.cooldown_gate.trip(retry_after_hint)
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
            except (KeyError, IndexError, TypeError, ValueError):
                # Обрезанный/битый JSON — типичный симптом перегруженного
                # локального сервера. Ретраябельно, не фатально.
                last_error = f"malformed response: {resp.text[:300]}"
                logger.debug("LLM %s attempt %d malformed response in %.1fs",
                             self.model, attempt + 1, duration)
                continue
            if content is None or (isinstance(content, str) and not content.strip()):
                # Пустой ответ без ошибки — зависший/оборванный стрим;
                # тоже пробуем ещё раз, прежде чем сдаться.
                last_error = "empty completion"
                continue
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
