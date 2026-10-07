"""Tier2Engine: GLiNER2-PII semantic detection. Emits `dlp_core.Span`, like every detector.

What this module guarantees (and tests without the model, via `parse_entities`):
  * OFFSETS ARE VERIFIED. If the model returns start/end they are used only when
    text[start:end] equals the entity text (chunked inference can return chunk-relative
    offsets). Otherwise EVERY word-bounded occurrence of the entity text is masked, because
    the same name elsewhere in a prompt is the same sensitive value. The old `text.find`
    covered only the first occurrence.
  * LOADING IS RACE-FREE AND OFF THE REQUEST PATH. `warmup()` loads at startup; until it is
    ready the tier reports UNAVAILABLE("warming_up") instead of making a user wait. One load
    lock prevents two first requests from loading the model twice.
  * HONEST AVAILABILITY. `available` never imports torch (a broken install can't crash the
    request) and `unavailable_reason` says why, as a short code.

Not verified here: the real model's output shape and quality (no model in the build
sandbox). `parse_entities` accepts the shapes plausible for GLiNER2 and falls back safely.
"""
from __future__ import annotations

import importlib.util
import re
import threading
from typing import Any, Iterable, Optional, Sequence

from ..span import Span
from .config import Tier2Config

_LABEL_MAP = {
    "PERSON": "PERSON", "FULL_NAME": "PERSON", "FIRST_NAME": "PERSON", "MIDDLE_NAME": "PERSON",
    "LAST_NAME": "PERSON", "DATE_OF_BIRTH": "DATE_OF_BIRTH", "ADDRESS": "ADDRESS",
    "STREET_ADDRESS": "ADDRESS", "CITY": "LOCATION", "STATE_OR_REGION": "LOCATION",
    "POSTAL_CODE": "LOCATION", "COUNTRY": "LOCATION", "USERNAME": "USERNAME",
    "ORGANIZATION": "ORGANIZATION", "PASSWORD": "PASSWORD", "SECRET": "SECRET",
    "GOVERNMENT_ID": "GOVERNMENT_ID", "NATIONAL_ID_NUMBER": "GOVERNMENT_ID",
    "PASSPORT_NUMBER": "PASSPORT_NUMBER", "DRIVERS_LICENSE_NUMBER": "DRIVERS_LICENSE_NUMBER",
    "SENSITIVE_DATE": "SENSITIVE_DATE", "EMAIL": "EMAIL", "PHONE_NUMBER": "PHONE_NUMBER",
    "IP_ADDRESS": "IP_ADDRESS", "API_KEY": "API_KEY", "ACCESS_TOKEN": "AUTH_TOKEN",
    "BANK_ACCOUNT": "BANK_ACCOUNT_NUMBER", "IBAN": "IBAN", "CARD_NUMBER": "CREDIT_CARD",
    "CARD_EXPIRY": "CARD_EXPIRY", "CARD_CVV": "CVV", "ROUTING_NUMBER": "ABA_ROUTING",
    "TAX_ID": "TAX_ID", "RECOVERY_CODE": "RECOVERY_CODE", "ACCOUNT_ID": "ACCOUNT_ID",
    "SENSITIVE_ACCOUNT_ID": "ACCOUNT_ID", "DOCUMENT_DATE": "SENSITIVE_DATE",
    "EXPIRATION_DATE": "SENSITIVE_DATE", "TRANSACTION_DATE": "SENSITIVE_DATE",
}


def map_label(raw: str) -> str:
    up = raw.strip().upper()
    return _LABEL_MAP.get(up, up)


# ------------------------------------------------------------------ pure parsing (no model needed)
def _occurrences(text: str, needle: str, *, ignore_case: bool = False, fuzzy_ws: bool = False) -> list[tuple[int, int]]:
    if fuzzy_ws:
        body = r"\s*".join(re.escape(tok) for tok in needle.split())
    else:
        body = re.escape(needle)
    if not body:
        return []
    left = r"(?<!\w)" if (needle[0].isalnum() or needle[0] == "_") else ""
    right = r"(?!\w)" if (needle[-1].isalnum() or needle[-1] == "_") else ""
    flags = re.IGNORECASE if ignore_case else 0
    return [(m.start(), m.end()) for m in re.finditer(left + body + right, text, flags)]


def _as_int(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def parse_entities(text: str, entities: Any) -> tuple[list[Span], int]:
    """Convert a GLiNER-style {label: [entity, ...]} result into Spans.

    Returns (spans, unlocated) where `unlocated` counts entities the model reported but we
    could not place in `text` (surfaced via Tier2Engine.stats(); they are never silently lost).
    """
    spans: list[Span] = []
    unlocated = 0
    if not isinstance(entities, dict):
        return spans, 0
    seen: set[tuple[int, int, str]] = set()
    for raw_label, values in entities.items():
        label = map_label(str(raw_label))
        for v in values or ():
            item = {"text": v} if isinstance(v, str) else v
            if not isinstance(item, dict):
                continue
            ent = str(item.get("text") or "").strip()
            if not ent:
                continue
            try:
                conf = float(item.get("confidence", item.get("score", 0.5)) or 0.0)
            except (TypeError, ValueError):
                conf = 0.5
            conf = min(1.0, max(0.0, conf))
            s0 = _as_int(item.get("start", item.get("start_char")))
            e0 = _as_int(item.get("end", item.get("end_char")))
            places: list[tuple[int, int]] = []
            if s0 is not None and e0 is not None and 0 <= s0 < e0 <= len(text) and text[s0:e0].strip() == ent:
                places = [(s0 + (len(text[s0:e0]) - len(text[s0:e0].lstrip())), e0 - (len(text[s0:e0]) - len(text[s0:e0].rstrip())))]
            else:
                places = (_occurrences(text, ent) or _occurrences(text, ent, ignore_case=True)
                          or _occurrences(text, ent, ignore_case=True, fuzzy_ws=True))
            if not places:
                unlocated += 1
                continue
            for s, e in places:
                if (s, e, label) not in seen and e > s:
                    seen.add((s, e, label))
                    spans.append(Span(s, e, label, conf, "tier2.gliner", None, False))
    return spans, unlocated


# ------------------------------------------------------------------ engine
class Tier2Engine:
    name = "tier2"

    def __init__(self, config: Optional[Tier2Config] = None) -> None:
        self.config = config or Tier2Config()
        if self.config.use_onnx:
            raise NotImplementedError(
                "ONNX inference is not implemented for Tier 2; use PyTorch (use_onnx=False). "
                "Benchmark before assuming an ONNX speed-up.")
        self._model: Any = None
        self._state = "idle"                       # idle | loading | ready | failed
        self._load_error: Optional[str] = None
        self._load_lock = threading.Lock()         # one model load, ever
        self._infer_lock = threading.Lock()        # the model is not thread-safe
        self._unlocated = 0

    # -- Detector protocol -------------------------------------------------
    @property
    def labels(self) -> frozenset[str]:
        return self.config.label_set

    @property
    def available(self) -> bool:
        return self._availability()[0]

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._availability()[1]

    def scan(self, text: str) -> Sequence[Span]:
        from ..detection import DetectorUnavailable
        if not self.config.enabled:
            raise DetectorUnavailable(self.name)
        self._ensure_model()
        if self._state != "ready":
            raise DetectorUnavailable(self.name)
        with self._infer_lock:
            result = self._model.extract_entities_long(
                text, entity_types=list(self.config.labels), threshold=self.config.threshold,
                chunk_size=self.config.chunk_size, chunk_overlap=self.config.chunk_overlap,
                include_confidence=True, include_spans=True)
        spans, unlocated = parse_entities(text, (result or {}).get("entities", {}))
        self._unlocated += unlocated
        return spans

    # -- lifecycle ---------------------------------------------------------
    def warmup(self) -> bool:
        """Load the model and run one tiny inference. Call at startup, off the request path."""
        self._ensure_model()
        if self._state == "ready":
            try:
                self.scan("Warm-up text for Alex Morgan in Berlin.")
            except Exception:  # noqa: BLE001 - warm-up must not take the server down
                pass
        return self._state == "ready"

    def stats(self) -> dict:
        return {"state": self._state, "load_error": self._load_error, "unlocated_entities": self._unlocated}

    def _availability(self) -> tuple[bool, Optional[str]]:
        if not self.config.enabled:
            return False, "disabled"
        if self._state == "ready":
            return True, None
        if self._state == "loading":
            return False, "warming_up"
        if self._state == "failed":
            return False, f"load_failed:{self._load_error}"
        try:                                       # idle: cheap check, never imports torch
            if importlib.util.find_spec("gliner2") is None:
                return False, "gliner2_not_installed"
        except Exception:  # noqa: BLE001
            return False, "gliner2_probe_failed"
        return True, None

    def _ensure_model(self) -> None:
        if self._state in ("ready", "failed"):
            return
        with self._load_lock:
            if self._state in ("ready", "failed"):
                return
            self._state = "loading"
            try:
                self._load_pytorch()
                self._state = "ready"
            except Exception as exc:  # noqa: BLE001 - torch problems raise OSError, not ImportError
                self._load_error = type(exc).__name__
                self._state = "failed"

    def _load_pytorch(self) -> None:
        from gliner2 import GLiNER2
        self._model = GLiNER2.from_pretrained(self.config.model_name)
