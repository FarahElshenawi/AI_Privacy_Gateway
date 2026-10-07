"""Tier 2: semantic detection (names, organizations, locations, addresses, ...) via GLiNER2-PII.

PyTorch inference only (ONNX is not implemented). See engine.py for the guarantees and for what
has NOT been verified against the real model.
"""
from .config import Tier2Config
from .engine import Tier2Engine, map_label, parse_entities

__all__ = ["Tier2Config", "Tier2Engine", "map_label", "parse_entities"]
