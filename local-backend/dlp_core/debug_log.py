"""Debug logging for detection, masking, and demasking.

This is a SEPARATE logger from the audit logger. The audit logger never logs values
(SOC 2 compliance). This one does — it's for local debugging only.

Enable with: DLP_DEBUG=true
Disable with: DLP_DEBUG=false (default — production)

Output goes to stderr, formatted for readability:

    [DETECT] tier1: EMAIL 'sarah@example.com' at [0,19] validated=True
    [DETECT] tier2: PERSON 'Sarah Mitchell' at [0,14] score=0.95
    [MERGE]  3 spans → 2 disjoint (1 overlap resolved)
    [MASK]   EMAIL 'sarah@example.com' → 'jennifer85@example.net' (faker)
    [MASK]   CREDIT_CARD '4242...' → '[REDACTED:CREDIT_CARD]' (redact, no store)
    [DEMASK] 'jennifer85@example.net' → 'sarah@example.com' (conv=chat_123)
"""
from __future__ import annotations

import logging
import os
import sys

# OFF by default. Set DLP_DEBUG=true to see what each tier detects and masks.
_ENABLED = os.environ.get("DLP_DEBUG", "false").strip().lower() in ("1", "true", "yes", "on")

# Separate from the audit logger — this one logs VALUES, not just metadata.
debug_logger = logging.getLogger("privacy_gateway.debug")

if _ENABLED:
    debug_logger.setLevel(logging.DEBUG)
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    debug_logger.addHandler(_handler)
    debug_logger.propagate = False


def is_enabled() -> bool:
    """Check if debug logging is on."""
    return _ENABLED


def _truncate(s: str, n: int = 40) -> str:
    """Truncate long values for readability."""
    if len(s) <= n:
        return s
    return s[:n - 3] + "..."


# ─── Detection logging ────────────────────────────────────────────────────

def log_tier_detected(tier_name: str, spans, text: str) -> None:
    """Log each entity a tier found, with the actual text snippet."""
    if not _ENABLED:
        return
    for sp in spans:
        snippet = text[sp.start:sp.end] if sp.end <= len(text) else "?"
        validated = getattr(sp, "validated", None)
        score = getattr(sp, "score", 0.5)
        val_str = ""
        if validated is True:
            val_str = " validated=True"
        elif validated is False:
            val_str = " validated=False"
        score_str = f" score={score:.2f}" if score != 0.5 else ""
        debug_logger.debug(
            f"[DETECT] {tier_name}: {sp.label} '{_truncate(snippet)}' "
            f"at [{sp.start},{sp.end}){val_str}{score_str}"
        )


def log_merge(merged_count: int, raw_count: int) -> None:
    """Log how many spans survived the merge."""
    if not _ENABLED:
        return
    overlaps = raw_count - merged_count
    overlap_str = f" ({overlaps} overlap resolved)" if overlaps > 0 else ""
    debug_logger.debug(
        f"[MERGE]  {raw_count} spans → {merged_count} disjoint{overlap_str}"
    )


# ─── Masking logging ───────────────────────────────────────────────────────

def log_mask_faker(label: str, real: str, fake: str, reused: bool) -> None:
    """Log when a FAKER replacement is applied."""
    if not _ENABLED:
        return
    kind = "reused" if reused else "new surrogate"
    debug_logger.debug(
        f"[MASK]   {label} '{_truncate(real)}' → '{_truncate(fake)}' (faker, {kind})"
    )


def log_mask_redact(label: str, real: str, placeholder: str, stored: bool) -> None:
    """Log when a REDACT replacement is applied."""
    if not _ENABLED:
        return
    kind = "stored for demask" if stored else "no store"
    debug_logger.debug(
        f"[MASK]   {label} '{_truncate(real)}' → '{_truncate(placeholder)}' (redact, {kind})"
    )


def log_mask_keep(label: str, real: str) -> None:
    """Log when a KEEP action is applied (value left in clear)."""
    if not _ENABLED:
        return
    debug_logger.debug(
        f"[MASK]   {label} '{_truncate(real)}' → (kept in clear)"
    )


def log_mask_result(text_len: int, masked_len: int, span_count: int) -> None:
    """Log a summary after masking."""
    if not _ENABLED:
        return
    debug_logger.debug(
        f"[MASK]   done: {span_count} spans masked, "
        f"{text_len} chars → {masked_len} chars"
    )


# ─── Demasking logging ─────────────────────────────────────────────────────

def log_demask(fake: str, real: str, conversation_id: str) -> None:
    """Log each fake→real restoration during demasking."""
    if not _ENABLED:
        return
    debug_logger.debug(
        f"[DEMASK] '{_truncate(fake)}' → '{_truncate(real)}' (conv={conversation_id})"
    )


def log_demask_result(count: int, conversation_id: str) -> None:
    """Log a summary after demasking."""
    if not _ENABLED:
        return
    debug_logger.debug(
        f"[DEMASK] done: {count} replacements restored (conv={conversation_id})"
    )
