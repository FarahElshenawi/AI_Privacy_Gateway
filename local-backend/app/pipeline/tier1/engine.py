"""ComplianceValidatorEngine: Tier 1 entry point for the parallel merge pipeline.

    engine = ComplianceValidatorEngine()
    spans = engine.scan(text)            # list[Span], ready for merge_engine.merge()

The engine is stateless after construction (thread-safe), runs each recognizer once over
the text, then applies two cheap cleanup passes so the merge engine gets fewer conflicts:
  * exact-duplicate collapse (same start/end/label keeps the best score and validation)
  * numeric containment (a CVV, expiry, phone or account number that sits INSIDE a
    validated card, IBAN, key or token is dropped) plus CVV/expiry mutual exclusion,
    which is what prevents the overlapping-span bug between 3-4 digit codes and dates.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .config import EngineConfig
from .normalizer import TextNormalizer
from .registry import build_default_recognizers
from .types import Span

_NUMERIC_LABELS = frozenset({"CVV", "CARD_EXPIRY", "BANK_ACCOUNT_NUMBER", "ABA_ROUTING", "US_SSN",
                             "PHONE_NUMBER", "TAX_ID", "MEDICAL_RECORD_NUMBER", "HEALTH_INSURANCE_ID",
                             "IP_ADDRESS", "RECOVERY_CODE"})
_CONTAINER_LABELS = frozenset({"CREDIT_CARD", "IBAN", "CRYPTO_WALLET", "PRIVATE_KEY", "API_KEY",
                               "AUTH_TOKEN", "CLOUD_SECRET", "CONNECTION_STRING"})


class ComplianceValidatorEngine:
    def __init__(self, config: Optional[EngineConfig] = None, extra_recognizers: Sequence = ()):
        self.config = config or EngineConfig()
        self.recognizers = build_default_recognizers(self.config) + list(extra_recognizers)
        self._normalizer = TextNormalizer()

    @property
    def labels(self) -> frozenset:
        return frozenset(l for r in self.recognizers for l in r.labels)

    # ------------------------------------------------------------------ public API
    def scan(self, text: str) -> list:
        """Scan already-normalized text. Offsets refer to `text`."""
        if len(text) > self.config.max_text_len:
            raise ValueError(f"text longer than max_text_len={self.config.max_text_len}; chunk it first")
        found = (span for rec in self.recognizers for span in rec.scan(text))
        return self._cleanup(found)

    def scan_raw(self, text: str) -> list:
        """Normalize first (NFKC, zero-width removal, dash folding) and map spans back to `text`."""
        normalized, index = self._normalizer.normalize(text)
        spans = self.scan(normalized)
        return [self._normalizer.map_span(sp, index) for sp in spans]

    # ------------------------------------------------------------------ cleanup
    def _cleanup(self, spans: Iterable[Span]) -> list:
        best: dict = {}
        for sp in spans:
            key = (sp.start, sp.end, sp.label)
            old = best.get(key)
            if old is None or (sp.validated is True, sp.score) > (old.validated is True, old.score):
                best[key] = sp
        items = list(best.values())

        containers = [s for s in items if s.label in _CONTAINER_LABELS and s.validated is True]
        kept = []
        for sp in items:
            if sp.label in _NUMERIC_LABELS and any(
                    c.start <= sp.start and sp.end <= c.end and c is not sp for c in containers):
                continue
            kept.append(sp)
        return sorted(self._exclusive_code_vs_date(kept), key=lambda s: (s.start, -s.end, s.label))

    @staticmethod
    def _exclusive_code_vs_date(spans: list) -> list:
        """A span cannot be both a security code and an expiry date: keep the stronger one."""
        codes = [s for s in spans if s.label == "CVV"]
        dates = [s for s in spans if s.label == "CARD_EXPIRY"]
        drop = set()
        for c in codes:
            for d in dates:
                if c.start < d.end and d.start < c.end:
                    loser = c if (c.score, c.end - c.start) < (d.score, d.end - d.start) else d
                    drop.add(id(loser))
        return [s for s in spans if id(s) not in drop]
