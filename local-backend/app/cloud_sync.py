"""Cloud sync — bridge between the local backend and the cloud control plane.

Three responsibilities, all running in background threads:
  1. Enrollment + heartbeat: enroll on first startup, then heartbeat every 60s.
     Lets the dashboard show which devices are online.
  2. Audit push: every 10s, flush buffered audit records to POST /api/audit.
     API handlers call record_event(); this module ships them.
  3. Policy pull: every 5 min, GET /api/policies/export and update the local
     routing table. Dashboard policy changes take effect without a restart.

The module is designed to be optional: if CLOUD_URL or CLOUD_ENROLL_KEY is unset (and no saved device token exists),
all three loops are no-ops and the local backend runs standalone (as before).

Configuration (env vars):
    CLOUD_URL              required  e.g. https://doppel-cloud.acme.io
    CLOUD_ENROLL_KEY       required  the org's ENROLLMENT key (X-Enroll-Key). It can only register
                                     this device; it cannot read or change anything. The org admin
                                     key is never needed (or accepted) here.
    DLP_CLOUD_TOKEN_FILE   default ~/.pii_gateway_cloud_endpoint.json (0600) — the per-device token
                                     returned by enrollment; used for heartbeat, audit, policy pull
    CLOUD_SYNC_ENABLED     default true   set false to disable all sync
    CLOUD_HEARTBEAT_INTERVAL_S   default 60
    CLOUD_AUDIT_FLUSH_INTERVAL_S default 10
    CLOUD_POLICY_PULL_INTERVAL_S default 300

Privacy contract:
  - Audit records pushed to the cloud contain ONLY metadata (entity_type,
    action, count, latency). Never the text, never the real value.
  - Events are queued by record_event() from the API handlers (mask, file, demask, fail_closed),
    with latency. The device token identifies the endpoint server-side.
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
CLOUD_ENROLL_KEY = os.environ.get("CLOUD_ENROLL_KEY", "")
if os.environ.get("CLOUD_API_KEY"):
    logger.warning("CLOUD_API_KEY is no longer used (the admin key must not live on endpoints). "
                   "Set CLOUD_ENROLL_KEY instead.")
TOKEN_FILE = os.environ.get("DLP_CLOUD_TOKEN_FILE") or os.path.join(
    os.path.expanduser("~"), ".pii_gateway_cloud_endpoint.json")
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


def record_event(event_type: str, *, entity_types: Optional[dict] = None, entity_count: Optional[int] = None,
                 latency_ms: Optional[float] = None, conversation_id: Optional[str] = None) -> None:
    """Queue one metadata-only audit event for the cloud (structured: no log-line parsing).

    event_type: mask | detect | file | fail_closed | demask. `entity_types` is {LABEL: count};
    for fail_closed it carries the REASON code instead (e.g. {"POLICY_BLOCK": 1}). Never raises:
    cloud sync must not be able to break the masking path.
    """
    try:
        if event_type not in ("mask", "detect", "file", "fail_closed", "demask"):
            return
        types = {_safe_label(k): max(0, int(v)) for k, v in (entity_types or {}).items()}
        ev: dict = {
            "event_type": event_type,
            "entity_types": types,
            "entity_count": max(0, int(entity_count if entity_count is not None else sum(types.values()))),
            "conversation_id": _safe_conversation_id(conversation_id),
        }
        if latency_ms is not None:
            ev["latency_ms"] = max(0, min(int(latency_ms), 3_600_000))
        _audit_buffer.append(ev)
    except Exception:  # noqa: BLE001
        pass


# --- HTTP helpers (stdlib only, no requests dependency) ---------------------

class _Unauthorized(Exception):
    """The cloud answered 401: the credential was rejected (revoked device, rotated key)."""


def _request(method: str, url: str, headers: dict, body: Optional[dict], timeout: float) -> Optional[dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = dict(headers)
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise _Unauthorized() from e
        logger.debug("cloud %s %s failed: HTTP %s", method, url, e.code)
        return None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as e:
        logger.debug("cloud %s %s failed: %s", method, url, e)
        return None


def _post_json(url: str, headers: dict, body: dict, timeout: float = 5.0) -> Optional[dict]:
    """POST JSON. Returns the parsed response, None on failure; raises _Unauthorized on 401."""
    return _request("POST", url, headers, body, timeout)


def _get_json(url: str, headers: dict, timeout: float = 5.0) -> Optional[dict]:
    """GET JSON. Returns the parsed response, None on failure; raises _Unauthorized on 401."""
    return _request("GET", url, headers, None, timeout)


# --- Device identity (enrollment key -> per-device token) ---------------------

_endpoint_token: Optional[str] = None
_endpoint_id: Optional[int] = None
_token_lock = threading.Lock()
_token_loaded = False


def _load_saved_token() -> None:
    """Reuse the token from a previous run, but only for the same cloud URL."""
    global _endpoint_token, _endpoint_id, _token_loaded
    _token_loaded = True
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("cloud_url") == CLOUD_URL and isinstance(d.get("endpoint_token"), str):
            _endpoint_token, _endpoint_id = d["endpoint_token"], d.get("endpoint_id")
    except (OSError, ValueError):
        pass


def _save_token(token: str, endpoint_id: Optional[int]) -> None:
    try:
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"cloud_url": CLOUD_URL, "endpoint_id": endpoint_id, "endpoint_token": token}, f)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    except OSError as e:
        logger.warning("Could not save the device token (%s); it will re-enroll on restart", e)


def _forget_token() -> None:
    global _endpoint_token, _endpoint_id
    with _token_lock:
        _endpoint_token = None
        _endpoint_id = None
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        pass


def _have_credentials() -> bool:
    if not CLOUD_URL:
        return False
    if not _token_loaded:
        _load_saved_token()
    return bool(_endpoint_token or CLOUD_ENROLL_KEY)


def _auth() -> Optional[dict]:
    """Headers for device calls, enrolling first if there is no token yet."""
    if not _token_loaded:
        _load_saved_token()
    if _endpoint_token is None and not _enroll():
        return None
    return {"X-Endpoint-Token": _endpoint_token} if _endpoint_token else None


def _hostname() -> str:
    try:
        return re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname())[:255] or "unknown"
    except Exception:
        return "unknown"


def _enroll() -> bool:
    """Register this device with the enrollment key and keep the token it returns."""
    global _endpoint_token, _endpoint_id
    if not CLOUD_URL or not CLOUD_ENROLL_KEY:
        return False
    try:
        resp = _post_json(f"{CLOUD_URL}/api/endpoints/enroll", {"X-Enroll-Key": CLOUD_ENROLL_KEY},
                          {"hostname": _hostname(), "version": "1.0.0"})
    except _Unauthorized:
        logger.error("Enrollment rejected: CLOUD_ENROLL_KEY is wrong or was rotated")
        return False
    if resp and resp.get("endpoint_token"):
        with _token_lock:
            _endpoint_token, _endpoint_id = resp["endpoint_token"], resp.get("endpoint_id")
        _save_token(_endpoint_token, _endpoint_id)
        logger.info("Enrolled as endpoint %s", _endpoint_id)
        return True
    logger.warning("Enrollment failed")
    return False


def _heartbeat() -> None:
    """One heartbeat. A 401 means this device was revoked: drop the token and re-enroll next cycle."""
    if not _have_credentials():
        return
    hdr = _auth()
    if hdr is None:
        return
    try:
        _post_json(f"{CLOUD_URL}/api/endpoints/heartbeat", hdr, {"version": "1.0.0"})
    except _Unauthorized:
        _forget_token()


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
    """Collapse per-request events into one event per (event_type, conversation_id): fewer HTTP calls,
    counts summed, latency averaged. fail_closed events are NOT merged (the dashboard counts rows)."""
    groups: dict[tuple, dict] = {}
    lat: dict[tuple, list[int]] = {}
    for i, e in enumerate(events):
        key = (e["event_type"], e.get("conversation_id"), i if e["event_type"] == "fail_closed" else None)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"event_type": e["event_type"], "entity_types": {},
                               "entity_count": 0, "conversation_id": e.get("conversation_id")}
        g["entity_count"] += int(e.get("entity_count", 0))
        for label, n in (e.get("entity_types") or {}).items():
            g["entity_types"][label] = g["entity_types"].get(label, 0) + int(n)
        if e.get("latency_ms") is not None:
            lat.setdefault(key, []).append(int(e["latency_ms"]))
    for key, g in groups.items():
        if lat.get(key):
            g["latency_ms"] = round(sum(lat[key]) / len(lat[key]))
    return list(groups.values())


def _flush_audit() -> None:
    """Drain the buffer and POST aggregated events. Anything the cloud didn't accept is put
    back so a cloud outage delays audit instead of losing it (bounded by AUDIT_BUFFER_MAX)."""
    if not _have_credentials():
        _audit_buffer.drain()   # sync not configured: don't let the buffer grow
        return
    events = _audit_buffer.drain()
    if not events:
        return
    aggregated = _aggregate(events)
    hdr = _auth()
    if hdr is None:                      # cloud unreachable / not enrolled yet: keep everything
        _audit_buffer.requeue(aggregated)
        return
    failed: list[dict] = []
    for i, event in enumerate(aggregated):
        try:
            ok = _post_json(f"{CLOUD_URL}/api/audit", hdr, event) is not None
        except _Unauthorized:
            _forget_token()
            ok = False
        if not ok:
            # Cloud likely down or token revoked: stop hammering it, requeue the rest as-is.
            failed.extend(aggregated[i:])
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


from app.policy_runtime import apply_policies as apply_cloud_policies  # noqa: E402  (shared with /api/policies/apply)


# --- Policy pull ------------------------------------------------------------

def _pull_and_apply_policies() -> None:
    """GET /api/policies/export and apply it to the running masking pipeline."""
    if not _have_credentials():
        return
    hdr = _auth()
    if hdr is None:
        return
    try:
        policies = _get_json(f"{CLOUD_URL}/api/policies/export", hdr)
    except _Unauthorized:
        _forget_token()
        return
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
    or if CLOUD_URL / CLOUD_ENROLL_KEY are unset.
    """
    if not CLOUD_SYNC_ENABLED:
        logger.info("Cloud sync disabled by CLOUD_SYNC_ENABLED=false")
        return
    if not _have_credentials():
        logger.info("Cloud sync disabled: CLOUD_URL or CLOUD_ENROLL_KEY not set")
        return
    if not _url_is_secure(CLOUD_URL):
        logger.error("Cloud sync disabled: CLOUD_URL must be https:// (or localhost). "
                     "The credentials and policies must not travel over plain http.")
        return

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
        "enabled": CLOUD_SYNC_ENABLED and bool(CLOUD_URL and (CLOUD_ENROLL_KEY or _endpoint_token)),
        "cloud_url": CLOUD_URL or None,
        "enrolled": _endpoint_token is not None,
        "endpoint_id": _endpoint_id,
        "audit_buffered": len(_audit_buffer),
    }
