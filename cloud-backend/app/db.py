"""Database layer for the Cloud Control Plane.

SQLAlchemy models for organizations, policies, audit events, and endpoints.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
import threading
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, Boolean,
    ForeignKey, JSON, func, Index,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Organization(Base):
    """An organization using the PII Gateway."""
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    # SHA-256 hex digest (64 chars) of the organization's API key. The key itself is shown once
    # at creation and never stored, so a leaked database file does not leak working credentials.
    api_key = Column(String(64), nullable=False, unique=True, index=True)
    # SHA-256 digest of the ENROLLMENT key: a separate, low-privilege secret that can only register
    # new endpoints. It is the only credential that has to be put on employee machines.
    enroll_key = Column(String(64), unique=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    policies = relationship("Policy", back_populates="organization")
    endpoints = relationship("Endpoint", back_populates="organization")
    audit_events = relationship("AuditEvent", back_populates="organization")


class Policy(Base):
    """Entity-type → action policy for an organization."""
    __tablename__ = "policies"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    entity_type = Column(String(64), nullable=False)  # e.g. "PERSON", "CREDIT_CARD"
    action = Column(String(32), nullable=False)       # "faker", "redact", "keep"
    is_default = Column(Boolean, default=False)        # system-wide default?
    version = Column(Integer, default=1)               # bumped on each update
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    organization = relationship("Organization", back_populates="policies")

    __table_args__ = (Index("uq_policies_org_entity", "org_id", "entity_type", unique=True),)


class Endpoint(Base):
    """A registered local backend installation."""
    __tablename__ = "endpoints"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    hostname = Column(String(255))                     # machine hostname
    enrollment_token = Column(String(64), unique=True, index=True)   # legacy plaintext; migrated to token_hash
    # SHA-256 digest of this endpoint's own token (shown once at enrollment).
    token_hash = Column(String(64), unique=True, index=True)
    last_seen = Column(DateTime)                       # last heartbeat
    version = Column(String(32))                       # backend version
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="endpoints")


class AuditEvent(Base):
    """Audit event from a local backend — metadata only, never prompt content."""
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    endpoint_id = Column(Integer, ForeignKey("endpoints.id"))
    event_type = Column(String(64), nullable=False)   # "mask", "detect", "file", "fail_closed"
    entity_types = Column(JSON)                        # {"PERSON": 3, "EMAIL": 2}
    entity_count = Column(Integer, default=0)
    latency_ms = Column(Integer)                      # P95 latency
    conversation_id = Column(String(64))              # for grouping
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    organization = relationship("Organization", back_populates="audit_events")

    # Every dashboard query is "this org, this time window".
    __table_args__ = (Index("ix_audit_events_org_ts", "org_id", "timestamp"),)


class PolicyChange(Base):
    """Who/what changed a policy, and from what to what (admin-key actions; no prompt content)."""
    __tablename__ = "policy_changes"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    entity_type = Column(String(64), nullable=False)
    old_action = Column(String(32))                    # None when created
    new_action = Column(String(32))                    # None when deleted
    changed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_policy_changes_org_ts", "org_id", "changed_at"),)


def log_policy_change(db, org, entity_type: str, old: str | None, new: str | None) -> None:
    db.add(PolicyChange(org_id=org.id, entity_type=entity_type, old_action=old, new_action=new))


class TenantConfig(Base):
    """Per-organization detection settings pushed to every endpoint: customer-specific words that
    must never leave (deny_terms) and internal-only domains (tenant_domains). One row per org."""
    __tablename__ = "tenant_config"

    org_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    deny_terms = Column(Text, nullable=False, default="[]")        # JSON list of strings
    tenant_domains = Column(Text, nullable=False, default="[]")    # JSON list of hostnames
    image_policy = Column(String(16), nullable=False, default="default", server_default="default")
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminAction(Base):
    """Who changed what with the admin key (no prompt content, no secrets)."""
    __tablename__ = "admin_actions"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False)        # e.g. "audit.delete", "policy.update"
    target = Column(String(128))                       # e.g. "event:42"
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


def log_admin_action(db, org, action: str, target: str = "") -> None:
    """Record an admin-key action (committed by the caller together with the change)."""
    db.add(AdminAction(org_id=org.id, action=action, target=target[:128]))


# === Database setup ===

DEFAULT_DB_URL = "sqlite:///./cloud_backend.db"
_engines: dict[str, object] = {}
_engines_lock = threading.Lock()


def database_url() -> str:
    """DATABASE_URL (see .env.example), or DB_URL (older docs), or a local SQLite file."""
    return os.getenv("DATABASE_URL") or os.getenv("DB_URL") or DEFAULT_DB_URL


def init_db(db_url: str | None = None):
    """Return the (cached, one-per-URL) engine, with the schema ready.

    SQLite (dev, tests, single-box installs): tables are created and upgraded automatically.
    Any other database (PostgreSQL in production): the schema is owned by Alembic. Run
    `alembic upgrade head` first; startup refuses to run against a database that is not at head,
    so a forgotten migration is an error, not a runtime surprise.
    """
    url = db_url or database_url()
    with _engines_lock:
        engine = _engines.get(url)
        if engine is None:
            is_sqlite = url.startswith("sqlite")
            kwargs = {"connect_args": {"check_same_thread": False}} if is_sqlite else {"pool_pre_ping": True}
            engine = create_engine(url, echo=False, **kwargs)
            if is_sqlite:
                Base.metadata.create_all(engine)
                _migrate(engine)
            else:
                _require_schema_at_head(engine)
            _engines[url] = engine
    return engine


def _require_schema_at_head(engine) -> None:
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    heads = set(ScriptDirectory(_alembic_config().get_main_option("script_location")).get_heads())
    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    if current != heads:
        raise RuntimeError("Database schema is not up to date. Run `alembic upgrade head` "
                           f"(current: {sorted(current) or 'none'}, expected: {sorted(heads)}).")


def _alembic_config():
    from pathlib import Path
    from alembic.config import Config
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return cfg


def _migrate(engine) -> None:
    """Idempotent, additive upgrade for SQLite databases created by older versions
    (create_all never alters existing tables). PostgreSQL uses Alembic instead."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    cols = {t: {c["name"] for c in insp.get_columns(t)} for t in ("organizations", "endpoints", "tenant_config")}
    with engine.begin() as conn:
        if "image_policy" not in cols["tenant_config"]:
            conn.execute(text("ALTER TABLE tenant_config ADD COLUMN image_policy VARCHAR(16) NOT NULL DEFAULT 'default'"))
        if "enroll_key" not in cols["organizations"]:
            conn.execute(text("ALTER TABLE organizations ADD COLUMN enroll_key VARCHAR(64)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_organizations_enroll_key ON organizations (enroll_key)"))
        if "token_hash" not in cols["endpoints"]:
            conn.execute(text("ALTER TABLE endpoints ADD COLUMN token_hash VARCHAR(64)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_endpoints_token_hash ON endpoints (token_hash)"))
        # Old rows kept the token in plaintext: keep only its hash from now on.
        rows = conn.execute(text(
            "SELECT id, enrollment_token FROM endpoints WHERE enrollment_token IS NOT NULL AND token_hash IS NULL")).fetchall()
        for rid, tok in rows:
            conn.execute(text("UPDATE endpoints SET token_hash=:h, enrollment_token=NULL WHERE id=:i"),
                         {"h": hashlib.sha256(tok.encode()).hexdigest(), "i": rid})
        # One policy per (org, entity type): drop older duplicates (keep the newest), then enforce it.
        conn.execute(text(
            "DELETE FROM policies WHERE id NOT IN (SELECT MAX(id) FROM policies GROUP BY org_id, entity_type)"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_policies_org_entity ON policies (org_id, entity_type)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_audit_events_org_ts ON audit_events (org_id, timestamp)"))


def get_session(engine):
    """Get a database session."""
    Session = sessionmaker(bind=engine)
    return Session()


def get_db():
    """FastAPI dependency: one session per request."""
    db = get_session(init_db())
    try:
        yield db
    finally:
        db.close()


# === API keys ===

MIN_API_KEY_LEN = 24


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_organization(session, name: str, api_key: str | None = None,
                        enroll_key: str | None = None) -> tuple["Organization", str]:
    """Create an organization. Returns (org, admin_key); keys are not recoverable later.
    The ENROLLMENT key is available as `org.enroll_key_plain` on the returned object (once)."""
    key = api_key or secrets.token_urlsafe(32)
    ekey = enroll_key or secrets.token_urlsafe(32)
    for k in (key, ekey):
        if len(k) < MIN_API_KEY_LEN:
            raise ValueError(f"keys must be at least {MIN_API_KEY_LEN} characters")
    if key == ekey:
        raise ValueError("the admin key and the enrollment key must differ")
    org = Organization(name=name, api_key=hash_api_key(key), enroll_key=hash_api_key(ekey))
    session.add(org)
    session.commit()
    session.refresh(org)
    org.enroll_key_plain = ekey      # not a column: only for the caller to display once
    return org, key


def seed_defaults(engine):
    """Seed default organization and ensure all canonical policies exist.
    
    Additive and idempotent:
    - Creates default organization if missing.
    - Preserves existing administrator-configured policies without resetting actions.
    - Migrates legacy SSN -> US_SSN while preserving custom action/version.
    - Removes legacy static IPV4/IPV6 default records.
    - Adds missing canonical policies from CANONICAL_DEFAULT_POLICIES.
    """
    from app.defaults import CANONICAL_DEFAULT_POLICIES

    session = get_session(engine)
    try:
        env_key = os.getenv("CLOUD_ADMIN_API_KEY")
        if env_key is not None and len(env_key) < MIN_API_KEY_LEN:
            # Fail closed: never start with a guessable admin key.
            raise RuntimeError(f"CLOUD_ADMIN_API_KEY must be at least {MIN_API_KEY_LEN} characters")

        env_enroll = os.getenv("CLOUD_ENROLL_KEY")
        if env_enroll is not None and len(env_enroll) < MIN_API_KEY_LEN:
            raise RuntimeError(f"CLOUD_ENROLL_KEY must be at least {MIN_API_KEY_LEN} characters")
        if env_enroll and env_key and env_enroll == env_key:
            raise RuntimeError("CLOUD_ENROLL_KEY must differ from CLOUD_ADMIN_API_KEY")

        org = session.query(Organization).order_by(Organization.id.asc()).first()
        if org is None:
            key = env_key or secrets.token_urlsafe(32)
            ekey = env_enroll or secrets.token_urlsafe(32)
            org = Organization(name="Default Organization", api_key=hash_api_key(key), enroll_key=hash_api_key(ekey))
            session.add(org)
            session.commit()
            session.refresh(org)
            if not env_key:
                # No key configured: show the generated one ONCE (only its hash is stored).
                print("\n" + "=" * 70 +
                      "\n  Doppel cloud backend: admin API key for the default organization\n"
                      "  (shown once; send it as the X-API-Key header):\n\n"
                      f"    {key}\n\n"
                      "  Set CLOUD_ADMIN_API_KEY to choose your own, or to rotate it.\n" +
                      "=" * 70 + "\n", file=sys.stderr)
            if not env_enroll:
                print("  Enrollment key (give THIS to employee machines, never the admin key;\n"
                      "  shown once; they send it as X-Enroll-Key):\n\n"
                      f"    {ekey}\n", file=sys.stderr)
        else:
            if env_key:
                org.api_key = hash_api_key(env_key)    # rotate/recover (also migrates old plaintext keys)
            if env_enroll:
                org.enroll_key = hash_api_key(env_enroll)
            elif org.enroll_key is None:
                ekey = secrets.token_urlsafe(32)       # upgrade of an older database
                org.enroll_key = hash_api_key(ekey)
                print("\n  Doppel cloud backend: NEW enrollment key for employee machines "
                      f"(shown once; X-Enroll-Key):\n\n    {ekey}\n", file=sys.stderr)
            session.commit()

        # Query all existing policies for the default organization
        existing_policies = {
            p.entity_type: p
            for p in session.query(Policy).filter(Policy.org_id == org.id).all()
        }

        # 1. Migrate legacy SSN -> US_SSN preserving existing action and configuration
        if "SSN" in existing_policies:
            ssn_policy = existing_policies["SSN"]
            if "US_SSN" not in existing_policies:
                ssn_policy.entity_type = "US_SSN"
                existing_policies["US_SSN"] = ssn_policy
            else:
                session.delete(ssn_policy)
            del existing_policies["SSN"]

        # 2. Clean up legacy static IP records (IP routing is value-dependent)
        for ip_type in ("IPV4", "IPV6"):
            if ip_type in existing_policies and existing_policies[ip_type].is_default:
                session.delete(existing_policies[ip_type])
                del existing_policies[ip_type]

        # 3. Add any missing canonical policies without touching existing ones
        for entity_type, default_action in CANONICAL_DEFAULT_POLICIES.items():
            if entity_type not in existing_policies:
                session.add(Policy(
                    org_id=org.id,
                    entity_type=entity_type,
                    action=default_action,
                    is_default=True,
                ))

        session.commit()
    finally:
        session.close()
