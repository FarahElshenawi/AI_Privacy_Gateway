"""Baseline: the schema as of the credential split and tenant config.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("api_key", sa.String(64), nullable=False),
        sa.Column("enroll_key", sa.String(64)),
        sa.Column("is_active", sa.Boolean),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_organizations_id", "organizations", ["id"])
    op.create_index("ix_organizations_api_key", "organizations", ["api_key"], unique=True)
    op.create_index("ix_organizations_enroll_key", "organizations", ["enroll_key"], unique=True)

    op.create_table(
        "policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("is_default", sa.Boolean),
        sa.Column("version", sa.Integer),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index("ix_policies_id", "policies", ["id"])

    op.create_table(
        "endpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("hostname", sa.String(255)),
        sa.Column("enrollment_token", sa.String(64)),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("last_seen", sa.DateTime),
        sa.Column("version", sa.String(32)),
        sa.Column("is_active", sa.Boolean),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_endpoints_id", "endpoints", ["id"])
    op.create_index("ix_endpoints_enrollment_token", "endpoints", ["enrollment_token"], unique=True)
    op.create_index("ix_endpoints_token_hash", "endpoints", ["token_hash"], unique=True)

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("endpoint_id", sa.Integer, sa.ForeignKey("endpoints.id")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("entity_types", sa.JSON),
        sa.Column("entity_count", sa.Integer),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("conversation_id", sa.String(64)),
        sa.Column("timestamp", sa.DateTime),
    )
    op.create_index("ix_audit_events_id", "audit_events", ["id"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])

    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128)),
        sa.Column("timestamp", sa.DateTime),
    )
    op.create_index("ix_admin_actions_id", "admin_actions", ["id"])
    op.create_index("ix_admin_actions_org_id", "admin_actions", ["org_id"])
    op.create_index("ix_admin_actions_timestamp", "admin_actions", ["timestamp"])

    op.create_table(
        "tenant_config",
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("deny_terms", sa.Text, nullable=False),
        sa.Column("tenant_domains", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime),
    )


def downgrade() -> None:
    for t in ("tenant_config", "admin_actions", "audit_events", "endpoints", "policies", "organizations"):
        op.drop_table(t)
