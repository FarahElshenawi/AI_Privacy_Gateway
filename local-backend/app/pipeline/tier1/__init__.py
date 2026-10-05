"""Tier 1 (deterministic) detection: loose patterns + strict validators.

    from tier1 import ComplianceValidatorEngine, EngineConfig
"""
from .config import EngineConfig
from .engine import ComplianceValidatorEngine
from .normalizer import TextNormalizer
from .types import Span

__all__ = ["ComplianceValidatorEngine", "EngineConfig", "TextNormalizer", "Span"]
