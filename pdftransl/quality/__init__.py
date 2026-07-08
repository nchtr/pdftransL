"""Контроль качества: валидаторы, ревью, судья, бэк-перевод, LaTeX.
"""

from pdftransl.quality.validators import validate_segment, document_report
from pdftransl.quality.reviewer import Reviewer

__all__ = ["Reviewer", "document_report", "validate_segment"]
