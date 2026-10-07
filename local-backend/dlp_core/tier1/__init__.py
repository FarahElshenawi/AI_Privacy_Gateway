"""Tier 1 (deterministic) detection: loose patterns + strict validators -> Span."""
from .config import Tier1Config
from .engine import Tier1Engine
from .recognizers import (AwsSecretRecognizer, CardRecognizer, DenyTermRecognizer, IbanRecognizer,
                          PatternRecognizer, Recognizer, RecoveryCodeRecognizer)
from .registry import build_default_recognizers

__all__ = ["Tier1Config", "Tier1Engine", "Recognizer", "PatternRecognizer", "CardRecognizer",
           "IbanRecognizer", "AwsSecretRecognizer", "RecoveryCodeRecognizer", "DenyTermRecognizer",
           "build_default_recognizers"]
