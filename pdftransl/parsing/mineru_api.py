"""Облачный API MinerU (mineru.net).

Загрузка PDF -> опрос статуса задачи -> скачивание ZIP с Markdown.
Нужен MINERU_API_KEY; удобно, когда локальный MinerU не потянуть.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from pathlib import Path

import requests

from pdftransl.config import PipelineConfig
from pdftransl.exceptions import ParserError
from pdftransl.models import ParsedDocument
from pdftransl.parsing.base import ParserBackend, collect_assets, mineru_api_key

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10.0
POLL_TIMEOUT = 1800.0


class MineruApiBackend(ParserBackend):
    name = "mineru_api"

    def __init__(self, config: PipelineConfig):
        self.config = config

    def available(self) -> bool:
        return bool(mineru_api_key(self.config))

    # -- helpers -----------------------------------------------------
    def _headers(self) -> dict[str, str]:
        key = mineru_api_key(self.config)
        return {"Authorization": f"Bearer {key}"}

    def _request_upload(self, pdf_path: Path) -> tuple[str, str]:
        """Returns (batch_id, upload_url)."""
        url = f"{self.config.mineru_api_base}/file-urls/batch"
        payload = {
            "files": [{"name": pdf_path.name, "is_ocr": True}],
            "enable_formula": True,
            "enable_table": True,
            "language": "en",
        }
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=60)
        self._check(resp, "request upload url")
        data = resp.json().get("data", {})
        urls = data.get("file_urls") or []
        batch_id = data.get("batch_id")
        if not urls or not batch_id:
            raise ParserError(f"MinerU API: unexpected upload response: {resp.text[:500]}")
        return batch_id, urls[0]

    def _poll(self, batch_id: str) -> dict:
        url = f"{self.config.mineru_api_base}/extract-results/batch/{batch_id}"
        deadline = time.monotonic() + POLL_TIMEOUT
        while time.monotonic() < deadline:
            resp = requests.get(url, headers=self._headers(), timeout=60)
            self._check(resp, "poll results")
            results = resp.json().get("data", {}).get("extract_result", [])
            if results:
                item = results[0]
                state = item.get("state")
                if state == "done":
                    return item
                if state == "failed":
                    raise ParserError(f"MinerU API extraction failed: {item.get('err_msg')}")
            time.sleep(POLL_INTERVAL)
        raise ParserError("MinerU API: extraction timed out")

    @staticmethod
    def _check(resp: requests.Response, what: str) -> None:
        if resp.status_code != 200:
            raise ParserError(
                f"MinerU API error during {what}: HTTP {resp.status_code} {resp.text[:500]}"
            )
        body = resp.json()
        if body.get("code") not in (0, 200, None):
            raise ParserError(f"MinerU API error during {what}: {body}")

    # -- main --------------------------------------------------------
    def parse(self, pdf_path: str | Path, workdir: str | Path) -> ParsedDocument:
        pdf_path = Path(pdf_path)
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        if not pdf_path.exists():
            raise ParserError(f"PDF not found: {pdf_path}")

        logger.info("MinerU API: uploading %s", pdf_path.name)
        batch_id, upload_url = self._request_upload(pdf_path)
        with open(pdf_path, "rb") as fh:
            put = requests.put(upload_url, data=fh, timeout=600)
        if put.status_code not in (200, 201):
            raise ParserError(f"MinerU API: upload failed HTTP {put.status_code}")

        logger.info("MinerU API: waiting for extraction (batch %s)", batch_id)
        item = self._poll(batch_id)
        zip_url = item.get("full_zip_url")
        if not zip_url:
            raise ParserError(f"MinerU API: no result archive in response: {item}")

        resp = requests.get(zip_url, timeout=600)
        if resp.status_code != 200:
            raise ParserError(f"MinerU API: failed to download results HTTP {resp.status_code}")
        extract_dir = workdir / f"{pdf_path.stem}_mineru"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(extract_dir)

        md_files = sorted(
            extract_dir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True
        )
        if not md_files:
            raise ParserError(f"MinerU API: no markdown in result archive {extract_dir}")
        md_path = md_files[0]
        markdown = md_path.read_text(encoding="utf-8")
        assets = collect_assets(md_path.parent, markdown)
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(md_path),
            assets=assets,
            backend=self.name,
            meta={"batch_id": batch_id},
        )
