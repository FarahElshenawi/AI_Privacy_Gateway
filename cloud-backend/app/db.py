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
    ForeignKey, JSON, func,
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


class Endpoint(Base):
    """A registered local backend installation."""
    __tablename__ = "endpoints"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    hostname = Column(String(255))                     # machine hostname
    enrollment_token = Column(String(64), unique=True, index=True)
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


# === Database setup ===

DEFAULT_DB_URL = "sqlite:///./cloud_backend.db"
_engines: dict[str, object] = {}
_engines_lock = threading.Lock()


def database_url() -> str:
    """DATABASE_URL (see .env.example), or DB_URL (older docs), or a local SQLite file."""
    return os.getenv("DATABASE_URL") or os.getenv("DB_URL") or DEFAULT_DB_URL


def init_db(db_url: str | None = None):
    """Create all tables and return the (cached, one-per-URL) engine.

    Previously every request built a brand-new engine and re-ran create_all; engines own the
    connection pool, so that leaked pools and ignored DATABASE_URL.
    """
    url = db_url or database_url()
    with _engines_lock:
        engine = _engines.get(url)
        if engine is None:
            kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
            engine = create_engine(url, echo=False, **kwargs)
            Base.metadata.create_all(engine)
            _engines[url] = engine
    return engine


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


def create_organization(session, name: str, api_key: str | None = None) -> tuple["Organization", str]:
    """Create an organization. Returns (org, plaintext_key); the key is not recoverable later."""
    key = api_key or secrets.token_urlsafe(32)
    if len(key) < MIN_API_KEY_LEN:
        raise ValueError(f"API key must be at least {MIN_API_KEY_LEN} characters")
    org = Organization(name=name, api_key=hash_api_key(key))
    session.add(org)
    session.commit()
    session.refresh(org)
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

        org = session.query(Organization).order_by(Organization.id.asc()).first()
        if org is None:
            key = env_key or secrets.token_urlsafe(32)
            org = Organization(name="Default Organization", api_key=hash_api_key(key))
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
        elif env_key:
            org.api_key = hash_api_key(env_key)    # rotate/recover (also migrates old plaintext keys)
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
