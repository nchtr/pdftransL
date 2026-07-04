"""SQLite job repository.

Tracks translation jobs (status, stage, progress, result paths,
QA report). Framework-agnostic: a Django view, a Celery worker and
the CLI all read/write the same rows, which is what makes the
submit/status/result API possible without a message broker.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from pdftransl.exceptions import JobNotFoundError
from pdftransl.models import new_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,             -- queued | running | completed | partial | failed
    stage TEXT,                       -- parse | translate | review | assemble
    progress REAL DEFAULT 0,          -- 0..1
    pdf_path TEXT,
    output_dir TEXT,
    source_lang TEXT,
    target_lang TEXT,
    result_json TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class JobRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create(
        self,
        pdf_path: str,
        output_dir: str,
        source_lang: str,
        target_lang: str,
        job_id: Optional[str] = None,
    ) -> str:
        job_id = job_id or new_id("job_")
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, status, pdf_path, output_dir, source_lang, "
                "target_lang, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, "queued", pdf_path, output_dir,
                 source_lang, target_lang, now, now),
            )
        return job_id

    def update(
        self,
        job_id: str,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress: Optional[float] = None,
        result: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        sets = ["updated_at=?"]
        params: list[Any] = [time.time()]
        for column, value in (
            ("status", status), ("stage", stage), ("progress", progress),
            ("error", error),
        ):
            if value is not None:
                sets.append(f"{column}=?")
                params.append(value)
        if result is not None:
            sets.append("result_json=?")
            params.append(json.dumps(result, ensure_ascii=False))
        params.append(job_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", params
            )
            if cur.rowcount == 0:
                raise JobNotFoundError(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        job = dict(row)
        if job.get("result_json"):
            job["result"] = json.loads(job.pop("result_json"))
        else:
            job.pop("result_json", None)
            job["result"] = None
        return job

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, status, stage, progress, pdf_path, created_at, updated_at "
                "FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
