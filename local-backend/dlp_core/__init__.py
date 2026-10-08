"""DLP core: Span contract, DetectionPipeline, MergeEngine, OffsetMasker, sealed Vault."""
from .span import Span, Evidence
from .policy import (
    Action, Policy, DEFAULT_ACTIONS, PolicyAction,
    StoragePolicy, RiskLevel, EntityCategory, ProcessingStrategy,
    RoutingEntry, PolicyEntry, RoutingDecision, ROUTING_TABLE, ALIASES,
    normalize_label, normalize_type, is_publicly_routable_ip,
    route_label, route_entity, route_entities, get_routing_entry, get_policy, should_block,
)
from .merge import MergeEngine, MergedSpan
from .vault import InMemoryVault, FernetSealer, Sealer, VaultCollisionError, VaultCapacityError
from .masker import (
    OffsetMasker, Demasker, MaskResult, MaskedSpanInfo, MaskingError, RequestBlockedError,
)
from .audit import AuditRecord, build_audit_record, emit_audit
from .detection import (
    DetectionPipeline, DetectorSpec, DetectorReport, DetectionResult,
    Detector, DetectorUnavailable, UnavailableDetector, Status,
)
from .residual_scanner import scan as residual_scan

__all__ = [
    "Span", "Evidence", "Action", "Policy", "DEFAULT_ACTIONS", "PolicyAction",
    "StoragePolicy", "RiskLevel", "EntityCategory", "ProcessingStrategy",
    "RoutingEntry", "PolicyEntry", "RoutingDecision", "ROUTING_TABLE", "ALIASES",
    "normalize_label", "normalize_type", "is_publicly_routable_ip",
    "route_label", "route_entity", "route_entities", "get_routing_entry", "get_policy", "should_block",
    "MergeEngine", "MergedSpan",
    "InMemoryVault", "FernetSealer", "Sealer", "VaultCollisionError", "VaultCapacityError",
    "OffsetMasker", "Demasker", "MaskResult", "MaskedSpanInfo", "MaskingError", "RequestBlockedError",
    "AuditRecord", "build_audit_record", "emit_audit",
    "DetectionPipeline", "DetectorSpec", "DetectorReport", "DetectionResult",
    "Detector", "DetectorUnavailable", "UnavailableDetector", "Status",
    "residual_scan",
]
