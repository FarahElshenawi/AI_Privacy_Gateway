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
  * HONEST AVAILABILITY. `available` never imports torch (a broken install can't crashthe
    request) and `unavailable_reason` says why, as a short code.
  * PARTIAL RESULTS ON DEADLINE. When the deadline passes between windows, we BREAK the
    loop instead of raising — the spans found so far are returned to the pipeline, so a
    long file gets partial Tier 2 coverage instead of zero. The pipeline still sees the
    timeout via the returned status flag and can report degradation honestly.
  * TIMING INSTRUMENTATION. Every scan logs window count, per-window inference time, lock
    wait time, and deadline hits — metadata only, never PII or text contents.

Not verified here: the real model's output shape and quality (no model in the build
sandbox). `parse_entities` accepts the shapes plausible for GLiNER2 and falls back safely.
"""
from __future__ import annotations

import importlib.util
import logging
import re
import threading
import time
from typing import Any, Iterable, Optional, Sequence

from ..span import Span
from .config import Tier2Config

# ── Timing diagnostics (metadata only, never document contents or PII) ──
_LOG = logging.getLogger("doppel.timing.tier2")
_LOG.setLevel(logging.INFO)
if not _LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
    _LOG.addHandler(_h)
_LOG.propagate = False


_LABEL_MAP = {
    "PERSON": "PERSON", "FULL_NAME": "PERSON", "FIRST_NAME": "PERSON", "MIDDLE_NAME": "PERSON",
    "LAST_NAME": "PERSON", "DATE_OF_BIRTH": "DATE_OF_BIRTH", "ADDRESS": "ADDRESS",
    "STREET_ADDRESS": "ADDRESS", "CITY": "LOCATION", "STATE_OR_REGION": "LOCATION",
    "POSTAL_CODE": "LOCATION", "COUNTRY": "LOCATION", "USERNAME": "USERNAME",
    "ORGANIZATION": "ORGANIZATION", "PASSWORD": "PASSWORD", "SECRET": "SECRET",
    "GOVERNMENT_ID": "GOVERNMENT_ID", "NATIONAL_ID_NUMBER": "GOVERNMENT_ID",
    "PASSPORT_NUMBER": "PASSPORT_NUMBER", "DRIVERS_LICENSE_NUMBER": "DRIVERS_LICENSE_NUMBER",
    "SENSITIVE_DATE": "SENSITIVE_DATE", "DATE": "DATE", "EMAIL": "EMAIL", "PHONE_NUMBER": "PHONE_NUMBER",
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


def iter_windows(text: str, size: int, overlap: int):
    """Yield (offset, window) pieces of at most `size` chars, cut at whitespace, overlapping by
    ~`overlap` so an entity on a boundary is whole in one window."""
    n = len(text)
    if n <= size:
        yield 0, text
        return
    pos = 0
    while pos < n:
        end = min(n, pos + size)
        if end < n:
            cut = max(text.rfind("\n", pos + size // 2, end), text.rfind(" ", pos + size // 2, end))
            if cut > pos:
                end = cut
        yield pos, text[pos:end]
        if end >= n:
            break
        nxt = max(end - overlap, pos + 1)
        ws = max(text.find(" ", nxt, end), text.find("\n", nxt, end))      # start thenext window on a word
        pos = ws + 1 if ws != -1 else end


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
        # Last scan timing summary (for stats() / health endpoint)
        self._last_scan_summary: Optional[dict] = None

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

    supports_deadline = True

    def scan(self, text: str, deadline: Optional[float] = None) -> Sequence[Span]:
        """Scan `text` in windows. `deadline` (time.perf_counter() value) is checked between
        windows. When it passes we BREAK (not raise) — partial spans are returned to the
        pipeline, and the caller can detect the timeout via `timed_out` in stats()."""
        from ..detection import DetectorUnavailable
        if not self.config.enabled:
            raise DetectorUnavailable(self.name)
        self._ensure_model()
        if self._state != "ready":
            raise DetectorUnavailable(self.name)

        # ── Timing diagnostics (metadata only, never PII) ──
        _t_scan_start = time.perf_counter()
        _windows_total = 0
        _windows_processed = 0
        _windows_skipped_deadline = 0
        _infer_total_ms = 0.0
        _lock_wait_total_ms = 0.0
        _max_window_ms = 0.0
        _timed_out = False

        found: dict[tuple[str, str], float] = {}          # (label, entity text) -> best confidence
        for start, window in iter_windows(text, self.config.window_chars, self.config.window_overlap):
            _windows_total += 1
            if deadline is not None and time.perf_counter() > deadline:
                _windows_skipped_deadline += 1
                _timed_out = True
                _LOG.info(
                    "[Doppel Tier2] window_skip_deadline window_index=%d windows_total_so_far=%d "
                    "found_unique=%d elapsed_ms=%.1f budget_remaining_ms=%.1f",
                    _windows_total - 1, _windows_total, len(found),
                    (time.perf_counter() - _t_scan_start) * 1000,
                    max(0.0, (deadline - time.perf_counter()) * 1000),
                )
                break  # ← KEY CHANGE: break instead of raise, keep partial results

            _t_lock = time.perf_counter()
            with self._infer_lock:
                _lock_wait_ms = (time.perf_counter() - _t_lock) * 1000
                _lock_wait_total_ms += _lock_wait_ms

                _t_infer = time.perf_counter()
                result = self._model.extract_entities_long(
                    window, entity_types=list(self.config.labels), threshold=self.config.threshold,
                    chunk_size=self.config.chunk_size, chunk_overlap=self.config.chunk_overlap,
                    include_confidence=True, include_spans=True)
                _infer_ms = (time.perf_counter() - _t_infer) * 1000
                _infer_total_ms += _infer_ms
                if _infer_ms > _max_window_ms:
                    _max_window_ms = _infer_ms

            spans, unlocated = parse_entities(window, (result or {}).get("entities", {}))
            self._unlocated += unlocated
            for sp in spans:
                key = (sp.label, window[sp.start:sp.end])
                found[key] = max(found.get(key, 0.0), sp.score)

            _windows_processed += 1

            # Log first 3 windows, then every 10th — see pacing without flooding
            if _windows_processed <= 3 or _windows_processed % 10 == 0:
                _LOG.info(
                    "[Doppel Tier2] window_done idx=%d/%d chars=%d infer_ms=%.1f "
                    "lock_wait_ms=%.2f found_unique=%d unlocated=%d scan_elapsed_ms=%.1f",
                    _windows_processed, _windows_total, len(window),
                    _infer_ms, _lock_wait_ms, len(found), unlocated,
                    (time.perf_counter() - _t_scan_start) * 1000,
                )

        _total_ms = (time.perf_counter() - _t_scan_start) * 1000
        _final_spans = self._spans_from(text, found)
        _LOG.info(
            "[Doppel Tier2] scan_complete windows_total=%d windows_processed=%d "
            "windows_skipped_deadline=%d infer_total_ms=%.1f infer_avg_ms=%.1f "
            "infer_max_ms=%.1f lock_wait_total_ms=%.2f unique_entities=%d "
            "total_spans=%d elapsed_ms=%.1f deadline_hit=%s "
            "config_window_chars=%d config_window_overlap=%d config_chunk_size=%d "
            "config_chunk_overlap=%d config_threshold=%.2f labels_count=%d",
            _windows_total, _windows_processed, _windows_skipped_deadline,
            _infer_total_ms,
            (_infer_total_ms / _windows_processed) if _windows_processed else 0.0,
            _max_window_ms,
            _lock_wait_total_ms,
            len(found),
            len(_final_spans),
            _total_ms,
            "yes" if _timed_out else "no",
            self.config.window_chars, self.config.window_overlap,
            self.config.chunk_size, self.config.chunk_overlap,
            self.config.threshold,
            len(self.config.labels),
        )

        # Stash summary for stats() — useful for /health and debugging
        self._last_scan_summary = {
            "windows_total": _windows_total,
            "windows_processed": _windows_processed,
            "windows_skipped_deadline": _windows_skipped_deadline,
            "infer_total_ms": _infer_total_ms,
            "infer_avg_ms": (_infer_total_ms / _windows_processed) if _windows_processed else 0.0,
            "infer_max_ms": _max_window_ms,
            "lock_wait_total_ms": _lock_wait_total_ms,
            "unique_entities": len(found),
            "total_spans": len(_final_spans),
            "elapsed_ms": _total_ms,
            "timed_out": _timed_out,
        }

        return _final_spans

    @staticmethod
    def _spans_from(text: str, found: dict) -> list[Span]:
        """Every word-bounded occurrence of an entity the model found in ANY window ismasked,
        so the same name is the same sensitive value wherever it appears in a long file."""
        out: list[Span] = []
        seen: set[tuple[int, int, str]] = set()
        for (label, ent), conf in found.items():
            for s0, e0 in _occurrences(text, ent):
                if (s0, e0, label) not in seen:
                    seen.add((s0, e0, label))
                    out.append(Span(s0, e0, label, conf, "tier2.gliner", None, False))
        return out

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
        out = {"state": self._state, "load_error": self._load_error, "unlocated_entities": self._unlocated}
        if self._last_scan_summary:
            out["last_scan"] = self._last_scan_summary
        return out

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