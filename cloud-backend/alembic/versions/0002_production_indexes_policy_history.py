"""One policy per (org, entity type), (org, timestamp) audit index, policy change history.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older duplicates would block the constraint: keep the newest row per (org, entity type).
    op.execute("DELETE FROM policies WHERE id NOT IN (SELECT MAX(id) FROM policies GROUP BY org_id, entity_type)")
    op.create_index("uq_policies_org_entity", "policies", ["org_id", "entity_type"], unique=True)
    op.create_index("ix_audit_events_org_ts", "audit_events", ["org_id", "timestamp"])
    op.create_table(
        "policy_changes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("old_action", sa.String(32)),
        sa.Column("new_action", sa.String(32)),
        sa.Column("changed_at", sa.DateTime),
    )
    op.create_index("ix_policy_changes_id", "policy_changes", ["id"])
    op.create_index("ix_policy_changes_org_ts", "policy_changes", ["org_id", "changed_at"])


def downgrade() -> None:
    op.drop_table("policy_changes")
    op.drop_index("ix_audit_events_org_ts", "audit_events")
    op.drop_index("uq_policies_org_entity", "policies")
