"""pdftransl — scientific PDF translation engine.

Parse PDF (layout, LaTeX formulas, tables, figures) -> Markdown,
translate with local or cloud LLM/VLM providers, verify quality,
learn via translation memory / RAG, integrate into Python backends.
"""

from pdftransl.config import PipelineConfig, ProviderConfig, get_provider_config
from pdftransl.models import (
    Asset,
    Block,
    BlockType,
    JobResult,
    ParsedDocument,
    QAIssue,
    Segment,
)
from pdftransl.pipeline import TranslationPipeline
from pdftransl.service import TranslationService

__version__ = "0.6.0"

__all__ = [
    "Asset",
    "Block",
    "BlockType",
    "JobResult",
    "ParsedDocument",
    "PipelineConfig",
    "ProviderConfig",
    "QAIssue",
    "Segment",
    "TranslationPipeline",
    "TranslationService",
    "get_provider_config",
    "__version__",
]
