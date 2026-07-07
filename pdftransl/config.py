"""Configuration: pipeline options and LLM provider presets.

Everything is overridable via environment variables (PDFTRANSL_* /
provider key vars) or programmatically, so the same code runs in a CLI,
a Django view, or a Celery worker without changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

try:  # optional: load .env if python-dotenv is available
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from pdftransl.exceptions import ConfigError


@dataclass
class ProviderConfig:
    """A concrete LLM endpoint (cloud API or local server)."""

    name: str
    base_url: str
    model: str
    api_key_env: Optional[str] = None   # env var holding the key
    api_key: Optional[str] = None       # explicit key (overrides env)
    kind: str = "openai"                # "openai" (OpenAI-compatible) | "anthropic"
    supports_vision: bool = False
    is_local: bool = False
    timeout: float = 300.0
    max_retries: int = 3
    extra_headers: dict[str, str] = field(default_factory=dict)

    def resolve_api_key(self) -> Optional[str]:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


# Cloud providers and local OpenAI-compatible servers work through the
# same client, so adding a provider is just another preset entry.
PROVIDER_PRESETS: dict[str, ProviderConfig] = {
    "openrouter": ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="openrouter/auto",
        api_key_env="OPENROUTER_API_KEY",
        supports_vision=True,
    ),
    "openai": ProviderConfig(
        name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        supports_vision=True,
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        model="claude-sonnet-5",
        api_key_env="ANTHROPIC_API_KEY",
        kind="anthropic",
        supports_vision=True,
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
    ),
    # Local servers (no API key required)
    "ollama": ProviderConfig(
        name="ollama",
        base_url="http://localhost:11434/v1",
        model="qwen2.5:14b",
        is_local=True,
    ),
    "vllm": ProviderConfig(
        name="vllm",
        base_url="http://localhost:8000/v1",
        model="Qwen/Qwen2.5-14B-Instruct",
        is_local=True,
    ),
    "lmstudio": ProviderConfig(
        name="lmstudio",
        base_url="http://localhost:1234/v1",
        model="local-model",
        is_local=True,
    ),
    "llamacpp": ProviderConfig(
        name="llamacpp",
        base_url="http://localhost:8080/v1",
        model="local-model",
        is_local=True,
    ),
}


# Substrings that mark a model as multimodal (vision-capable). Lets a
# local multimodal model (e.g. Ollama gemma3, llava, qwen2.5-vl) be used
# for OCR / figure description without a separate vision-model setting.
_VISION_MODEL_HINTS = (
    "vl", "vision", "llava", "gemma3", "gemma-3", "minicpm-v", "moondream",
    "pixtral", "internvl", "cogvlm", "idefics", "granite-vision", "-v:",
    "llama3.2-vision", "llama-3.2-vision", "gpt-4o", "gpt-4.1", "gpt-5",
    "claude", "gemini",
)


def model_supports_vision(name: Optional[str]) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _VISION_MODEL_HINTS)


def get_provider_config(
    provider: str,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> ProviderConfig:
    """Return a preset provider config with optional overrides.

    Unknown provider names are allowed if ``base_url`` is given —
    they are treated as a custom OpenAI-compatible endpoint.
    """
    preset = PROVIDER_PRESETS.get(provider)
    if preset is None:
        if not base_url:
            raise ConfigError(
                f"Unknown provider '{provider}'. Known: "
                f"{', '.join(sorted(PROVIDER_PRESETS))}. "
                "For a custom endpoint pass base_url explicitly."
            )
        preset = ProviderConfig(name=provider, base_url=base_url, model=model or "")

    cfg = ProviderConfig(**{**preset.__dict__})
    if model:
        cfg.model = model
    if base_url:
        cfg.base_url = base_url
    if api_key:
        cfg.api_key = api_key
    # env overrides: PDFTRANSL_MODEL / PDFTRANSL_BASE_URL
    cfg.model = os.environ.get("PDFTRANSL_MODEL", cfg.model) if not model else cfg.model
    # a multimodal model name implies vision even on presets marked non-vision
    if model_supports_vision(cfg.model):
        cfg.supports_vision = True
    return cfg


@dataclass
class PipelineConfig:
    """All knobs of the translation pipeline."""

    # Languages
    source_lang: str = "en"
    target_lang: str = "ru"

    # Parsing
    parser_backend: str = "auto"        # auto | mineru_local | mineru_api | pymupdf
    mineru_api_base: str = "https://mineru.net/api/v4"
    mineru_api_key_env: str = "MINERU_API_KEY"

    # Translation provider
    provider: str = "openrouter"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    temperature: float = 0.15
    max_output_tokens: Optional[int] = None
    # Fallback chain: providers tried in order when the primary fails
    # (e.g. ["ollama", "openrouter"] = local first, cloud on failure).
    fallback_providers: list[str] = field(default_factory=list)

    # Chunking / throughput
    chunk_char_budget: int = 4000       # max chars of masked text per request
    max_workers: int = 4                # parallel segment translations (1 = sequential)

    # Document-level translation context
    doc_summary: bool = True            # LLM summary of the paper in the system prompt
    auto_glossary: bool = True          # LLM-extracted per-document term glossary
    source_context_chars: int = 400     # tail of the previous source segment in the prompt
    skip_references: bool = True        # do not translate the References/Bibliography section

    # Quality control
    review: bool = True                 # LLM self-review of flagged segments
    max_repair_attempts: int = 2
    min_length_ratio: float = 0.4       # translated/source length bounds
    max_length_ratio: float = 3.5
    max_residual_source_ratio: float = 0.35  # tolerated share of source-script words

    # Extra quality checks
    backtranslation_check: bool = False  # embed(source) vs embed(back-translation)
    backtranslation_min_similarity: float = 0.5
    quality_score: bool = False          # LLM-judge score per segment (0-100)
    quality_score_threshold: float = 70.0  # below this -> flag for review
    fix_latex: bool = True               # LLM-repair broken formulas in the result
    max_latex_fixes: int = 10
    render_check: bool = False           # render exported HTML, count KaTeX errors

    # Provider behaviour
    structured_outputs: bool = False     # ask for JSON mode where the task is JSON
    rpm_limit: Optional[int] = None      # max requests/minute (free-tier throttle)

    # RAG / translation memory
    use_rag: bool = True
    tm_top_k: int = 3
    tm_min_similarity: float = 0.82
    tm_domain: Optional[str] = None     # restrict TM search/learn to a domain
    learn: bool = True                  # store good translations back into TM
    embedder: str = "auto"              # auto | hashing | sentence-transformers | api
    embedding_model: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_api_key_env: Optional[str] = None

    # Figures / VLM
    describe_figures: bool = False      # run VLM over exported images
    vision_provider: Optional[str] = None   # defaults to `provider`
    vision_model: Optional[str] = None
    max_figures: int = 30

    # Scanned / image-only PDFs (OCR)
    ocr_on_scan: bool = True            # auto-route detected scans to VLM OCR
    ocr_dpi: int = 200                  # page render resolution for OCR
    max_ocr_pages: int = 50             # cap VLM OCR calls per document

    # Output
    bilingual: bool = False             # alternate source/translation paragraphs
    export_formats: list[str] = field(default_factory=lambda: ["html", "docx", "pdf"])
    # any of: "html", "docx", "pdf", "latex" (md is always produced); a format
    # whose engine is missing is reported in the QA report, not silently dropped

    # Storage
    db_path: str = "data/pdftransl.db"
    output_dir: str = "data/output"
    parse_cache: bool = True            # cache parse results by PDF content hash

    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, **overrides: Any) -> "PipelineConfig":
        """Build config from PDFTRANSL_* environment variables + overrides."""
        env = os.environ
        kwargs: dict[str, Any] = {}
        mapping = {
            "PDFTRANSL_SOURCE_LANG": "source_lang",
            "PDFTRANSL_TARGET_LANG": "target_lang",
            "PDFTRANSL_PARSER": "parser_backend",
            "PDFTRANSL_PROVIDER": "provider",
            "PDFTRANSL_MODEL": "model",
            "PDFTRANSL_BASE_URL": "base_url",
            "PDFTRANSL_DB": "db_path",
            "PDFTRANSL_OUTPUT_DIR": "output_dir",
            "PDFTRANSL_VISION_PROVIDER": "vision_provider",
            "PDFTRANSL_VISION_MODEL": "vision_model",
        }
        for env_name, attr in mapping.items():
            if env.get(env_name):
                kwargs[attr] = env[env_name]
        for flag, attr in (
            ("PDFTRANSL_REVIEW", "review"),
            ("PDFTRANSL_USE_RAG", "use_rag"),
            ("PDFTRANSL_LEARN", "learn"),
            ("PDFTRANSL_DESCRIBE_FIGURES", "describe_figures"),
            ("PDFTRANSL_DOC_SUMMARY", "doc_summary"),
            ("PDFTRANSL_AUTO_GLOSSARY", "auto_glossary"),
            ("PDFTRANSL_SKIP_REFERENCES", "skip_references"),
            ("PDFTRANSL_BILINGUAL", "bilingual"),
            ("PDFTRANSL_PARSE_CACHE", "parse_cache"),
            ("PDFTRANSL_QUALITY_SCORE", "quality_score"),
            ("PDFTRANSL_FIX_LATEX", "fix_latex"),
            ("PDFTRANSL_RENDER_CHECK", "render_check"),
            ("PDFTRANSL_STRUCTURED_OUTPUTS", "structured_outputs"),
            ("PDFTRANSL_OCR_ON_SCAN", "ocr_on_scan"),
        ):
            if env.get(flag) is not None:
                kwargs[attr] = env[flag].strip().lower() in ("1", "true", "yes", "on")
        if env.get("PDFTRANSL_MAX_WORKERS"):
            kwargs["max_workers"] = int(env["PDFTRANSL_MAX_WORKERS"])
        if env.get("PDFTRANSL_OCR_DPI"):
            kwargs["ocr_dpi"] = int(env["PDFTRANSL_OCR_DPI"])
        if env.get("PDFTRANSL_RPM"):
            kwargs["rpm_limit"] = int(env["PDFTRANSL_RPM"])
        if env.get("PDFTRANSL_FALLBACK_PROVIDERS"):
            kwargs["fallback_providers"] = [
                p.strip() for p in env["PDFTRANSL_FALLBACK_PROVIDERS"].split(",") if p.strip()
            ]
        if env.get("PDFTRANSL_EXPORT_FORMATS"):
            kwargs["export_formats"] = [
                f.strip() for f in env["PDFTRANSL_EXPORT_FORMATS"].split(",") if f.strip()
            ]
        kwargs.update(overrides)
        return cls(**kwargs)

    def provider_config(self) -> ProviderConfig:
        return get_provider_config(
            self.provider, model=self.model,
            base_url=self.base_url, api_key=self.api_key,
        )

    def vision_provider_config(self) -> ProviderConfig:
        same_provider = not self.vision_provider
        # When no dedicated vision model is set, reuse the main model —
        # if the user runs a multimodal main model (e.g. gemma3:12b) it
        # should handle OCR/figures, not some preset default.
        model = self.vision_model or (self.model if same_provider else None)
        cfg = get_provider_config(
            self.vision_provider or self.provider,
            model=model,
            base_url=self.base_url if same_provider else None,
            api_key=self.api_key if same_provider else None,
        )
        # An explicitly chosen vision provider/model signals intent — trust
        # it even for local presets marked non-vision.
        if self.vision_model or self.vision_provider:
            cfg.supports_vision = True
        return cfg
