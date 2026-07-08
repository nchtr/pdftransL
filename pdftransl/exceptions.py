"""Иерархия исключений pdftransl.

Всё наследуется от PdftranslError, чтобы интеграции могли ловить одно
исключение; парсинг и LLM разведены по своим веткам.
"""


class PdfTranslError(Exception):
    """Base error for the pdftransl package."""


class ParserError(PdfTranslError):
    """PDF parsing backend failed."""


class ParserUnavailableError(ParserError):
    """Requested parsing backend is not installed / not configured."""


class LLMError(PdfTranslError):
    """LLM provider call failed."""


class ConfigError(PdfTranslError):
    """Invalid or missing configuration."""


class JobNotFoundError(PdfTranslError):
    """Job id is unknown to the repository."""
