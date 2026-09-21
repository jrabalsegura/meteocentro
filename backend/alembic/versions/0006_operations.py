"""Persistent worker heartbeat for operation diagnostics."""

import sqlalchemy as sa

from alembic import op

revision = "0006_operations"
down_revision = "0005_administration"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_heartbeat",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("worker_heartbeat")
