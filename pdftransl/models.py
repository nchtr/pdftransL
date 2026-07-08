"""Core data structures shared across the pipeline."""

from __future__ import annotations

import dataclasses
import enum
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class BlockType(str, enum.Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CODE = "code"
    MATH = "math"          # display math / LaTeX environment
    IMAGE = "image"        # image-only line
    HTML = "html"
    OTHER = "other"


# Block types whose content is sent to the LLM for translation.
TRANSLATABLE_TYPES = {BlockType.HEADING, BlockType.PARAGRAPH, BlockType.TABLE}


@dataclass
class Block:
    """A structural unit of the parsed Markdown document."""

    type: BlockType
    text: str
    index: int
    translatable: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Asset:
    """An exported binary asset (figure, chart, embedded image)."""

    path: str                       # path on disk
    kind: str = "image"
    rel_path: Optional[str] = None  # path as referenced from the markdown
    page: Optional[int] = None
    caption: Optional[str] = None
    description: Optional[str] = None  # VLM-generated description

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ParsedDocument:
    """Result of the PDF parsing stage."""

    source_path: str
    markdown: str
    markdown_path: Optional[str] = None
    assets: list[Asset] = field(default_factory=list)
    backend: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class QAIssue:
    """A single quality problem found by validators or the reviewer."""

    code: str
    message: str
    severity: str = "warning"  # "warning" | "error"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Segment:
    """A translation unit: one or more consecutive blocks sent to the LLM
    as a single request, or a pass-through span that is copied verbatim."""

    id: str
    kind: str                      # "translate" | "pass"
    source_text: str
    block_indices: list[int] = field(default_factory=list)
    masked_text: str = ""
    placeholders: dict[str, str] = field(default_factory=dict)
    translation: Optional[str] = None
    issues: list[QAIssue] = field(default_factory=list)
    attempts: int = 0
    tm_examples: list[dict[str, str]] = field(default_factory=list)
    glossary_hits: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def final_text(self) -> str:
        """Text that goes into the assembled output document."""
        if self.kind == "pass":
            return self.source_text
        if self.translation:
            return self.translation
        # Graceful degradation: keep source. Deliberately falsy-checked, not
        # `is not None` — a stalled/overloaded LLM can "answer" with an empty
        # string (cut off mid-stream, or a hung provider returning nothing)
        # without raising, and that used to slip through as an empty segment,
        # producing a document that looked entirely blank.
        return self.source_text

    def to_dict(self) -> dict[str, Any]:
        """Serializable view for backends storing segments (review UI)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "source_text": self.source_text,
            "translation": self.translation,
            "block_indices": self.block_indices,
            "ok": self.ok,
            "attempts": self.attempts,
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass
class JobResult:
    """Outcome of a full pipeline run."""

    job_id: str
    status: str                     # "completed" | "failed" | "partial" | "paused"
    output_markdown_path: Optional[str] = None
    source_markdown_path: Optional[str] = None
    assets_dir: Optional[str] = None
    report_path: Optional[str] = None
    report: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    # format -> exported file path (html/docx/pdf), None if engine missing
    exports: dict[str, Optional[str]] = field(default_factory=dict)
    # serialized segments for backends that store them (review UI)
    segments: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, include_segments: bool = False) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if not include_segments:
            data.pop("segments", None)
        return data

    def save_report(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"
