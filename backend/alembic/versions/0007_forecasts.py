"""Latest AEMET and Open-Meteo forecasts, kept apart from observations."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0007_forecasts"
down_revision = "0006_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_snapshots",
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("location", sa.String(40), primary_key=True),
        sa.Column("product", sa.String(40), primary_key=True),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_table("forecast_snapshots")
