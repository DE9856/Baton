"""Extractor selection: Claude when credentials exist, Ollama when requested, keyword rules otherwise."""
from __future__ import annotations

import os

from .base import CLAIM_TYPES, TYPE_TITLES, ChunkUnit, ExtractionError, Extractor, RawClaim
from .local import ReplayExtractor, RuleExtractor


def default_extractor() -> Extractor:
    mode = os.environ.get("BATON_EXTRACTOR", "").lower()
    if mode == "ollama":
        from .llm import OllamaExtractor

        return OllamaExtractor()
    if mode == "rules":
        return RuleExtractor()
    if mode == "claude" or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        from .llm import ClaudeExtractor

        return ClaudeExtractor()
    return RuleExtractor()


__all__ = ["CLAIM_TYPES", "TYPE_TITLES", "ChunkUnit", "ExtractionError", "Extractor", "RawClaim", "ReplayExtractor",
           "RuleExtractor", "default_extractor"]
