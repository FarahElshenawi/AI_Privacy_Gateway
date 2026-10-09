"""Tier 2 configuration: GLiNER2-PII model settings (PyTorch inference only).

ONNX is NOT implemented. The `use_onnx`/`onnx_path` fields are kept so old configs still load,
but `Tier2Engine` refuses `use_onnx=True` at construction instead of failing on every request.
Speed claims for ONNX were never measured; benchmark before adopting it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# SEMANTIC labels only: things Tier 1's regex + checksums cannot catch. Structured labels
# (card_number, iban, ...) are deliberately left to Tier 1.
DEFAULT_LABELS: tuple[str, ...] = (
    "person", "full_name", "first_name", "middle_name", "last_name", "date_of_birth",
    "address", "street_address", "city", "state_or_region", "postal_code", "country",
    "username",
    "government_id", "national_id_number", "passport_number", "drivers_license_number",
    "sensitive_date",
    "password", "secret",
)


@dataclass(frozen=True, slots=True)
class Tier2Config:
    """
    Attributes:
        model_name: HuggingFace model id (unverified by the author of this module: check that
                    it exists, its license, and that its output matches `parse_entities`).
        labels: labels sent to the model.
        threshold: confidence threshold. Low = high recall (a miss is a data leak).
        chunk_size / chunk_overlap: passed to the model's long-text extraction.
        enabled: False reports the tier as unavailable (degraded mode).
    """
    model_name: str = "fastino/gliner2-privacy-filter-PII-multi"
    onnx_path: Optional[str] = None
    use_onnx: bool = False
    labels: tuple[str, ...] = DEFAULT_LABELS
    threshold: float = 0.3
    chunk_size: int = 384
    chunk_overlap: int = 64
    window_chars: int = 6000        # text is scanned in windows of this size (budget checked between them)
    window_overlap: int = 200
    enabled: bool = True

    @property
    def label_set(self) -> frozenset[str]:
        """Canonical (post-mapping) uppercase labels this tier can emit."""
        from .engine import map_label
        return frozenset(map_label(l) for l in self.labels)
