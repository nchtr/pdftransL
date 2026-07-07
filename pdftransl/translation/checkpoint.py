"""Per-document translation checkpoint for resumable jobs.

A large document that fails halfway (MinerU crash, provider outage,
process kill) shouldn't have to re-translate everything on the next
run. The pipeline writes each finished segment to a checkpoint file
next to the output; a re-run loads it and skips segments already
done — independent of the translation memory (which only keeps
successful, ``learn``-enabled segments and is keyed globally).

The file is an append-only JSONL log (one record per segment), so a
crash mid-write loses at most the last line. It is keyed by the source
text hash plus the language pair, so it is safe to reuse across runs of
the same document and ignores stale entries from a different target
language.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _key(source_text: str, src_lang: str, tgt_lang: str) -> str:
    digest = hashlib.sha256(
        f"{src_lang}>{tgt_lang}\x00{source_text.strip()}".encode("utf-8")
    ).hexdigest()
    return digest[:24]


class Checkpoint:
    """Thread-safe append-only cache of finished segment translations."""

    def __init__(self, path: str | Path, src_lang: str, tgt_lang: str):
        self.path = Path(path)
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang
        self._lock = threading.Lock()
        self._done: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        loaded = 0
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # torn last line after a crash — skip it
                if rec.get("k") and rec.get("t") is not None:
                    self._done[rec["k"]] = rec["t"]
                    loaded += 1
        except OSError as exc:  # pragma: no cover
            logger.warning("checkpoint load failed: %s", exc)
        if loaded:
            logger.info("resuming: %d segment(s) loaded from checkpoint %s",
                        loaded, self.path.name)

    def get(self, source_text: str) -> Optional[str]:
        return self._done.get(_key(source_text, self.src_lang, self.tgt_lang))

    def put(self, source_text: str, translation: str) -> None:
        key = _key(source_text, self.src_lang, self.tgt_lang)
        with self._lock:
            if key in self._done:
                return
            self._done[key] = translation
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"k": key, "t": translation},
                                    ensure_ascii=False) + "\n")

    @property
    def count(self) -> int:
        return len(self._done)

    def clear(self) -> None:
        with self._lock:
            self._done.clear()
            if self.path.exists():
                self.path.unlink()
