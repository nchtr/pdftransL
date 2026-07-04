"""Quality control: deterministic validators and LLM review pass."""

from pdftransl.quality.validators import validate_segment, document_report
from pdftransl.quality.reviewer import Reviewer

__all__ = ["Reviewer", "document_report", "validate_segment"]
