from __future__ import annotations
"""server/app/migrations/versions/0001_initial.py
~~~~~~~~~~~~~~~~~~~~~~~~
Schéma initial.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial_full"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_keys_key", "api_keys", ["key"], unique=True)

    op.create_table(
        "client_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), unique=True, nullable=False),
        sa.Column("notification_email", sa.String(255), nullable=True),
        sa.Column("slack_webhook_url", sa.String(500), nullable=True),
        sa.Column("heartbeat_threshold_minutes", sa.Integer(), server_default="5", nullable=False),
        sa.Column("consecutive_failures_threshold", sa.Integer(), server_default="2", nullable=False),
        sa.Column("alert_grouping_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("alert_grouping_window_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "machines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("os_type", sa.String(50), nullable=True),
        sa.Column("os_version", sa.String(100), nullable=True),
        sa.Column("last_seen", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("registered_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_machines_hostname", "machines", ["hostname"], unique=False)

    op.create_table(
        "metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("machine_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("baseline_value", sa.Text(), nullable=True),
        sa.Column("is_alerting_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_metrics_machine_name", "metrics", ["machine_id", "name"], unique=True)

    op.create_table(
        "thresholds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("condition", sa.String(32), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("value_str", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(16), server_default="warning", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("consecutive_breaches", sa.Integer(), server_default="1", nullable=False),
        sa.Column("cooldown_sec", sa.Integer(), server_default="0", nullable=False),
        sa.Column("min_duration_sec", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["metric_id"], ["metrics.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "ingest_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("machine_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingest_id", sa.String(64), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_unique_constraint("uq_ingest_events_client_ingest", "ingest_events", ["client_id", "ingest_id"])

    op.create_table(
        "samples",
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("num_value", sa.Float(), nullable=True),
        sa.Column("bool_value", sa.Boolean(), nullable=True),
        sa.Column("str_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("metric_id", "ts", "seq", name="pk_samples"),
        sa.ForeignKeyConstraint(["metric_id"], ["metrics.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_samples_num_recent", "samples", ["metric_id", "ts"], unique=False, postgresql_where=sa.text("num_value IS NOT NULL"))
    op.create_index("ix_samples_bool_recent", "samples", ["metric_id", "ts"], unique=False, postgresql_where=sa.text("bool_value IS NOT NULL"))
    op.create_index("ix_samples_str_recent", "samples", ["metric_id", "ts"], unique=False, postgresql_where=sa.text("str_value IS NOT NULL"))

    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("threshold_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("machine_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="FIRING", nullable=False),
        sa.Column("current_value", sa.Text(), server_default="", nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(16), server_default="warning", nullable=False),
        sa.Column("triggered_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), server_default="OPEN", nullable=False),
        sa.Column("severity", sa.String(16), server_default="warning", nullable=False),
        sa.Column("machine_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alert_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_index(
        "ux_incidents_open_unique",
        "incidents",
        ["client_id", "machine_id", "title"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'")
    )

    op.create_table(
        "notification_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_table(
        "http_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("method", sa.String(10), server_default="GET", nullable=False),
        sa.Column("expected_status_code", sa.Integer(), server_default="200", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="30", nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("last_check_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_response_time_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_unique_constraint(
        "uq_http_targets_client_url",
        "http_targets",
        ["client_id", "url"],
    )

def downgrade() -> None:
    op.drop_index("ux_incidents_open_unique", table_name="incidents")
    op.drop_constraint("uq_http_targets_client_url", "http_targets", type_="unique")
    op.drop_table("http_targets")
    op.drop_table("notification_log")
    op.drop_table("incidents")
    op.drop_table("alerts")
    op.drop_index("ix_samples_str_recent", table_name="samples")
    op.drop_index("ix_samples_bool_recent", table_name="samples")
    op.drop_index("ix_samples_num_recent", table_name="samples")
    op.drop_table("samples")
    op.drop_table("ingest_events")
    op.drop_table("thresholds")
    op.drop_index("ix_metrics_machine_name", table_name="metrics")
    op.drop_table("metrics")
    op.drop_index("ix_machines_hostname", table_name="machines")
    op.drop_table("machines")
    op.drop_table("client_settings")
    op.drop_index("ix_api_keys_key", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("clients")
