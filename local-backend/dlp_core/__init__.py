"""DLP core: Span contract, DetectionPipeline, MergeEngine, OffsetMasker, sealed Vault."""
from .span import Span, Evidence
from .policy import Action, Policy, DEFAULT_ACTIONS
from .merge import MergeEngine, MergedSpan
from .vault import InMemoryVault, FernetSealer, Sealer, VaultCollisionError, VaultCapacityError
from .masker import OffsetMasker, Demasker, MaskResult, MaskedSpanInfo, MaskingError
from .detection import (
    DetectionPipeline, DetectorSpec, DetectorReport, DetectionResult,
    Detector, DetectorUnavailable, UnavailableDetector, Status,
)
from .residual_scanner import scan as residual_scan

__all__ = [
    "Span", "Evidence", "Action", "Policy", "DEFAULT_ACTIONS", "MergeEngine", "MergedSpan",
    "InMemoryVault", "FernetSealer", "Sealer", "VaultCollisionError", "VaultCapacityError",
    "OffsetMasker", "Demasker", "MaskResult", "MaskedSpanInfo", "MaskingError",
    "DetectionPipeline", "DetectorSpec", "DetectorReport", "DetectionResult",
    "Detector", "DetectorUnavailable", "UnavailableDetector", "Status",
    "residual_scan",
]
