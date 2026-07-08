"""Resource monitoring: memory pressure, stalls and non-responses.

Local pipelines run heavy things back to back — a parser model (MinerU,
Nougat) and then a translation model loaded into Ollama. If they overlap
in RAM the machine OOMs (MinerU + a fresh Ollama model load can eat tens
of GB and crash). This module lets the pipeline:

- read available memory cross-platform (psutil if present, else
  /proc/meminfo on Linux or vm_stat on macOS);
- wait for memory to free before a heavy stage (the OOM fix — don't load
  the translation model while the parser still holds its memory);
- watch long operations for stalls (a parser or LLM that stops
  responding) via a lightweight watchdog thread.

Everything degrades gracefully: if memory can't be read, guards become
no-ops and log once, so the pipeline never breaks because a metric is
unavailable.
"""

from __future__ import annotations

import gc
import logging
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_warned_no_metric = False


@dataclass
class MemoryStats:
    total_mb: float
    available_mb: float

    @property
    def used_pct(self) -> float:
        if self.total_mb <= 0:
            return 0.0
        return 100.0 * (1 - self.available_mb / self.total_mb)

    def to_dict(self) -> dict:
        return {
            "total_mb": round(self.total_mb),
            "available_mb": round(self.available_mb),
            "used_pct": round(self.used_pct, 1),
        }


def _from_psutil() -> Optional[MemoryStats]:
    try:
        import psutil
    except ImportError:
        return None
    vm = psutil.virtual_memory()
    return MemoryStats(vm.total / 1e6, vm.available / 1e6)


def _from_proc_meminfo() -> Optional[MemoryStats]:
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            info = {}
            for line in fh:
                parts = line.split(":")
                if len(parts) == 2:
                    info[parts[0].strip()] = parts[1].strip()
    except OSError:
        return None
    total = _kb(info.get("MemTotal"))
    avail = _kb(info.get("MemAvailable")) or _kb(info.get("MemFree"))
    if total is None or avail is None:
        return None
    return MemoryStats(total / 1000.0, avail / 1000.0)


def _kb(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = re.match(r"(\d+)", value)
    return float(m.group(1)) if m else None


def _from_vm_stat() -> Optional[MemoryStats]:
    """macOS: total from sysctl, free+inactive from vm_stat."""
    try:
        total = int(subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
        ).stdout.strip())
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    page = 4096
    m = re.search(r"page size of (\d+)", out)
    if m:
        page = int(m.group(1))
    def pages(name: str) -> int:
        mm = re.search(rf"{name}:\s+(\d+)", out)
        return int(mm.group(1)) if mm else 0
    free_pages = pages("Pages free") + pages("Pages inactive") + pages("Pages purgeable")
    return MemoryStats(total / 1e6, free_pages * page / 1e6)


def memory_stats() -> Optional[MemoryStats]:
    """Best available memory reading, or None if none work."""
    global _warned_no_metric
    for provider in (_from_psutil, _from_proc_meminfo, _from_vm_stat):
        stats = provider()
        if stats is not None:
            return stats
    if not _warned_no_metric:
        _warned_no_metric = True
        logger.info("memory metrics unavailable (install psutil for cross-platform "
                    "support); memory guards disabled")
    return None


def wait_for_memory(
    min_free_mb: float,
    timeout: float,
    label: str = "",
    poll: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> MemoryStats | None:
    """Block until at least ``min_free_mb`` is free or ``timeout`` passes.

    Runs a GC first (frees Python-held buffers from a finished stage).
    Returns the final MemoryStats, or None if memory can't be measured.
    Never raises — a memory guard must not itself break the pipeline.
    """
    if min_free_mb <= 0:
        return memory_stats()
    gc.collect()
    stats = memory_stats()
    if stats is None:
        return None
    deadline = time.monotonic() + max(timeout, 0)
    waited = False
    while stats.available_mb < min_free_mb and time.monotonic() < deadline:
        if not waited:
            waited = True
            logger.warning(
                "%slow memory: %.0f MB free < %.0f MB needed; waiting up to %.0fs "
                "for it to free (avoids OOM when the parser and model overlap)",
                f"[{label}] " if label else "", stats.available_mb, min_free_mb, timeout,
            )
        sleep(poll)
        gc.collect()
        stats = memory_stats()
        if stats is None:
            return None
    if waited:
        if stats.available_mb >= min_free_mb:
            logger.info("%smemory recovered: %.0f MB free",
                        f"[{label}] " if label else "", stats.available_mb)
        else:
            logger.warning(
                "%sstill low on memory after %.0fs (%.0f MB free) — proceeding "
                "anyway; a crash here means the machine is out of RAM",
                f"[{label}] " if label else "", timeout, stats.available_mb,
            )
    return stats


class Watchdog:
    """Flag an operation that stops making progress (a hung parser/LLM).

    Call ``beat()`` whenever progress happens; if no beat arrives within
    ``stall_seconds`` the ``on_stall`` callback fires (once per stall).
    Used to turn a silent hang into a visible warning instead of an
    indefinite wait.
    """

    def __init__(
        self,
        stall_seconds: float,
        on_stall: Callable[[float], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.stall_seconds = stall_seconds
        self._on_stall = on_stall
        self._clock = clock
        self._last = clock()
        self._stalled = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def beat(self) -> None:
        self._last = self._clock()
        self._stalled = False

    def _run(self) -> None:
        while not self._stop.wait(min(self.stall_seconds, 5.0)):
            idle = self._clock() - self._last
            if idle >= self.stall_seconds and not self._stalled:
                self._stalled = True
                try:
                    self._on_stall(idle)
                except Exception:  # a watchdog must never crash its target
                    logger.debug("watchdog callback raised", exc_info=True)

    def __enter__(self) -> "Watchdog":
        # the clock starts when monitoring starts, not at construction —
        # otherwise setup time before `with` counts toward the first stall
        self._last = self._clock()
        self._stalled = False
        if self.stall_seconds > 0:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="pdftransl-watchdog")
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
