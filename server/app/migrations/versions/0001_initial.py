from __future__ import annotations
"""
server/app/migrations/versions/0001_initial.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Schéma initial refactoré avec toutes les contraintes de clés étrangères

✔ Toutes les colonnes *_id ont leurs FK appropriées
✔ metric_instance_id dans incidents correctement liée
✔ machine_id dans alerts et incidents avec FK
✔ Contraintes cohérentes avec les types d'incidents
✔ Compatible PostgreSQL & SQLite
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_full"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Typed columns for PG/SQLite
    now_sql = sa.text("NOW()") if is_pg else sa.text("CURRENT_TIMESTAMP")

    if is_pg:
        from sqlalchemy.dialects import postgresql as pg
        UUIDType = pg.UUID(as_uuid=True)
        JSONType = pg.JSONB(astext_type=sa.Text())
        TSTZ = sa.TIMESTAMP(timezone=True)
    else:
        UUIDType = sa.String(36)
        JSONType = sa.JSON()
        TSTZ = sa.DateTime(timezone=True)

    # =========================================================================
    # CLIENTS
    # =========================================================================
    op.create_table(
        "clients",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql, nullable=False),
        sa.Column("updated_at", TSTZ, server_default=now_sql, nullable=False),
    )

    # =========================================================================
    # USERS
    # =========================================================================
    op.create_table(
        "users",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="admin_client"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", TSTZ, server_default=now_sql, nullable=False),
        sa.Column("updated_at", TSTZ, server_default=now_sql, nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # =========================================================================
    # MACHINES
    # =========================================================================
    op.create_table(
        "machines",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("os_type", sa.String(50), nullable=True),
        sa.Column("os_version", sa.String(100), nullable=True),
        sa.Column("last_seen", TSTZ, nullable=True),
        sa.Column("registered_at", TSTZ, server_default=now_sql, nullable=False),
        sa.Column("unregistered_at", TSTZ, server_default=now_sql, nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_machines_hostname", "machines", ["client_id", "hostname"])
    op.create_index("ix_machines_fingerprint", "machines", ["fingerprint"])
    op.create_index("ix_machines_status", "machines", ["status"])

    # =========================================================================
    # API KEYS
    # =========================================================================
    op.create_table(
        "api_keys",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, nullable=False),
        sa.Column("key", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("last_used_at", TSTZ, nullable=True),
        sa.Column("machine_id", UUIDType, nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_api_keys_key", "api_keys", ["key"], unique=True)
    op.create_index("ix_api_keys_machine_id", "api_keys", ["machine_id"])

    # =========================================================================
    # CLIENT SETTINGS
    # =========================================================================
    op.create_table(
        "client_settings",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, unique=True, nullable=False),
        sa.Column("notification_email", sa.String(255), nullable=True),
        sa.Column("slack_webhook_url", sa.String(500), nullable=True),
        sa.Column("slack_channel_name", sa.String(16), nullable=True),
        sa.Column("heartbeat_threshold_minutes", sa.Integer(), server_default="5"),
        sa.Column("consecutive_failures_threshold", sa.Integer(), server_default="2"),
        sa.Column("alert_grouping_enabled", sa.Boolean(), server_default=sa.text("TRUE")),
        sa.Column("alert_grouping_window_seconds", sa.Integer(), server_default="300"),
        sa.Column("reminder_notification_seconds", sa.Integer(), server_default="600"),
        sa.Column("notify_on_resolve", sa.Boolean(), server_default=sa.text("TRUE")),
        sa.Column("grace_period_seconds", sa.Integer(), server_default="120"),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )

    # =========================================================================
    # ENUM metric_type
    # =========================================================================
    if is_pg:
        op.execute("""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'metric_type') THEN
                CREATE TYPE metric_type AS ENUM ('numeric', 'boolean', 'string');
              END IF;
            END$$;
        """)
        metric_type_enum = sa.dialects.postgresql.ENUM(
            "numeric", "boolean", "string",
            name="metric_type",
            create_type=False,
        )
    else:
        metric_type_enum = sa.Enum("numeric", "boolean", "string", name="metric_type", native_enum=False)

    # =========================================================================
    # METRIC DEFINITIONS (catalogue global)
    # =========================================================================
    op.create_table(
        "metric_definitions",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", metric_type_enum, nullable=False),
        sa.Column("group_name", sa.Text(), nullable=False, server_default="misc"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("vendor", sa.String(100), nullable=False, server_default="builtin"),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_suggested_critical", sa.Boolean(), server_default=sa.text("FALSE")),
        sa.Column("default_condition", sa.String(32), nullable=True),
        sa.Column("is_dynamic_family", sa.Boolean(), server_default=sa.text("FALSE")),
        sa.Column("dynamic_dimension", sa.String(64), nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        sa.CheckConstraint(
            "(is_dynamic_family = FALSE AND dynamic_dimension IS NULL) OR "
            "(is_dynamic_family = TRUE AND dynamic_dimension IS NOT NULL)",
            name="ck_metric_def_dynamic_consistency"
        ),
        *([] if is_pg else [sa.UniqueConstraint("name", "vendor", name="uq_metric_definitions_name_vendor")]),
    )

    if is_pg:
        op.create_unique_constraint(
            "uq_metric_definitions_name_vendor",
            "metric_definitions",
            ["name", "vendor"],
        )

    # =========================================================================
    # METRIC INSTANCES (réelles, par machine)
    # =========================================================================
    op.create_table(
        "metric_instances",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("machine_id", UUIDType, nullable=False),
        sa.Column("definition_id", UUIDType, nullable=True),
        sa.Column("name_effective", sa.Text(), nullable=False),
        sa.Column("dimension_value", sa.String(), nullable=False, server_default=""),
        sa.Column("is_alerting_enabled", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("needs_threshold", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("baseline_value", sa.Text(), nullable=True),
        sa.Column("last_value", sa.Text(), nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["definition_id"], ["metric_definitions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("machine_id", "definition_id", "dimension_value", name="uq_metric_instance"),
    )

    op.create_index(
        "ix_metric_instances_machine_name_effective",
        "metric_instances",
        ["machine_id", "name_effective"],
        unique=True,
    )

    # =========================================================================
    # THRESHOLD TEMPLATES (modèle théorique par definition_id)
    # =========================================================================
    op.create_table(
        "threshold_templates",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("definition_id", UUIDType, nullable=False),
        sa.Column("name", sa.String(100), nullable=False, server_default="default"),
        sa.Column("condition", sa.String(32), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("value_str", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(16), server_default="warning", nullable=False),
        sa.Column("consecutive_breaches", sa.Integer(), server_default="1"),
        sa.Column("cooldown_sec", sa.Integer(), server_default="0"),
        sa.Column("min_duration_sec", sa.Integer(), server_default="0"),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        sa.ForeignKeyConstraint(["definition_id"], ["metric_definitions.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            """
              (value_num IS NOT NULL AND value_bool IS NULL AND value_str IS NULL)
           OR (value_num IS NULL AND value_bool IS NOT NULL AND value_str IS NULL)
           OR (value_num IS NULL AND value_bool IS NULL AND value_str IS NOT NULL)
            """,
            name="ck_threshold_templates_single_value",
        ),
        *([] if is_pg else [sa.UniqueConstraint("definition_id", "name", name="uq_threshold_templates_definition_id_name")]),
    )

    if is_pg:
        op.create_unique_constraint(
            "uq_threshold_templates_definition_id_name",
            "threshold_templates",
            ["definition_id", "name"],
        )

    # =========================================================================
    # THRESHOLDS_NEW (seuils effectifs appliqués à une metric_instance)
    # =========================================================================
    op.create_table(
        "thresholds_new",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("metric_instance_id", UUIDType, nullable=False),
        sa.Column("template_id", UUIDType, nullable=True),
        sa.Column("name", sa.String(100), nullable=False, server_default="default"),
        sa.Column("condition", sa.String(32), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_bool", sa.Boolean(), nullable=True),
        sa.Column("value_str", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(16), server_default="warning", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE")),
        sa.Column("consecutive_breaches", sa.Integer(), server_default="1"),
        sa.Column("cooldown_sec", sa.Integer(), server_default="0"),
        sa.Column("min_duration_sec", sa.Integer(), server_default="0"),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        sa.ForeignKeyConstraint(["metric_instance_id"], ["metric_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["threshold_templates.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            """
              (value_num IS NOT NULL AND value_bool IS NULL AND value_str IS NULL)
           OR (value_num IS NULL AND value_bool IS NOT NULL AND value_str IS NULL)
           OR (value_num IS NULL AND value_bool IS NULL AND value_str IS NOT NULL)
            """,
            name="ck_thresholds_single_value",
        ),
        *([] if is_pg else [sa.UniqueConstraint("metric_instance_id", "name", name="uq_thresholds_metric_instance_id_name")]),
    )

    if is_pg:
        op.create_unique_constraint(
            "uq_thresholds_metric_instance_id_name",
            "thresholds_new",
            ["metric_instance_id", "name"],
        )

    # =========================================================================
    # INGEST EVENTS
    # =========================================================================
    op.create_table(
        "ingest_events",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, nullable=False),
        sa.Column("machine_id", UUIDType, nullable=False),
        sa.Column("ingest_id", sa.String(64), nullable=False),
        sa.Column("sent_at", TSTZ, nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        # ✅ FK ajoutées
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        *([] if is_pg else [sa.UniqueConstraint("client_id", "ingest_id", name="uq_ingest_events_client_ingest")]),
    )

    if is_pg:
        op.create_unique_constraint(
            "uq_ingest_events_client_ingest",
            "ingest_events",
            ["client_id", "ingest_id"],
        )

    # =========================================================================
    # SAMPLES
    # =========================================================================
    op.create_table(
        "samples",
        sa.Column("metric_instance_id", UUIDType, nullable=False),
        sa.Column("ts", TSTZ, server_default=now_sql, nullable=False),
        sa.Column("seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("num_value", sa.Float(), nullable=True),
        sa.Column("bool_value", sa.Boolean(), nullable=True),
        sa.Column("str_value", sa.Text(), nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.PrimaryKeyConstraint("metric_instance_id", "ts", "seq", name="pk_samples"),
        sa.ForeignKeyConstraint(["metric_instance_id"], ["metric_instances.id"], ondelete="CASCADE"),
    )

    # Index partiels
    if is_pg:
        op.create_index(
            "ix_samples_num_recent",
            "samples",
            ["metric_instance_id", "ts"],
            postgresql_where=sa.text("num_value IS NOT NULL"),
        )
        op.create_index(
            "ix_samples_bool_recent",
            "samples",
            ["metric_instance_id", "ts"],
            postgresql_where=sa.text("bool_value IS NOT NULL"),
        )
        op.create_index(
            "ix_samples_str_recent",
            "samples",
            ["metric_instance_id", "ts"],
            postgresql_where=sa.text("str_value IS NOT NULL"),
        )
    else:
        op.create_index(
            "ix_samples_num_recent",
            "samples",
            ["metric_instance_id", "ts"],
            sqlite_where=sa.text("num_value IS NOT NULL"),
        )
        op.create_index(
            "ix_samples_bool_recent",
            "samples",
            ["metric_instance_id", "ts"],
            sqlite_where=sa.text("bool_value IS NOT NULL"),
        )
        op.create_index(
            "ix_samples_str_recent",
            "samples",
            ["metric_instance_id", "ts"],
            sqlite_where=sa.text("str_value IS NOT NULL"),
        )

    # =========================================================================
    # HTTP TARGETS
    # =========================================================================
    accepted_status_column_type = JSONType

    op.create_table(
        "http_targets",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("method", sa.String(10), server_default="GET"),
        sa.Column("accepted_status_codes", accepted_status_column_type, nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), server_default="30"),
        sa.Column("check_interval_seconds", sa.Integer(), server_default="300"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE")),
        sa.Column("last_check_at", TSTZ, nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_state_change_at", TSTZ, nullable=True),
        sa.Column("last_response_time_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_message", sa.String(500), nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        *([] if is_pg else [sa.UniqueConstraint("client_id", "url", name="uq_http_targets_client_url")]),
    )

    if is_pg:
        op.create_unique_constraint(
            "uq_http_targets_client_url",
            "http_targets",
            ["client_id", "url"],
        )

    # =========================================================================
    # TABLE DE SÉQUENCE POUR incident_number (cross-DB)
    # =========================================================================
    op.create_table(
        "client_incident_counter",
        sa.Column("client_id", UUIDType, primary_key=True),
        sa.Column("next_incident_number", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )

    # =========================================================================
    # ENUM incident_type
    # =========================================================================
    if is_pg:
        op.execute("""
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'incident_type') THEN
                CREATE TYPE incident_type AS ENUM ('NO_DATA_MACHINE', 'NO_DATA_METRIC', 'BREACH', 'HTTP_FAILURE');
              END IF;
            END$$;
        """)
        incident_type_enum = sa.dialects.postgresql.ENUM(
            "NO_DATA_MACHINE", "NO_DATA_METRIC", "BREACH", "HTTP_FAILURE",
            name="incident_type",
            create_type=False,
        )
    else:
        incident_type_enum = sa.String(32)

    # =========================================================================
    # CHECK cohérence incident_type <-> colonnes de contexte
    # - NO_DATA_MACHINE  : machine_id requis, pas de metric_instance_id / http_target_id
    # - NO_DATA_METRIC   : metric_instance_id requis + machine_id requis, pas de http_target_id
    # - BREACH           : metric_instance_id requis + machine_id requis, pas de http_target_id
    # - HTTP_FAILURE     : http_target_id requis, pas de machine_id / metric_instance_id
    #
    # NOTE PG: cast explicite sur l'ENUM pour éviter les soucis de types
    # =========================================================================
    if is_pg:
        ck_incident_type_consistency_sql = """
            (incident_type = 'NO_DATA_MACHINE'::incident_type AND machine_id IS NOT NULL AND metric_instance_id IS NULL AND http_target_id IS NULL)
            OR (incident_type = 'NO_DATA_METRIC'::incident_type AND metric_instance_id IS NOT NULL AND machine_id IS NOT NULL AND http_target_id IS NULL)
            OR (incident_type = 'BREACH'::incident_type AND metric_instance_id IS NOT NULL AND machine_id IS NOT NULL AND http_target_id IS NULL)
            OR (incident_type = 'HTTP_FAILURE'::incident_type AND http_target_id IS NOT NULL AND machine_id IS NULL AND metric_instance_id IS NULL)
        """
    else:
        ck_incident_type_consistency_sql = """
            (incident_type = 'NO_DATA_MACHINE' AND machine_id IS NOT NULL AND metric_instance_id IS NULL AND http_target_id IS NULL)
            OR (incident_type = 'NO_DATA_METRIC' AND metric_instance_id IS NOT NULL AND machine_id IS NOT NULL AND http_target_id IS NULL)
            OR (incident_type = 'BREACH' AND metric_instance_id IS NOT NULL AND machine_id IS NOT NULL AND http_target_id IS NULL)
            OR (incident_type = 'HTTP_FAILURE' AND http_target_id IS NOT NULL AND machine_id IS NULL AND metric_instance_id IS NULL)
        """

    # =========================================================================
    # INCIDENTS
    # =========================================================================
    incident_constraints = [
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        # ✅ FK machine_id avec nom explicite
        sa.ForeignKeyConstraint(
            ["machine_id"],
            ["machines.id"],
            ondelete="SET NULL",
            name="fk_incidents_machine_id",
        ),
        # ✅ FK http_target_id
        sa.ForeignKeyConstraint(
            ["http_target_id"],
            ["http_targets.id"],
            ondelete="SET NULL",
            name="fk_incidents_http_target_id",
        ),
        # ✅ FK metric_instance_id
        sa.ForeignKeyConstraint(
            ["metric_instance_id"], 
            ["metric_instances.id"], 
            ondelete="SET NULL",
            name="fk_incidents_metric_instance_id"
        ),
        sa.CheckConstraint(
            "incident_number IS NULL OR incident_number > 0",
            name="ck_incidents_incident_number_pos",
        ),
        # ✅ Contrainte de cohérence des types d'incidents (PG/SQLite)
        sa.CheckConstraint(
            ck_incident_type_consistency_sql,
            name="ck_incidents_type_consistency",
        ),
    ]
    if not is_pg:
        incident_constraints.append(
            sa.CheckConstraint(
                "incident_type IN ('NO_DATA_MACHINE','NO_DATA_METRIC','BREACH','HTTP_FAILURE')",
                name="ck_incidents_type_values",
            )
        )

    op.create_table(
        "incidents",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, nullable=False),
        sa.Column("incident_number", sa.Integer(), nullable=True),
        sa.Column("incident_type", incident_type_enum, nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), server_default="OPEN"),
        sa.Column("severity", sa.String(16), server_default="warning"),
        sa.Column("machine_id", UUIDType, nullable=True),
        sa.Column("metric_instance_id", UUIDType, nullable=True),
        sa.Column("http_target_id", UUIDType, nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("resolved_at", TSTZ, nullable=True),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        *incident_constraints,
        *([] if is_pg else [sa.UniqueConstraint("client_id", "incident_number", name="uq_incidents_client_incident_number")]),
    )

    # INDEX UNIQUES "OPEN" (PG + SQLite)
    if is_pg:
        op.create_unique_constraint(
            "uq_incidents_client_incident_number",
            "incidents",
            ["client_id", "incident_number"],
        )

        op.create_index(
            "ux_incidents_machine_open_unique",
            "incidents",
            ["client_id", "incident_type", "machine_id", "dedup_key"],
            unique=True,
            postgresql_where=sa.text("status = 'OPEN' AND machine_id IS NOT NULL AND metric_instance_id IS NULL AND http_target_id IS NULL"),
        )
        op.create_index(
            "ux_incidents_metric_open_unique",
            "incidents",
            ["client_id", "incident_type", "metric_instance_id", "dedup_key"],
            unique=True,
            postgresql_where=sa.text("status = 'OPEN' AND metric_instance_id IS NOT NULL"),
        )
        op.create_index(
            "ux_incidents_http_open_unique",
            "incidents",
            ["client_id", "incident_type", "http_target_id", "dedup_key"],
            unique=True,
            postgresql_where=sa.text("status = 'OPEN' AND http_target_id IS NOT NULL"),
        )
        op.create_index(
            "ux_incidents_generic_open_unique",
            "incidents",
            ["client_id", "incident_type", "dedup_key"],
            unique=True,
            postgresql_where=sa.text("status = 'OPEN' AND machine_id IS NULL AND http_target_id IS NULL AND metric_instance_id IS NULL"),
        )
    else:
        op.create_index(
            "ux_incidents_machine_open_unique",
            "incidents",
            ["client_id", "incident_type", "machine_id", "dedup_key"],
            unique=True,
            sqlite_where=sa.text("status = 'OPEN' AND machine_id IS NOT NULL AND metric_instance_id IS NULL AND http_target_id IS NULL"),
        )
        op.create_index(
            "ux_incidents_http_open_unique",
            "incidents",
            ["client_id", "incident_type", "http_target_id", "dedup_key"],
            unique=True,
            sqlite_where=sa.text("status = 'OPEN' AND http_target_id IS NOT NULL"),
        )
        op.create_index(
            "ux_incidents_metric_open_unique",
            "incidents",
            ["client_id", "incident_type", "metric_instance_id", "dedup_key"],
            unique=True,
            sqlite_where=sa.text("status = 'OPEN' AND metric_instance_id IS NOT NULL"),
        )
        op.create_index(
            "ux_incidents_generic_open_unique",
            "incidents",
            ["client_id", "incident_type", "dedup_key"],
            unique=True,
            sqlite_where=sa.text("status = 'OPEN' AND machine_id IS NULL AND http_target_id IS NULL AND metric_instance_id IS NULL"),
        )

    # =========================================================================
    # INCIDENT NUMBER auto-increment per client
    # =========================================================================
    if is_pg:
        op.execute("""
        CREATE OR REPLACE FUNCTION set_incident_number_per_client()
        RETURNS trigger AS $$
        DECLARE
            current_num INTEGER;
        BEGIN
            IF NEW.incident_number IS NULL OR NEW.incident_number = 0 THEN
                INSERT INTO client_incident_counter (client_id, next_incident_number)
                VALUES (NEW.client_id, 1)
                ON CONFLICT (client_id) DO NOTHING;

                SELECT next_incident_number
                INTO current_num
                FROM client_incident_counter
                WHERE client_id = NEW.client_id
                FOR UPDATE;

                NEW.incident_number := current_num;

                UPDATE client_incident_counter
                SET next_incident_number = next_incident_number + 1
                WHERE client_id = NEW.client_id;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)

        op.execute("""
        DROP TRIGGER IF EXISTS trg_set_incident_number_per_client ON incidents;
        CREATE TRIGGER trg_set_incident_number_per_client
        BEFORE INSERT ON incidents
        FOR EACH ROW
        EXECUTE FUNCTION set_incident_number_per_client();
        """)
    else:
        op.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_set_incident_number_sqlite
        AFTER INSERT ON incidents
        FOR EACH ROW
        WHEN NEW.incident_number IS NULL OR NEW.incident_number = 0
        BEGIN
            INSERT OR IGNORE INTO client_incident_counter (client_id, next_incident_number)
            VALUES (NEW.client_id, 1);

            UPDATE incidents
            SET incident_number = (
                SELECT next_incident_number
                FROM client_incident_counter
                WHERE client_id = NEW.client_id
            )
            WHERE rowid = NEW.rowid;

            UPDATE client_incident_counter
            SET next_incident_number = next_incident_number + 1
            WHERE client_id = NEW.client_id;
        END;
        """)

    # =========================================================================
    # ALERTS
    # =========================================================================
    op.create_table(
        "alerts",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("threshold_id", UUIDType, nullable=False),
        sa.Column("machine_id", UUIDType, nullable=False),
        sa.Column("metric_instance_id", UUIDType, nullable=True),
        sa.Column("status", sa.String(16), server_default="FIRING"),
        sa.Column("current_value", sa.Text(), server_default=""),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(16), server_default="warning"),
        sa.Column("triggered_at", TSTZ, server_default=now_sql),
        sa.Column("resolved_at", TSTZ, nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        # ✅ FK ajoutées pour alerts
        sa.ForeignKeyConstraint(["threshold_id"], ["thresholds_new.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["metric_instance_id"], ["metric_instances.id"], ondelete="SET NULL"),
    )

    op.create_index(
        "ix_alerts_machine_status",
        "alerts",
        ["machine_id", "status"]
    )

    op.create_index(
        "ix_alerts_status_triggered",
        "alerts",
        ["status", "triggered_at"]
    )

    # =========================================================================
    # NOTIFICATION LOG
    # =========================================================================
    op.create_table(
        "notification_log",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, nullable=False),
        sa.Column("incident_id", UUIDType, nullable=True),
        sa.Column("alert_id", UUIDType, nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", TSTZ, nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        # ✅ FK ajoutées pour notification_log
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="SET NULL"),
    )

    # =========================================================================
    # OUTBOX EVENTS + ENUM
    # =========================================================================
    if is_pg:
        op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'outbox_status') THEN
                CREATE TYPE outbox_status AS ENUM (
                    'PENDING','DELIVERING','DELIVERED','FAILED'
                );
            END IF;
        END$$;
        """)
        status_col = sa.dialects.postgresql.ENUM(
            "PENDING", "DELIVERING", "DELIVERED", "FAILED",
            name="outbox_status",
            create_type=False
        )
    else:
        status_col = sa.Enum(
            "PENDING", "DELIVERING", "DELIVERED", "FAILED",
            name="outbox_status", native_enum=False, create_type=False
        )

    op.create_table(
        "outbox_events",
        sa.Column("id", UUIDType, primary_key=True),
        sa.Column("client_id", UUIDType, nullable=False),
        sa.Column("incident_id", UUIDType, nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", JSONType, nullable=False),
        sa.Column("status", status_col, nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TSTZ, nullable=True),
        sa.Column("delivery_receipt", JSONType, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", TSTZ, server_default=now_sql),
        sa.Column("updated_at", TSTZ, server_default=now_sql),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
    )

    op.create_index(
        "ix_outbox_status_due",
        "outbox_events",
        ["status", "next_attempt_at"]
    )


# =========================================================================
# DOWNGRADE
# =========================================================================

def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # =========================================================================
    # OUTBOX EVENTS
    # =========================================================================
    op.drop_index("ix_outbox_status_due", table_name="outbox_events")
    op.drop_table("outbox_events")

    if is_pg:
        r = bind.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'outbox_status'")).fetchone()
        if r:
            op.execute("DROP TYPE outbox_status")

    # =========================================================================
    # NOTIFICATION LOG
    # =========================================================================
    op.drop_table("notification_log")

    # =========================================================================
    # ALERTS
    # =========================================================================
    op.drop_index("ix_alerts_status_triggered", table_name="alerts")
    op.drop_index("ix_alerts_machine_status", table_name="alerts")
    op.drop_table("alerts")

    # =========================================================================
    # INCIDENTS
    # =========================================================================
    op.drop_index("ux_incidents_generic_open_unique", table_name="incidents")
    op.drop_index("ux_incidents_metric_open_unique", table_name="incidents")
    op.drop_index("ux_incidents_http_open_unique", table_name="incidents")
    op.drop_index("ux_incidents_machine_open_unique", table_name="incidents")

    if is_pg:
        op.drop_constraint("fk_incidents_metric_instance_id", "incidents", type_="foreignkey")
        op.drop_constraint("fk_incidents_http_target_id", "incidents", type_="foreignkey")
        op.drop_constraint("fk_incidents_machine_id", "incidents", type_="foreignkey")
        op.drop_constraint("uq_incidents_client_incident_number", "incidents", type_="unique")
        op.execute("DROP TRIGGER IF EXISTS trg_set_incident_number_per_client ON incidents;")
        op.execute("DROP FUNCTION IF EXISTS set_incident_number_per_client();")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_set_incident_number_sqlite;")

    op.drop_table("incidents")

    # =========================================================================
    # CLIENT INCIDENT COUNTER
    # =========================================================================
    op.drop_table("client_incident_counter")

    # =========================================================================
    # INCIDENT TYPE ENUM
    # =========================================================================
    if is_pg:
        r = bind.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'incident_type'")).fetchone()
        if r:
            op.execute("DROP TYPE incident_type")

    # =========================================================================
    # HTTP TARGETS
    # =========================================================================
    if is_pg:
        op.drop_constraint("uq_http_targets_client_url", "http_targets", type_="unique")
    op.drop_table("http_targets")

    # =========================================================================
    # SAMPLES
    # =========================================================================
    op.drop_index("ix_samples_str_recent", table_name="samples")
    op.drop_index("ix_samples_bool_recent", table_name="samples")
    op.drop_index("ix_samples_num_recent", table_name="samples")
    op.drop_table("samples")

    # =========================================================================
    # INGEST EVENTS
    # =========================================================================
    if is_pg:
        op.drop_constraint("uq_ingest_events_client_ingest", "ingest_events", type_="unique")
    op.drop_table("ingest_events")

    # =========================================================================
    # THRESHOLDS_NEW
    # =========================================================================
    if is_pg:
        op.drop_constraint("uq_thresholds_metric_instance_id_name", "thresholds_new", type_="unique")
    op.drop_table("thresholds_new")

    # =========================================================================
    # THRESHOLD TEMPLATES
    # =========================================================================
    if is_pg:
        op.drop_constraint("uq_threshold_templates_definition_id_name", "threshold_templates", type_="unique")
    op.drop_table("threshold_templates")

    # =========================================================================
    # METRIC INSTANCES
    # =========================================================================
    op.drop_index("ix_metric_instances_machine_name_effective", table_name="metric_instances")
    op.drop_table("metric_instances")

    # =========================================================================
    # METRIC DEFINITIONS
    # =========================================================================
    if is_pg:
        op.drop_constraint("uq_metric_definitions_name_vendor", "metric_definitions", type_="unique")
    op.drop_table("metric_definitions")

    # =========================================================================
    # METRIC TYPE ENUM
    # =========================================================================
    if is_pg:
        r = bind.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'metric_type'")).fetchone()
        if r:
            op.execute("DROP TYPE metric_type")

    # =========================================================================
    # CLIENT SETTINGS
    # =========================================================================
    op.drop_table("client_settings")

    # =========================================================================
    # API KEYS
    # =========================================================================
    op.drop_index("ix_api_keys_machine_id", table_name="api_keys")
    op.drop_index("ix_api_keys_key", table_name="api_keys")
    op.drop_table("api_keys")

    # =========================================================================
    # MACHINES
    # =========================================================================
    op.drop_index("ix_machines_status", table_name="machines")
    op.drop_index("ix_machines_fingerprint", table_name="machines")
    op.drop_index("ix_machines_hostname", table_name="machines")
    op.drop_table("machines")

    # =========================================================================
    # USERS
    # =========================================================================
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    # =========================================================================
    # CLIENTS
    # =========================================================================
    op.drop_table("clients")