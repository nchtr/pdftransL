"""Публичный API пакета pdftransl.

Реэкспортирует всё, что нужно для интеграции: TranslationPipeline
(движок), TranslationService (фасад submit/process/status),
PipelineConfig (настройки) и модели данных.
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

__version__ = "0.18.0"

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
