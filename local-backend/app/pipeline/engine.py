"""Engine facade: the shared singleton wiring dlp_core for the FastAPI layer.

Holds the DetectionPipeline (Tier 1 critical + Tier 2 non-critical), the OffsetMasker + sealed
vault, and the Demasker. Everything that can be tuned is an environment variable:

    DLP_TIER1_TIMEOUT_S   default 2.0    Tier 1 is critical: exceeding this BLOCKS the request
    DLP_TIER2_TIMEOUT_S   default 3.0    Tier 2 exceeding this degrades (names/orgs/locations uncovered)
    The two timeouts are BASE budgets: each call also gets DLP_TIERn_PER_KCHAR_S per 1000 characters
    (defaults 0.05 / 0.5), capped at DLP_TIERn_MAX_S for prompts (10 / 20) and DLP_TIERn_FILE_MAX_S for
    file batches (60 / 300), so a 100 KB file batch is not judged like a chat prompt.
    DLP_MIN_SCORES_FILE   optional       JSON of per-label minimum scores from the bake-off (see app/thresholds.py)
    DLP_IMAGE_POLICY      default default  default | block | warn: what to do with images (see multimodal/pipeline.py)
    DLP_FILE_STRICT       default true   /process_file fails (422) when any tier was degraded for the file
    DLP_TIER2_ENABLED     default true   false = run Tier 1 only (reported as degraded/uncovered)
    DLP_WARM_TIER2        default true   load the model at startup in a background thread
    DLP_STRICT            default false  true = safe_to_send is False whenever coverage is incomplete
    DLP_MAX_TEXT_CHARS    default 200000 /mask and /detect reject longer text (files are batched instead)

Vault persistence (env vars):
    DLP_VAULT_PERSIST     default true   false = use InMemoryVault (mappings die on restart)
    DLP_VAULT_DB_PATH     default ~/.pii_gateway_vault.db  SQLite path for PersistentVault
    DLP_VAULT_KEY         optional       base64 Fernet key; if unset, ~/.pii_gateway_vault.key
                                          is created with 0600 perms on first run
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from dlp_core import Demasker, FernetSealer, InMemoryVault, OffsetMasker, PersistentVault
from dlp_core.detection import DetectionPipeline, DetectionResult, DetectorSpec
from dlp_core.merge import MergeEngine
from dlp_core.residual_scanner import scan as residual_scan
from dlp_core.tier1 import Tier1Config, Tier1Engine
from dlp_core.tier2 import Tier2Config, Tier2Engine
from app.thresholds import load_min_scores
from dlp_core.vault import ensure_vault_key_file, resolve_vault_key


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


TIER1_TIMEOUT_S = _env_float("DLP_TIER1_TIMEOUT_S", 2.0)
TIER2_TIMEOUT_S = _env_float("DLP_TIER2_TIMEOUT_S", 3.0)
FILE_STRICT_DEFAULT = _env_bool("DLP_FILE_STRICT", True)
IMAGE_POLICIES = ("default", "block", "warn")
IMAGE_POLICY = os.environ.get("DLP_IMAGE_POLICY", "default").strip().lower()
if IMAGE_POLICY not in IMAGE_POLICIES:
    raise RuntimeError(f"DLP_IMAGE_POLICY must be one of {IMAGE_POLICIES}")
STRICT_DEFAULT = _env_bool("DLP_STRICT", False)
WARM_TIER2 = _env_bool("DLP_WARM_TIER2", True)
MAX_TEXT_CHARS = int(_env_float("DLP_MAX_TEXT_CHARS", 200_000))

# ── Surrogates: one realistic fake per label. Missing Faker => FAKER spans degrade to REDACT. ──
try:
    from faker import Faker
    _faker = Faker()
    _faker_lock = threading.Lock()

    def _surrogate(label: str, real: str) -> str:
        label = label.upper()
        with _faker_lock:
            if label == "PERSON":        return _faker.name()
            if label == "EMAIL":         return _faker.email()
            if label == "PHONE_NUMBER":  return _faker.phone_number()
            if label == "ORGANIZATION":  return _faker.company()
            if label == "ADDRESS":       return _faker.street_address()
            if label == "LOCATION":      return _faker.city()
            if label == "USERNAME":      return _faker.user_name()
            if label == "DATE_OF_BIRTH": return _faker.date_of_birth().isoformat()
            return _faker.word()
except ImportError:
    _surrogate = None  # type: ignore[assignment]


# ── Vault: PersistentVault by default (survives restarts), InMemoryVault if disabled. ──
# Persistence is critical for demasking: without it, every backend restart loses every
# fake↔real mapping and /demask becomes useless for any conversation older than the restart.
def _build_vault():
    if not _env_bool("DLP_VAULT_PERSIST", True):
        return InMemoryVault(FernetSealer())
    db_path = os.environ.get("DLP_VAULT_DB_PATH",
                             str(Path.home() / ".pii_gateway_vault.db"))
    key = resolve_vault_key() or ensure_vault_key_file()
    return PersistentVault(FernetSealer(key), db_path)


_vault = _build_vault()
_tier1 = Tier1Engine(Tier1Config())
_tier2 = Tier2Engine(Tier2Config(enabled=_env_bool("DLP_TIER2_ENABLED", True)))

if os.environ.get("GLINER_ONNX_PATH"):
    import warnings
    warnings.warn("GLINER_ONNX_PATH is ignored: ONNX inference is not implemented for Tier 2.")

_pipeline = DetectionPipeline([
    DetectorSpec(_tier1, critical=True, timeout_s=TIER1_TIMEOUT_S,
                 per_kchar_s=_env_float("DLP_TIER1_PER_KCHAR_S", 0.05),
                 max_s=_env_float("DLP_TIER1_MAX_S", 10.0), file_max_s=_env_float("DLP_TIER1_FILE_MAX_S", 60.0)),
    DetectorSpec(_tier2, critical=False, timeout_s=TIER2_TIMEOUT_S,
                 per_kchar_s=_env_float("DLP_TIER2_PER_KCHAR_S", 0.5),
                 max_s=_env_float("DLP_TIER2_MAX_S", 20.0), file_max_s=_env_float("DLP_TIER2_FILE_MAX_S", 300.0)),
], merge=MergeEngine(min_scores=load_min_scores()))
_masker = OffsetMasker(_vault, _surrogate)
_demasker = Demasker(_vault)


def detect(text: str) -> DetectionResult:
    return _pipeline.run(text)


def apply_tenant_config(deny_terms, tenant_domains, image_policy=None) -> None:
    """Hot-apply the cloud-managed tenant settings: Tier 1 deny terms + internal-only domains, and the
    image policy (default | block | warn; see app/multimodal/pipeline.py)."""
    from dataclasses import replace
    global IMAGE_POLICY
    if image_policy in IMAGE_POLICIES:
        IMAGE_POLICY = image_policy
    _tier1.reconfigure(replace(_tier1.config, deny_terms=tuple(deny_terms), tenant_domains=tuple(tenant_domains)))


def detect_file(text: str) -> DetectionResult:
    """Same detectors, with the larger file-batch time budget."""
    return _pipeline.run(text, mode="file")


def warm_tier2() -> None:
    """Load Tier 2 in the background so no user request pays for the model load."""
    if WARM_TIER2:
        _tier2.warmup()


def tier_status() -> dict:
    """Coarse status for /health (codes only, no text)."""
    t2 = "ready" if _tier2.available and _tier2.stats()["state"] == "ready" else (_tier2.unavailable_reason or "idle")
    return {"tier1": "ready", "tier2": t2}


def degraded_reasons(result: DetectionResult) -> list[str]:
    return [f"{r.name}:{r.error or r.status.value}" for r in result.reports if r.status.value != "ok"]
