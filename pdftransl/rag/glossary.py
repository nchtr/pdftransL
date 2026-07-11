"""Глоссарий терминов в SQLite.

Принудительные переводы терминологии: ручные записи, CSV-импорт,
авто-пополнение из коротких правок человека. match() находит термины,
встречающиеся в конкретном сегменте.
"""

from __future__ import annotations

import csv
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _closing_conn(conn):
    try:
        with conn:
            yield conn
    finally:
        conn.close()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS glossary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    translation TEXT NOT NULL,
    src_lang TEXT NOT NULL,
    tgt_lang TEXT NOT NULL,
    notes TEXT,
    UNIQUE (term, src_lang, tgt_lang)
);
"""


class Glossary:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return _closing_conn(conn)

    def add(
        self,
        term: str,
        translation: str,
        src_lang: str,
        tgt_lang: str,
        notes: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO glossary (term, translation, src_lang, tgt_lang, notes) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT (term, src_lang, tgt_lang) "
                "DO UPDATE SET translation=excluded.translation, notes=excluded.notes",
                (term.strip(), translation.strip(), src_lang, tgt_lang, notes),
            )

    def load_csv(self, path: str | Path, src_lang: str, tgt_lang: str) -> int:
        """Load 'term,translation[,notes]' rows from a CSV file."""
        count = 0
        with open(path, encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if len(row) < 2 or row[0].startswith("#"):
                    continue
                self.add(row[0], row[1], src_lang, tgt_lang,
                         row[2] if len(row) > 2 else None)
                count += 1
        return count

    def match(self, text: str, src_lang: str, tgt_lang: str, limit: int = 30) -> list[dict]:
        """Return glossary entries whose term occurs in ``text``."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT term, translation FROM glossary WHERE src_lang=? AND tgt_lang=?",
                (src_lang, tgt_lang),
            ).fetchall()
        lowered = text.lower()
        hits = []
        for row in rows:
            term = row["term"]
            if re.search(r"(?<![\w-])" + re.escape(term.lower()) + r"(?![\w-])", lowered):
                hits.append({"term": term, "translation": row["translation"]})
            if len(hits) >= limit:
                break
        return hits

    def remove(self, term: str, src_lang: str, tgt_lang: str) -> bool:
        """Delete a term; returns True if something was removed."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM glossary WHERE term=? AND src_lang=? AND tgt_lang=?",
                (term.strip(), src_lang, tgt_lang),
            )
            return cur.rowcount > 0

    def list_all(self, src_lang: str | None = None, tgt_lang: str | None = None) -> list[dict]:
        query = "SELECT term, translation, src_lang, tgt_lang, notes FROM glossary"
        params: tuple = ()
        if src_lang and tgt_lang:
            query += " WHERE src_lang=? AND tgt_lang=?"
            params = (src_lang, tgt_lang)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]
