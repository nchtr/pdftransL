"""Central logging configuration, driven by environment variables.

Set ``PDFTRANSL_LOG_LEVEL=DEBUG`` to see everything the pipeline does —
every LLM call with sizes and timing, per-segment translation progress,
parser/export engine decisions — in the CLI, the bot and the Django
server alike. ``PDFTRANSL_LOG_FILE`` additionally mirrors logs into a
rotating file, which is handy when the server console scrolls away.

The level can also be changed at runtime (the web UI settings panel
does this through ``set_level``), no restart required.
"""

from __future__ import annotations

import logging
import logging.handlers
import os

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# libraries that flood DEBUG with noise irrelevant to translation work
_NOISY = ("urllib3", "PIL", "matplotlib", "fontTools", "asyncio", "httpcore", "httpx")

_configured = False


def env_level(default: str = "INFO") -> str:
    name = os.environ.get("PDFTRANSL_LOG_LEVEL", default).strip().upper()
    return name if name in ("DEBUG", "INFO", "WARNING", "ERROR") else default


def setup_logging(default: str = "INFO") -> str:
    """Configure root logging from the environment. Safe to call twice."""
    global _configured
    level_name = env_level(default)
    level = getattr(logging, level_name)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.environ.get("PDFTRANSL_LOG_FILE", "").strip()
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(level=level, format=LOG_FORMAT, handlers=handlers, force=True)

    # even in DEBUG, third-party internals stay at INFO to keep logs readable
    for name in _NOISY:
        logging.getLogger(name).setLevel(max(level, logging.INFO))
    _configured = True
    logging.getLogger(__name__).debug("logging configured at %s", level_name)
    return level_name


def set_level(level_name: str) -> str:
    """Change the level at runtime (used by the settings API)."""
    level_name = level_name.strip().upper()
    if level_name not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ValueError(f"unknown log level: {level_name}")
    level = getattr(logging, level_name)
    logging.getLogger().setLevel(level)
    logging.getLogger("pdftransl").setLevel(level)
    logging.getLogger("api").setLevel(level)
    for name in _NOISY:
        logging.getLogger(name).setLevel(max(level, logging.INFO))
    logging.getLogger(__name__).info("log level changed to %s", level_name)
    return level_name
