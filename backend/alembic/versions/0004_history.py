"""Partitioned archive, incremental aggregates and traceable daily imports.

Conversion is transactional and requires a maintenance window (table rewrite).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_history"
down_revision = "0003_network_catalog"
branch_labels = depends_on = None


def convert_observations(partitioned):
    op.execute(
        "LOCK TABLE observations, latest_observations, observation_revisions IN ACCESS EXCLUSIVE MODE"
    )
    for table in ("latest_observations", "observation_revisions"):
        for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
            if fk["referred_table"] == "observations":
                op.drop_constraint(fk["name"], table, type_="foreignkey")
    op.execute("ALTER TABLE observations RENAME TO observations_old")
    op.execute(
        "CREATE TABLE observations (LIKE observations_old INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
        + (" PARTITION BY RANGE (observed_at)" if partitioned else "")
    )
    if partitioned:
        op.execute("""DO $$ DECLARE d date; BEGIN
          FOR d IN SELECT generate_series(
            LEAST(COALESCE((SELECT min(observed_at)::date FROM observations_old), CURRENT_DATE), CURRENT_DATE)::date - interval '1 month',
            GREATEST(COALESCE((SELECT max(observed_at)::date FROM observations_old), CURRENT_DATE), CURRENT_DATE)::date + interval '3 months', interval '1 month')::date
          LOOP
            d := date_trunc('month', d)::date;
            EXECUTE format('CREATE TABLE observations_%s PARTITION OF observations FOR VALUES FROM (%L) TO (%L)',
              to_char(d,'YYYYMM'), d::text || ' 00:00:00+00', (d + interval '1 month')::date::text || ' 00:00:00+00');
          END LOOP;
        END $$""")
        op.execute("CREATE TABLE observations_default PARTITION OF observations DEFAULT")
    op.execute("INSERT INTO observations SELECT * FROM observations_old")
    op.execute("DROP TABLE observations_old")
    op.create_primary_key(
        "observations_pkey", "observations", ["id", "observed_at"] if partitioned else ["id"]
    )
    op.create_unique_constraint(
        "uq_observation_semantic",
        "observations",
        ["source_id", "product", "observed_at", "period_start", "period_end", "period_basis"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_foreign_key(
        "observations_source_id_fkey", "observations", "station_sources", ["source_id"], ["id"]
    )
    op.create_index("ix_observations_observed_at", "observations", ["observed_at"])
    op.create_index("ix_observations_source_id", "observations", ["source_id"])
    if partitioned:
        op.create_index("ix_observations_source_time", "observations", ["source_id", "observed_at"])
    for table, stamp, name in [
        ("latest_observations", "observed_at", "fk_latest_observation"),
        ("observation_revisions", "observation_at", "fk_revision_observation"),
    ]:
        op.create_foreign_key(
            name,
            table,
            "observations",
            ["observation_id", stamp] if partitioned else ["observation_id"],
            ["id", "observed_at"] if partitioned else ["id"],
        )


def upgrade():
    op.add_column("observation_revisions", sa.Column("observation_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE observation_revisions r SET observation_at=o.observed_at FROM observations o WHERE o.id=r.observation_id"
    )
    op.alter_column("observation_revisions", "observation_at", nullable=False)
    convert_observations(True)
    op.add_column(
        "provider_runtime",
        sa.Column("history_calls", sa.Integer(), nullable=False, server_default="0"),
    )
    for table in ("daily_summaries", "hourly_aggregates"):
        op.add_column(table, sa.Column("channel", sa.String(64), nullable=False, server_default=""))
    op.add_column(
        "daily_summaries", sa.Column("provenance", JSONB(), nullable=False, server_default="{}")
    )
    op.add_column(
        "daily_summaries",
        sa.Column("provisional", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "hourly_aggregates", sa.Column("stats", JSONB(), nullable=False, server_default="{}")
    )
    op.drop_constraint("uq_daily_summary", "daily_summaries")
    op.create_unique_constraint(
        "uq_daily_summary",
        "daily_summaries",
        ["source_id", "product", "period_start", "period_end", "period_basis", "method", "channel"],
    )
    op.drop_constraint("uq_hourly_aggregate", "hourly_aggregates")
    op.create_unique_constraint(
        "uq_hourly_aggregate",
        "hourly_aggregates",
        ["source_id", "metric", "channel", "period_start"],
    )
    op.create_index(
        "ix_hourly_source_metric_time", "hourly_aggregates", ["source_id", "metric", "period_start"]
    )
    op.create_table(
        "aggregate_dirty_days",
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("station_sources.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "daily_summary_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("summary_id", sa.Uuid(), sa.ForeignKey("daily_summaries.id"), nullable=False),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("previous", JSONB(), nullable=False),
    )
    op.create_index(
        "ix_daily_summary_revisions_summary_id", "daily_summary_revisions", ["summary_id"]
    )
    op.execute("""CREATE FUNCTION dirty_observation_days() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        INSERT INTO aggregate_dirty_days(source_id,day)
        SELECT NEW.source_id, d::date FROM generate_series(
          (NEW.observed_at AT TIME ZONE 'Europe/Madrid')::date - 1,
          (NEW.observed_at AT TIME ZONE 'Europe/Madrid')::date + 1, interval '1 day') d
        ON CONFLICT (source_id,day) DO UPDATE SET changed_at=clock_timestamp();
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER observations_dirty AFTER INSERT OR UPDATE ON observations FOR EACH ROW EXECUTE FUNCTION dirty_observation_days()"
    )
    op.execute("""INSERT INTO aggregate_dirty_days(source_id, day)
        SELECT DISTINCT source_id, (observed_at AT TIME ZONE 'Europe/Madrid')::date FROM observations
        ON CONFLICT DO NOTHING""")


def downgrade():
    op.execute("DROP TRIGGER observations_dirty ON observations")
    op.execute("DROP FUNCTION dirty_observation_days()")
    op.drop_table("aggregate_dirty_days")
    op.drop_table("daily_summary_revisions")
    op.drop_index("ix_hourly_source_metric_time", "hourly_aggregates")
    # Older models cannot represent multiple channels: refuse lossy rollback.
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM daily_summaries WHERE channel <> '')
        OR EXISTS(SELECT 1 FROM hourly_aggregates WHERE channel <> '') THEN
        RAISE EXCEPTION 'History aggregates exist; downgrade would lose semantics'; END IF; END $$""")
    op.drop_constraint("uq_daily_summary", "daily_summaries")
    op.create_unique_constraint(
        "uq_daily_summary",
        "daily_summaries",
        ["source_id", "product", "period_start", "period_end", "period_basis", "method"],
    )
    op.drop_constraint("uq_hourly_aggregate", "hourly_aggregates")
    op.create_unique_constraint(
        "uq_hourly_aggregate", "hourly_aggregates", ["source_id", "metric", "period_start"]
    )
    for table, column in [
        ("daily_summaries", "provenance"),
        ("daily_summaries", "provisional"),
        ("hourly_aggregates", "stats"),
        ("daily_summaries", "channel"),
        ("hourly_aggregates", "channel"),
        ("provider_runtime", "history_calls"),
    ]:
        op.drop_column(table, column)
    convert_observations(False)
    op.drop_column("observation_revisions", "observation_at")
