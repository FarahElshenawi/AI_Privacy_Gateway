"""Cloud sync — bridge between the local backend and the cloud control plane.

Three responsibilities, all running in background threads:
  1. Enrollment + heartbeat: enroll on first startup, then heartbeat every 60s.
     Lets the dashboard show which devices are online.
  2. Audit push: every 10s, flush buffered audit records to POST /api/audit.
     The local stdlib logging handler captures records; this module ships them.
  3. Policy pull: every 5 min, GET /api/policies/export and update the local
     routing table. Dashboard policy changes take effect without a restart.

The module is designed to be optional: if CLOUD_URL or CLOUD_API_KEY is unset,
all three loops are no-ops and the local backend runs standalone (as before).

Configuration (env vars):
    CLOUD_URL              required  e.g. https://doppel-cloud.acme.io
    CLOUD_API_KEY          required  the org's API key (X-API-Key header)
    CLOUD_SYNC_ENABLED     default true   set false to disable all sync
    CLOUD_HEARTBEAT_INTERVAL_S   default 60
    CLOUD_AUDIT_FLUSH_INTERVAL_S default 10
    CLOUD_POLICY_PULL_INTERVAL_S default 300

Privacy contract:
  - Audit records pushed to the cloud contain ONLY metadata (entity_type,
    action, count, latency). Never the text, never the real value.
  - The local audit logger handler captures records as they are emitted by
    emit_audit() / emit_demask_audit() and buffers them for the push loop.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import time
from collections import deque
from typing import Optional

import urllib.request
import urllib.error

logger = logging.getLogger("privacy_gateway.cloud_sync")

# --- Configuration ----------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


CLOUD_URL = os.environ.get("CLOUD_URL", "").rstrip("/")
CLOUD_API_KEY = os.environ.get("CLOUD_API_KEY", "")
CLOUD_SYNC_ENABLED = _env_bool("CLOUD_SYNC_ENABLED", True)
HEARTBEAT_INTERVAL_S = _env_int("CLOUD_HEARTBEAT_INTERVAL_S", 60)
AUDIT_FLUSH_INTERVAL_S = _env_int("CLOUD_AUDIT_FLUSH_INTERVAL_S", 10)
POLICY_PULL_INTERVAL_S = _env_int("CLOUD_POLICY_PULL_INTERVAL_S", 300)

# How many audit records to buffer before dropping (if the cloud is unreachable).
# Oldest are dropped first — we never block the masking path on the cloud.
AUDIT_BUFFER_MAX = 10_000


# --- Audit buffer + logging handler -----------------------------------------

class _CloudAuditBuffer:
    """Thread-safe ring buffer of audit records waiting to be pushed to the cloud.

    The local audit handler (installed below) appends records here. The push
    loop drains the buffer every AUDIT_FLUSH_INTERVAL_S seconds. If the cloud is
    unreachable, records accumulate up to AUDIT_BUFFER_MAX, then the oldest are
    dropped (we never block or fail the masking path on cloud sync).
    """

    def __init__(self, max_size: int = AUDIT_BUFFER_MAX) -> None:
        self._buf: deque = deque()
        self._max = max_size
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        with self._lock:
            if len(self._buf) >= self._max:
                self._buf.popleft()  # drop oldest
            self._buf.append(record)

    def requeue(self, records: list[dict]) -> None:
        """Put records that could not be delivered back at the FRONT (oldest first),
        still bounded by max size (newest records win when over capacity)."""
        with self._lock:
            merged = list(records) + list(self._buf)
            self._buf = deque(merged[-self._max:])

    def drain(self) -> list[dict]:
        with self._lock:
            items = list(self._buf)
            self._buf.clear()
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


_audit_buffer = _CloudAuditBuffer()


_CONV_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_LABEL_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _safe_conversation_id(value: Optional[str]) -> Optional[str]:
    """The cloud schema only accepts this charset/length; anything else is dropped (not sent)."""
    return value if value and _CONV_RE.match(value) else None


def _safe_label(value: Optional[str]) -> str:
    v = (value or "").upper()
    return v if _LABEL_RE.match(v) else "UNKNOWN"


class CloudAuditHandler(logging.Handler):
    """Captures audit records emitted by emit_audit() / emit_demask_audit()
    and buffers them for the cloud push loop.

    Installed on the `privacy_gateway.audit` logger. Parses the structured
    key=value log line back into a dict for the cloud /api/audit endpoint.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            # The audit log format is "<event_kind> k1=v1 k2=v2 ..."
            # Parse the trailing key=value pairs into a dict.
            parts = msg.split()
            if not parts:
                return
            event_kind = parts[0]  # "routing_decision" or "demask_event"
            kv: dict = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    kv[k] = v
            # Map to the cloud's AuditEventCreate schema
            conv = _safe_conversation_id(kv.get("conversation_id"))
            if event_kind == "routing_decision":
                cloud_event = {
                    "event_type": "mask",
                    "entity_types": {_safe_label(kv.get("entity_type")): 1},
                    "entity_count": 1,
                    "conversation_id": conv,
                }
            elif event_kind == "demask_event":
                cloud_event = {
                    "event_type": "demask",
                    "entity_types": {},
                    "entity_count": max(0, int(kv.get("replacements_made", 0))),
                    "conversation_id": conv,
                }
            else:
                return
            _audit_buffer.append(cloud_event)
        except Exception:
            # Never let audit handling break the masking path
            pass


# --- HTTP helpers (stdlib only, no requests dependency) ---------------------

def _post_json(url: str, api_key: str, body: dict, timeout: float = 5.0) -> Optional[dict]:
    """POST JSON to the cloud backend. Returns the parsed response, or None on failure."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        logger.debug("cloud POST %s failed: %s", url, e)
        return None


def _get_json(url: str, api_key: str, timeout: float = 5.0) -> Optional[dict]:
    """GET JSON from the cloud backend. Returns the parsed response, or None on failure."""
    req = urllib.request.Request(
        url, method="GET",
        headers={"X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        logger.debug("cloud GET %s failed: %s", url, e)
        return None


# --- Enrollment + heartbeat --------------------------------------------------

_enrollment_token: Optional[str] = None
_endpoint_id: Optional[int] = None
_enrollment_lock = threading.Lock()


def _hostname() -> str:
    try:
        return socket.gethostname()[:255]
    except Exception:
        return "unknown"


def _enroll() -> bool:
    """Enroll this local backend with the cloud. Returns True on success."""
    global _enrollment_token, _endpoint_id
    if not CLOUD_URL or not CLOUD_API_KEY:
        return False
    # Local backend version (could come from a __version__ module, hardcode for now)
    version = "1.0.0"
    body = {"hostname": _hostname(), "version": version}
    resp = _post_json(f"{CLOUD_URL}/api/endpoints/enroll", CLOUD_API_KEY, body)
    if resp and "enrollment_token" in resp:
        with _enrollment_lock:
            _enrollment_token = resp["enrollment_token"]
            _endpoint_id = resp.get("endpoint_id")
        logger.info("Enrolled as endpoint %s", _endpoint_id)
        return True
    logger.warning("Enrollment failed")
    return False


def _heartbeat() -> None:
    """Send a single heartbeat. Re-enrolls if the token is missing."""
    global _enrollment_token
    if not CLOUD_URL or not CLOUD_API_KEY:
        return
    if _enrollment_token is None:
        _enroll()
        return
    body = {"enrollment_token": _enrollment_token, "version": "1.0.0"}
    resp = _post_json(f"{CLOUD_URL}/api/endpoints/heartbeat", CLOUD_API_KEY, body)
    if resp is None:
        # Heartbeat failed — token may be invalid. Re-enroll next cycle.
        with _enrollment_lock:
            _enrollment_token = None


def _heartbeat_loop(stop_event: threading.Event) -> None:
    """Background loop: heartbeat every HEARTBEAT_INTERVAL_S seconds."""
    while not stop_event.is_set():
        try:
            _heartbeat()
        except Exception as e:
            logger.debug("heartbeat error: %s", e)
        stop_event.wait(HEARTBEAT_INTERVAL_S)


# --- Audit push -------------------------------------------------------------

def _aggregate(events: list[dict]) -> list[dict]:
    """Collapse per-entity events into one event per (event_type, conversation_id).
    Fewer HTTP calls; the cloud stats only need counts."""
    groups: dict[tuple, dict] = {}
    for e in events:
        key = (e["event_type"], e.get("conversation_id"))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"event_type": e["event_type"], "entity_types": {},
                               "entity_count": 0, "conversation_id": e.get("conversation_id")}
        g["entity_count"] += int(e.get("entity_count", 0))
        for label, n in (e.get("entity_types") or {}).items():
            g["entity_types"][label] = g["entity_types"].get(label, 0) + int(n)
    return list(groups.values())


def _flush_audit() -> None:
    """Drain the buffer and POST aggregated events. Anything the cloud didn't accept is put
    back so a cloud outage delays audit instead of losing it (bounded by AUDIT_BUFFER_MAX)."""
    if not CLOUD_URL or not CLOUD_API_KEY:
        _audit_buffer.drain()   # sync not configured: don't let the buffer grow
        return
    events = _audit_buffer.drain()
    if not events:
        return
    failed: list[dict] = []
    for i, event in enumerate(_aggregate(events)):
        if _post_json(f"{CLOUD_URL}/api/audit", CLOUD_API_KEY, event) is None:
            failed.append(event)
            # Cloud likely down: stop hammering it, requeue the rest as-is.
            rest = _aggregate(events)[i + 1:]
            failed.extend(rest)
            break
    if failed:
        _audit_buffer.requeue(failed)


def _audit_push_loop(stop_event: threading.Event) -> None:
    """Background loop: flush audit buffer every AUDIT_FLUSH_INTERVAL_S seconds."""
    while not stop_event.is_set():
        try:
            _flush_audit()
        except Exception as e:
            logger.debug("audit push error: %s", e)
        stop_event.wait(AUDIT_FLUSH_INTERVAL_S)


# --- Policy pull ------------------------------------------------------------

def apply_cloud_policies(policies: dict) -> tuple[int, list[str]]:
    """Apply a {LABEL: "faker"|"redact"|"keep"} map to the LIVE masking path.

    Masking reads two tables: the merge engine's label->action Policy (decides what happens to a
    span) and the active routing table (storage/audit metadata). Both are updated, atomically
    per table, starting from the built-in defaults so labels removed in the cloud revert.

    Safety: unknown actions are ignored, and the local guard in configure_policy_override
    refuses to downgrade critical secrets (cards, keys, SSN, ...) to KEEP. Rejected labels are
    returned so they can be logged. Returns (applied_count, rejected_labels).
    """
    from dlp_core import policy as P
    from dlp_core.policy import Action, Policy, PolicyConfigError

    actions = {"faker": Action.FAKER, "redact": Action.REDACT, "keep": Action.KEEP}
    table = dict(P.DEFAULT_ACTIONS)
    rejected: list[str] = []
    accepted: dict[str, Action] = {}
    P.reset_policy_to_defaults()
    for label, action_str in policies.items():
        act = actions.get(str(action_str).lower())
        canon = P.normalize_label(str(label))
        if act is None or not canon:
            rejected.append(str(label)); continue
        try:
            P.configure_policy_override(canon, act)
        except PolicyConfigError:
            rejected.append(canon); continue
        accepted[canon] = act
    table.update(accepted)
    from app.pipeline import engine
    engine._pipeline.set_policy(Policy(table))
    return len(accepted), rejected


def _pull_and_apply_policies() -> None:
    """GET /api/policies/export and apply it to the running masking pipeline."""
    if not CLOUD_URL or not CLOUD_API_KEY:
        return
    policies = _get_json(f"{CLOUD_URL}/api/policies/export", CLOUD_API_KEY)
    if not isinstance(policies, dict):      # None = fetch failed; {} = valid (all defaults)
        return
    try:
        applied, rejected = apply_cloud_policies(policies)
        logger.info("Applied %d cloud policies%s", applied,
                    f" (rejected: {', '.join(rejected)})" if rejected else "")
    except Exception as e:
        logger.warning("policy apply error: %s", e)


def _policy_pull_loop(stop_event: threading.Event) -> None:
    """Background loop: pull policies every POLICY_PULL_INTERVAL_S seconds."""
    # First pull immediately on startup so cloud overrides apply ASAP.
    try:
        _pull_and_apply_policies()
    except Exception as e:
        logger.debug("initial policy pull error: %s", e)
    while not stop_event.is_set():
        stop_event.wait(POLICY_PULL_INTERVAL_S)
        try:
            _pull_and_apply_policies()
        except Exception as e:
            logger.debug("policy pull error: %s", e)


def _url_is_secure(url: str) -> bool:
    from urllib.parse import urlparse
    u = urlparse(url)
    return u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1"))


# --- Lifecycle --------------------------------------------------------------

_stop_event = threading.Event()
_threads: list[threading.Thread] = []


def start() -> None:
    """Install the audit handler and start the three background loops.

    Call from app.main lifespan startup. No-op if CLOUD_SYNC_ENABLED is false
    or if CLOUD_URL / CLOUD_API_KEY are unset.
    """
    if not CLOUD_SYNC_ENABLED:
        logger.info("Cloud sync disabled by CLOUD_SYNC_ENABLED=false")
        return
    if not CLOUD_URL or not CLOUD_API_KEY:
        logger.info("Cloud sync disabled: CLOUD_URL or CLOUD_API_KEY not set")
        return
    if not _url_is_secure(CLOUD_URL):
        logger.error("Cloud sync disabled: CLOUD_URL must be https:// (or localhost). "
                     "The API key and policies must not travel over plain http.")
        return

    # Install the audit handler so emit_audit() / emit_demask_audit() records
    # get captured into _audit_buffer.
    audit_logger = logging.getLogger("privacy_gateway.audit")
    audit_logger.addHandler(CloudAuditHandler())
    audit_logger.setLevel(logging.INFO)

    # Start the three background threads.
    for name, target in (
        ("cloud-heartbeat", _heartbeat_loop),
        ("cloud-audit-push", _audit_push_loop),
        ("cloud-policy-pull", _policy_pull_loop),
    ):
        t = threading.Thread(target=target, args=(_stop_event,), name=name, daemon=True)
        t.start()
        _threads.append(t)
    logger.info("Cloud sync started: %s", CLOUD_URL)


def stop() -> None:
    """Signal the background loops to stop. Call from app.main lifespan shutdown."""
    _stop_event.set()
    for t in _threads:
        t.join(timeout=2.0)
    _threads.clear()


def status() -> dict:
    """Coarse status for /health (no secrets)."""
    return {
        "enabled": CLOUD_SYNC_ENABLED and bool(CLOUD_URL and CLOUD_API_KEY),
        "cloud_url": CLOUD_URL or None,
        "enrolled": _enrollment_token is not None,
        "endpoint_id": _endpoint_id,
        "audit_buffered": len(_audit_buffer),
    }
