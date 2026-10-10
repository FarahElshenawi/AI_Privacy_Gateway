"""Per-label minimum scores from the bake-off (`DLP_MIN_SCORES_FILE`).

The bake-off report recommends, per label, the lowest model score that still meets the recall target.
Point DLP_MIN_SCORES_FILE at a JSON file, either {"PERSON": 0.35, ...} or a report containing
{"min_scores": {...}}, and the merge engine drops NON-validated spans scoring below their label's floor.

Safety rules:
  * a bad file (missing, not JSON, value outside 0..1) stops startup: silently running without the
    thresholds you configured would change what gets masked;
  * labels in the critical-secret set can never get a floor (a context-only API key must not be
    dropped because a model score was low); they are reported as ignored;
  * validated spans (Luhn, checksums...) always pass, as in MergeEngine.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("privacy_gateway.thresholds")


class ThresholdsError(RuntimeError):
    pass


def parse_min_scores(raw: object) -> tuple[dict[str, float], list[str]]:
    from dlp_core.policy import _IMMUTABLE_CRITICAL_SECRETS, normalize_label
    if isinstance(raw, dict) and isinstance(raw.get("min_scores"), dict):
        raw = raw["min_scores"]
    if not isinstance(raw, dict):
        raise ThresholdsError("expected a JSON object of label -> score")
    out: dict[str, float] = {}
    ignored: list[str] = []
    for label, val in raw.items():
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0.0 <= float(val) <= 1.0:
            raise ThresholdsError(f"score for {label!r} must be a number between 0 and 1")
        canon = normalize_label(str(label))
        if not canon:
            raise ThresholdsError(f"bad label {label!r}")
        if canon in _IMMUTABLE_CRITICAL_SECRETS:
            ignored.append(canon)
            continue
        out[canon] = float(val)
    return out, sorted(ignored)


def load_min_scores(path: Optional[str] = None) -> dict[str, float]:
    path = path or os.environ.get("DLP_MIN_SCORES_FILE", "")
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        raise ThresholdsError(f"cannot read DLP_MIN_SCORES_FILE ({type(exc).__name__})") from exc
    scores, ignored = parse_min_scores(raw)
    if ignored:
        logger.warning("Ignoring thresholds for critical labels: %s", ", ".join(ignored))
    logger.info("Loaded %d per-label thresholds from %s", len(scores), path)
    return scores
