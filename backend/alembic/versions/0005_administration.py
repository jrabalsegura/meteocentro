"""Private administration and durable login throttling."""

from alembic import op
import sqlalchemy as sa

revision = "0005_administration"
down_revision = "0004_history"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_table(
        "catalog_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO catalog_version (id, version) VALUES (1, 0)")


def downgrade():
    op.drop_table("catalog_version")
    op.drop_table("login_throttles")
