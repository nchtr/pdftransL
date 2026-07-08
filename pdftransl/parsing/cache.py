"""Кэш результатов парсинга по SHA-256 содержимого PDF.

Повторная загрузка того же файла (или ретрай упавшей задачи) не
парсится заново — на MinerU это экономит десятки минут.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from pdftransl.models import Asset, ParsedDocument

logger = logging.getLogger(__name__)


def pdf_hash(pdf_path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(pdf_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ParseCache:
    def __init__(self, root: str | Path):
        self.root = Path(root) / ".parse_cache"

    def _entry_dir(self, key: str, backend: str) -> Path:
        return self.root / f"{key}_{backend}"

    def get(self, pdf_path: str | Path, backend: str) -> Optional[ParsedDocument]:
        entry = self._entry_dir(pdf_hash(pdf_path), backend)
        meta_path = entry / "cache.json"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            markdown = (entry / "document.md").read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        assets = []
        for item in meta.get("assets", []):
            path = entry / item["rel_path"]
            if path.exists():
                assets.append(Asset(path=str(path), rel_path=item["rel_path"],
                                    kind=item.get("kind", "image")))
        logger.info("Parse cache hit for %s (%s)", Path(pdf_path).name, backend)
        return ParsedDocument(
            source_path=str(pdf_path),
            markdown=markdown,
            markdown_path=str(entry / "document.md"),
            assets=assets,
            backend=backend,
            meta={"cache": "hit"},
        )

    def put(self, pdf_path: str | Path, parsed: ParsedDocument) -> None:
        entry = self._entry_dir(pdf_hash(pdf_path), parsed.backend)
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "document.md").write_text(parsed.markdown, encoding="utf-8")
        stored_assets = []
        for asset in parsed.assets:
            src = Path(asset.path)
            rel = asset.rel_path or src.name
            if not src.exists():
                continue
            dst = entry / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            stored_assets.append({"rel_path": rel, "kind": asset.kind})
        (entry / "cache.json").write_text(
            json.dumps({"assets": stored_assets, "backend": parsed.backend},
                       ensure_ascii=False),
            encoding="utf-8",
        )
