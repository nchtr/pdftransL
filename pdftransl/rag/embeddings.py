"""Pluggable embedders for the translation memory.

Three tiers so RAG always works:
- ``HashingEmbedder`` — zero-dependency character-n-gram hashing;
  crude but deterministic and offline.
- ``SentenceTransformerEmbedder`` — local neural embeddings
  (``pip install sentence-transformers``).
- ``ApiEmbedder`` — any OpenAI-compatible ``/embeddings`` endpoint.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from typing import Optional

from pdftransl.config import PipelineConfig


class BaseEmbedder(ABC):
    name: str = "base"
    dim: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class HashingEmbedder(BaseEmbedder):
    """Character 3-gram hashing into a fixed-size normalized vector."""

    name = "hashing"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text.lower())
        for token in tokens:
            padded = f"^{token}$"
            for i in range(len(padded) - 2):
                gram = padded[i:i + 3]
                digest = hashlib.md5(gram.encode("utf-8")).digest()
                idx = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] % 2 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class SentenceTransformerEmbedder(BaseEmbedder):
    name = "sentence-transformers"

    def __init__(self, model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
        from sentence_transformers import SentenceTransformer  # lazy import

        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]


class ApiEmbedder(BaseEmbedder):
    name = "api"

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None):
        import requests

        self._requests = requests
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = 0  # discovered on first call

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = self._requests.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        vectors = [item["embedding"] for item in data]
        if vectors:
            self.dim = len(vectors[0])
        return vectors


def get_embedder(config: PipelineConfig) -> BaseEmbedder:
    """'auto' prefers sentence-transformers if installed, else hashing."""
    import os

    choice = config.embedder
    if choice == "auto":
        try:
            import sentence_transformers  # noqa: F401
            choice = "sentence-transformers"
        except ImportError:
            choice = "hashing"
    if choice == "hashing":
        return HashingEmbedder()
    if choice == "sentence-transformers":
        return SentenceTransformerEmbedder(
            config.embedding_model
            or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    if choice == "api":
        api_key = (
            os.environ.get(config.embedding_api_key_env)
            if config.embedding_api_key_env
            else None
        )
        return ApiEmbedder(
            base_url=config.embedding_base_url or "http://localhost:11434/v1",
            model=config.embedding_model or "nomic-embed-text",
            api_key=api_key,
        )
    raise ValueError(f"Unknown embedder '{choice}'")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
