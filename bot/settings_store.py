"""Per-chat настройки бота в JSON-файле.

Язык, форматы, провайдер, флаги — переживают перезапуск бота.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ChatSettings:
    target_lang: str = "ru"
    source_lang: str = "en"
    provider: str = ""              # "" = server default
    formats: list[str] = field(default_factory=lambda: ["docx", "pdf"])
    bilingual: bool = False
    review: bool = True


class SettingsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self._data = {}

    def get(self, chat_id: int) -> ChatSettings:
        raw = self._data.get(str(chat_id), {})
        known = {k: v for k, v in raw.items() if k in ChatSettings.__dataclass_fields__}
        return ChatSettings(**known)

    def update(self, chat_id: int, **changes) -> ChatSettings:
        with self._lock:
            settings = self.get(chat_id)
            for key, value in changes.items():
                setattr(settings, key, value)
            self._data[str(chat_id)] = asdict(settings)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        return settings
