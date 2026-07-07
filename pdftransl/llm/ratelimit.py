"""Thread-safe request throttling: a fixed rpm budget and an adaptive
cooldown gate.

Free tiers of cloud providers (and small local servers) choke when a
parallel pipeline fires 8 requests at once. One limiter/gate instance
is shared by all clients in a fallback chain, so the whole pipeline
respects a single budget.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class RateLimiter:
    """Fixed budget: at most ``rpm`` requests per minute."""

    def __init__(self, rpm: int, clock=time.monotonic, sleep=time.sleep):
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.interval = 60.0 / rpm
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self) -> None:
        """Block until a request slot is available."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.interval
        delay = slot - now
        if delay > 0:
            self._sleep(delay)


class CooldownGate:
    """Adaptive rate-limit response: pause *all* workers on HTTP 429.

    With N parallel translators a 429 means every thread is about to hit
    the same wall; per-thread retries just burn the quota faster. The
    gate is shared: one 429 trips a global cooldown (honouring
    Retry-After when the provider sends it, else an exponentially
    growing penalty), every thread's next request waits it out, and a
    successful request shrinks the penalty back.
    """

    def __init__(
        self,
        base: float = 15.0,
        max_cooldown: float = 120.0,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self._base = base
        self._max = max_cooldown
        self._penalty = base
        self._deadline = 0.0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block while a cooldown is active."""
        while True:
            with self._lock:
                remaining = self._deadline - self._clock()
            if remaining <= 0:
                return
            self._sleep(min(remaining, 1.0))

    def trip(self, retry_after: float | None = None) -> float:
        """Register a 429; returns the cooldown applied (seconds)."""
        with self._lock:
            cooldown = retry_after if retry_after and retry_after > 0 else self._penalty
            cooldown = min(cooldown, self._max)
            self._penalty = min(self._penalty * 2, self._max)
            self._deadline = max(self._deadline, self._clock() + cooldown)
        logger.warning("Rate limited (429): pausing all requests for %.0fs", cooldown)
        return cooldown

    def reset(self) -> None:
        """A request succeeded — relax the penalty back to base."""
        with self._lock:
            self._penalty = self._base
