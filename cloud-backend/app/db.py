"""Database layer for the Cloud Control Plane.

SQLAlchemy models for organizations, policies, audit events, and endpoints.
"""
from __future__ import annotations

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

def init_db(db_url: str = "sqlite:///./cloud_backend.db"):
    """Create all tables."""
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    """Get a database session."""
    Session = sessionmaker(bind=engine)
    return Session()


def seed_defaults(engine):
    """Seed the database with default policies."""
    session = get_session(engine)

    # Ensure the default organization exists (policies reference org_id=1)
    import secrets
    if not session.query(Organization).filter(Organization.id == 1).first():
        session.add(Organization(
            id=1,
            name="Default Organization",
            api_key=secrets.token_hex(32),
        ))
        session.commit()

    # Check if defaults already exist
    if session.query(Policy).filter(Policy.is_default == True).first():
        session.close()
        return

    defaults = [
        ("PERSON", "faker"),
        ("EMAIL", "faker"),
        ("PHONE_NUMBER", "faker"),
        ("ORGANIZATION", "faker"),
        ("ADDRESS", "faker"),
        ("USERNAME", "faker"),
        ("CREDIT_CARD", "redact"),
        ("API_KEY", "redact"),
        ("JWT", "redact"),
        ("PEM_BLOCK", "redact"),
        ("IBAN", "redact"),
        ("SSN", "redact"),
        ("URL", "keep"),
        ("IPV4", "keep"),
        ("IPV6", "keep"),
    ]

    for entity_type, action in defaults:
        session.add(Policy(
            org_id=1,  # default org
            entity_type=entity_type,
            action=action,
            is_default=True,
        ))

    session.commit()
    session.close()
