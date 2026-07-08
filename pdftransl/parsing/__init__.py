"""Парсинг PDF: бэкенды, кэш, детекторы сканов и кракозябр.
"""

from pdftransl.parsing.base import ParserBackend, get_backend, parse_pdf
from pdftransl.parsing.splitter import split_markdown

__all__ = ["ParserBackend", "get_backend", "parse_pdf", "split_markdown"]
