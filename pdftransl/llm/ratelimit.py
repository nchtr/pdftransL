"""Simple thread-safe request-rate limiter.

Free tiers of cloud providers (and small local servers) choke when a
parallel pipeline fires 8 requests at once. One limiter instance is
shared by all clients in a fallback chain, so the whole pipeline
respects a single requests-per-minute budget.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
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
