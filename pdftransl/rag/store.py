"""Translation memory (TM) — the "learning" storage.

Every approved segment translation is stored with its embedding.
Future documents retrieve similar past segments as few-shot examples
(RAG) and reuse exact matches directly. Human corrections added via
``origin='human'`` take priority over automatic entries.

SQLite keeps everything in one file; vectors are stored as JSON blobs
and compared in Python — fine up to tens of thousands of segments.
Swap in sqlite-vec/pgvector/Qdrant behind the same interface to scale.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from pdftransl.rag.embeddings import BaseEmbedder, cosine

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tm_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    src_lang TEXT NOT NULL,
    tgt_lang TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'auto',      -- auto | human
    quality REAL,
    doc_id TEXT,
    domain TEXT NOT NULL DEFAULT '',          -- subject area filter

    embedding TEXT,                           -- JSON list[float]
    embedder TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tm_hash
    ON tm_segments (source_hash, src_lang, tgt_lang);
CREATE INDEX IF NOT EXISTS idx_tm_langs
    ON tm_segments (src_lang, tgt_lang);
"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _batch_cosine(query: list[float], vectors: list[list[float]]) -> list[float]:
    """Cosine of `query` against many vectors; numpy path when available
    (worth it from a few thousand TM segments onward)."""
    dim = len(query)
    try:
        import numpy as np

        matrix = np.array(
            [v if len(v) == dim else [0.0] * dim for v in vectors],
            dtype=np.float32,
        )
        if not len(matrix):
            return []
        q = np.array(query, dtype=np.float32)
        q_norm = np.linalg.norm(q) or 1.0
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0
        return (matrix @ q / (norms * q_norm)).tolist()
    except ImportError:
        return [cosine(query, v) for v in vectors]


class TranslationMemory:
    def __init__(self, db_path: str | Path, embedder: BaseEmbedder):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # migrate pre-domain databases
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tm_segments)")]
            if "domain" not in cols:
                conn.execute(
                    "ALTER TABLE tm_segments ADD COLUMN domain TEXT NOT NULL DEFAULT ''"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- writing (learning) -------------------------------------------
    def add(
        self,
        source: str,
        target: str,
        src_lang: str,
        tgt_lang: str,
        origin: str = "auto",
        quality: Optional[float] = None,
        doc_id: Optional[str] = None,
        domain: str = "",
    ) -> None:
        source = source.strip()
        target = target.strip()
        if not source or not target:
            return
        vector = self.embedder.embed([source])[0]
        with self._lock, self._connect() as conn:
            # human corrections replace earlier auto entries for the same source
            if origin == "human":
                conn.execute(
                    "DELETE FROM tm_segments WHERE source_hash=? AND src_lang=? "
                    "AND tgt_lang=? AND origin='auto'",
                    (_hash(source), src_lang, tgt_lang),
                )
            conn.execute(
                "INSERT INTO tm_segments (source_hash, source, target, src_lang, "
                "tgt_lang, origin, quality, doc_id, domain, embedding, embedder, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _hash(source), source, target, src_lang, tgt_lang,
                    origin, quality, doc_id, domain,
                    json.dumps(vector), self.embedder.name, time.time(),
                ),
            )

    # -- reading (retrieval) -------------------------------------------
    def exact_match(
        self, source: str, src_lang: str, tgt_lang: str
    ) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT target FROM tm_segments WHERE source_hash=? AND src_lang=? "
                "AND tgt_lang=? ORDER BY origin='human' DESC, created_at DESC LIMIT 1",
                (_hash(source.strip()), src_lang, tgt_lang),
            ).fetchone()
        return row["target"] if row else None

    def search(
        self,
        query: str,
        src_lang: str,
        tgt_lang: str,
        top_k: int = 3,
        min_similarity: float = 0.8,
        domain: Optional[str] = None,
    ) -> list[dict]:
        """Cosine search over stored segments of the same language pair
        and the same embedder (vectors from different embedders are
        incomparable). ``domain`` restricts to one subject area."""
        query_vec = self.embedder.embed([query])[0]
        sql = (
            "SELECT source, target, origin, embedding FROM tm_segments "
            "WHERE src_lang=? AND tgt_lang=? AND embedder=?"
        )
        params: list = [src_lang, tgt_lang, self.embedder.name]
        if domain:
            sql += " AND domain IN ('', ?)"
            params.append(domain)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        vectors = [
            json.loads(row["embedding"]) if row["embedding"] else []
            for row in rows
        ]
        similarities = _batch_cosine(query_vec, vectors)
        scored = []
        for row, sim in zip(rows, similarities):
            if sim >= min_similarity:
                scored.append({
                    "source": row["source"],
                    "target": row["target"],
                    "origin": row["origin"],
                    "similarity": sim,
                })
        scored.sort(key=lambda r: (r["origin"] == "human", r["similarity"]), reverse=True)
        return scored[:top_k]

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM tm_segments").fetchone()["n"]
            human = conn.execute(
                "SELECT COUNT(*) AS n FROM tm_segments WHERE origin='human'"
            ).fetchone()["n"]
        return {"segments": total, "human_corrections": human}

    def export_jsonl(self, path: str | Path) -> int:
        """Dump TM as JSONL — e.g. as a fine-tuning dataset."""
        count = 0
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn, open(path, "w", encoding="utf-8") as fh:
            for row in conn.execute(
                "SELECT source, target, src_lang, tgt_lang, origin FROM tm_segments"
            ):
                fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                count += 1
        return count

    def maybe_autoexport(self, every: int, path: str | Path) -> Optional[int]:
        """Export the fine-tuning dataset each time the memory crosses a
        new multiple of ``every`` segments. A sidecar file tracks the
        last export so it fires once per threshold, not every run.
        Returns the exported count when it fired, else None."""
        if every <= 0:
            return None
        total = self.stats()["segments"]
        marker = Path(str(path) + ".mark")
        last = 0
        if marker.exists():
            try:
                last = int(marker.read_text().strip() or "0")
            except (OSError, ValueError):
                last = 0
        if total // every <= last // every:
            return None
        count = self.export_jsonl(path)
        try:
            marker.write_text(str(total), encoding="utf-8")
        except OSError:
            pass
        logger.info("TM auto-export: %d segments -> %s (fine-tuning dataset)",
                    count, path)
        return count
