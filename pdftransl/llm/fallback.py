"""Цепочка провайдеров: упал основной — пробуем следующий.

Один RateLimiter/CooldownGate на всю цепочку, чтобы фолбэк не удваивал
нагрузку на квоты.

С v0.18 цепочка «липкая»: последний рабочий провайдер запоминается и
следующие запросы начинаются с него. Без этого мёртвый основной
провайдер заново проходил ПОЛНЫЙ цикл своих ретраев с бэкоффом на
каждом сегменте документа — сотни сегментов превращались в часы
ожидания заведомых отказов. Периодически (раз в
``primary_retry_seconds``) цепочка пробует вернуться на основной —
восстановившийся провайдер снова становится первым.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from pdftransl.exceptions import LLMError
from pdftransl.llm.base import BaseLLMClient, Message

logger = logging.getLogger(__name__)


class FallbackClient(BaseLLMClient):
    def __init__(
        self,
        clients: list[BaseLLMClient],
        primary_retry_seconds: float = 300.0,
    ):
        if not clients:
            raise LLMError("FallbackClient needs at least one client")
        self.clients = clients
        self.model = clients[0].model
        self.supports_vision = any(c.supports_vision for c in clients)
        self._primary_retry = primary_retry_seconds
        self._lock = threading.Lock()
        self._active = 0        # индекс провайдера, которому сейчас доверяем
        self._demoted_at = 0.0  # когда основной был разжалован

    def _start_index(self) -> int:
        """С кого начинать: с доверенного, либо — по истечении окна —
        снова с основного (проба восстановления)."""
        with self._lock:
            if self._active and (
                time.monotonic() - self._demoted_at >= self._primary_retry
            ):
                logger.info(
                    "Fallback chain: probing the primary provider again "
                    "after %.0fs on '%s'",
                    self._primary_retry, self.clients[self._active].model,
                )
                self._active = 0
            return self._active

    def _mark_healthy(self, index: int) -> None:
        with self._lock:
            if index != self._active:
                self._active = index
                self._demoted_at = time.monotonic() if index else 0.0

    def chat(
        self,
        messages: list[Message],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        response_format: Optional[dict] = None,
    ) -> str:
        start = self._start_index()
        # доверенный первым, затем остальные по порядку цепочки; ранее
        # разжалованные (до start) — последним шансом
        order = list(range(start, len(self.clients))) + list(range(start))
        last_exc: Optional[Exception] = None
        for index in order:
            client = self.clients[index]
            try:
                result = client.chat(
                    messages, temperature=temperature,
                    max_tokens=max_tokens, response_format=response_format,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                # ловим не только LLMError: неожиданное исключение одного
                # клиента (баг, сюрприз провайдера) не должно ронять всю
                # цепочку, пока есть живые альтернативы
                logger.warning(
                    "Provider %s failed (%s); falling back to next provider",
                    client.model, exc,
                )
                last_exc = exc
                continue
            self._mark_healthy(index)
            if index != start:
                logger.info("Fallback chain: '%s' is now the active provider",
                            client.model)
            return result
        raise LLMError(f"All providers in the fallback chain failed: {last_exc}")
