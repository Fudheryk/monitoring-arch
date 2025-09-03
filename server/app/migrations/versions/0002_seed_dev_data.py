from __future__ import annotations

from alembic import op
from sqlalchemy import text
import uuid
import os

revision = "0002_seed_dev_data"
down_revision = "0001_initial_full"
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    
    # 1) Client "Dev" (EXISTANT - conservé tel quel)
    SEED_CLIENT_NAME = os.getenv("SEED_CLIENT_NAME", "Dev")
    SEED_API_KEY = os.getenv("SEED_API_KEY", "dev-apikey-123")
    
    client_uuid = str(uuid.uuid4())
    conn.execute(
        text("""
        INSERT INTO clients (id, name)
        SELECT CAST(:id AS UUID), CAST(:name AS VARCHAR(255))
        WHERE NOT EXISTS (SELECT 1 FROM clients WHERE name = :name)
        """),
        {"id": client_uuid, "name": SEED_CLIENT_NAME}
    )
    
    # Récupère l'ID (EXISTANT)
    client_id = conn.execute(
        text("SELECT id FROM clients WHERE name = :name LIMIT 1"),
        {"name": SEED_CLIENT_NAME}
    ).scalar()

    # 2) API Key (EXISTANT - seul le nom de colonne 'actif' corrigé en 'is_active')
    conn.execute(
        text("""
        INSERT INTO api_keys (id, client_id, key, name, is_active)
        SELECT 
            CAST(:id AS UUID),
            CAST(:client_id AS UUID),
            CAST(:key AS VARCHAR(255)),
            'dev',
            TRUE
        WHERE NOT EXISTS (SELECT 1 FROM api_keys WHERE key = :key)
        """),
        {
            "id": str(uuid.uuid4()),
            "client_id": client_id,
            "key": SEED_API_KEY
        }
    )

    # 3) Cible HTTP (EXISTANT - conservée)
    conn.execute(
        text("""
        INSERT INTO http_targets (
            id, client_id, name, url, method,
            expected_status_code, timeout_seconds,
            check_interval_seconds, is_active
        )
        SELECT
            CAST(:id AS UUID),
            CAST(:client_id AS UUID),
            CAST(:name AS VARCHAR(255)),
            CAST(:url AS VARCHAR(1000)),
            'GET',
            200,
            30,
            300,
            TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM http_targets
            WHERE client_id = :client_id AND url = :url
        )
        """),
        {
            "id": str(uuid.uuid4()),
            "client_id": client_id,
            "name": "Example Target",
            "url": "https://example.com"
        }
    )

    # NOUVEAU : Ajout d'un second client avec ses données
    acme_client_id = str(uuid.uuid4())
    conn.execute(
        text("""
        INSERT INTO clients (id, name, email)
        SELECT CAST(:id AS UUID), 'Acme Corp', 'it@acme.com'
        WHERE NOT EXISTS (SELECT 1 FROM clients WHERE name = 'Acme Corp')
        """),
        {"id": acme_client_id}
    )
    
    # NOUVEAU : API Key pour Acme
    conn.execute(
        text("""
        INSERT INTO api_keys (id, client_id, key, name, is_active)
        SELECT 
            CAST(:id AS UUID),
            (SELECT id FROM clients WHERE name = 'Acme Corp' LIMIT 1),
            'acme-prod-key-456',
            'prod',
            TRUE
        WHERE NOT EXISTS (SELECT 1 FROM api_keys WHERE key = 'acme-prod-key-456')
        """),
        {"id": str(uuid.uuid4())}
    )

    # NOUVEAU : Cible HTTP supplémentaire pour Acme
    conn.execute(
        text("""
        INSERT INTO http_targets (
            id, client_id, name, url, method,
            expected_status_code, timeout_seconds,
            check_interval_seconds, is_active
        )
        SELECT
            CAST(:id AS UUID),
            (SELECT id FROM clients WHERE name = 'Acme Corp' LIMIT 1),
            'Acme API Status',
            'https://api.acme.com/health',
            'GET',
            200,
            10,
            60,
            TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM http_targets 
            WHERE url = 'https://api.acme.com/health'
        )
        """),
        {"id": str(uuid.uuid4())}
    )
    
    # NOUVEAU : Création d'une machine et métriques de test pour le client Dev
    # Cette section doit être AJOUTÉE avant la création des seuils
    machine_id = str(uuid.uuid4())
    conn.execute(
        text("""
        INSERT INTO machines (id, client_id, hostname, os_type)
        SELECT 
            CAST(:machine_id AS UUID),
            CAST(:client_id AS UUID),
            'test-server',
            'linux'
        WHERE NOT EXISTS (
            SELECT 1 FROM machines 
            WHERE client_id = :client_id AND hostname = 'test-server'
        )
        """),
        {
            "machine_id": machine_id,
            "client_id": client_id
        }
    )

    # NOUVEAU : Création des métriques pour cette machine
    metrics_to_create = [
        ('cpu_load', 'numeric', 'ratio'),
        ('memory_usage', 'numeric', 'percent'),
        ('disk_usage', 'numeric', 'percent')
    ]

    for metric_name, metric_type, metric_unit in metrics_to_create:
        conn.execute(
            text("""
            INSERT INTO metrics (id, machine_id, name, type, unit, is_alerting_enabled)
            SELECT 
                CAST(:metric_id AS UUID),
                (SELECT id FROM machines WHERE hostname = 'test-server' AND client_id = :client_id LIMIT 1),
                CAST(:name AS VARCHAR(100)),
                CAST(:type AS VARCHAR(16)),
                CAST(:unit AS VARCHAR(20)),
                TRUE
            WHERE NOT EXISTS (
                SELECT 1 FROM metrics 
                WHERE name = :name AND machine_id IN (
                    SELECT id FROM machines WHERE client_id = :client_id
                )
            )
            """),
            {
                "metric_id": str(uuid.uuid4()),
                "client_id": client_id,
                "name": metric_name,
                "type": metric_type,
                "unit": metric_unit
            }
        )

    # NOUVEAU : Seuil d'alerte CPU pour le client Dev - VERSION CORRIGÉE
    conn.execute(
        text("""
        INSERT INTO thresholds (
            id, metric_id, name, condition, value_num, severity,
            consecutive_breaches, is_active, created_at, updated_at
        )
        SELECT
            CAST(:threshold_id AS UUID),
            (SELECT id FROM metrics WHERE name = 'cpu_load' AND machine_id IN 
                (SELECT id FROM machines WHERE client_id = :client_id AND hostname = 'test-server') LIMIT 1),
            'High CPU Load',
            'gt',
            1.0,
            'warning',
            1,
            TRUE,
            NOW(),
            NOW()
        WHERE EXISTS (
            SELECT 1 FROM metrics WHERE name = 'cpu_load' AND machine_id IN 
            (SELECT id FROM machines WHERE client_id = :client_id AND hostname = 'test-server')
        )
        AND NOT EXISTS (
            SELECT 1 FROM thresholds WHERE name = 'High CPU Load'
        )
        """),
        {
            "threshold_id": str(uuid.uuid4()),
            "client_id": client_id  # ID du client Dev
        }
    )

    # NOUVEAU : Seuil d'alerte mémoire (exemple supplémentaire) - VERSION CORRIGÉE
    conn.execute(
        text("""
        INSERT INTO thresholds (
            id, metric_id, name, condition, value_num, severity,
            consecutive_breaches, is_active, created_at, updated_at
        )
        SELECT
            CAST(:threshold_id AS UUID),
            (SELECT id FROM metrics WHERE name = 'memory_usage' AND machine_id IN 
                (SELECT id FROM machines WHERE client_id = :client_id AND hostname = 'test-server') LIMIT 1),
            'High Memory Usage',
            'gt',
            0.8,
            'warning',
            1,
            TRUE,
            NOW(),
            NOW()
        WHERE EXISTS (
            SELECT 1 FROM metrics WHERE name = 'memory_usage' AND machine_id IN 
            (SELECT id FROM machines WHERE client_id = :client_id AND hostname = 'test-server')
        )
        AND NOT EXISTS (
            SELECT 1 FROM thresholds WHERE name = 'High Memory Usage'
        )
        """),
        {
            "threshold_id": str(uuid.uuid4()),
            "client_id": client_id
        }
    )

def downgrade():
    """Version compatible avec les données ajoutées"""
    conn = op.get_bind()
    
    # Suppression des NOUVELLES données en premier
    conn.execute(
        text("DELETE FROM http_targets WHERE url = 'https://api.acme.com/health'")
    )
    
    conn.execute(
        text("DELETE FROM api_keys WHERE key = 'acme-prod-key-456'")
    )
    conn.execute(
        text("DELETE FROM clients WHERE name = 'Acme Corp'")
    )
    
    # Conserve le comportement EXISTANT pour les données originales
    SEED_CLIENT_NAME = os.getenv("SEED_CLIENT_NAME", "Dev")
    SEED_API_KEY = os.getenv("SEED_API_KEY", "dev-apikey-123")
    
    conn.execute(
        text("DELETE FROM http_targets WHERE url = 'https://example.com'")
    )
    
    conn.execute(
        text("DELETE FROM api_keys WHERE key = :key"),
        {"key": SEED_API_KEY}
    )
    
    conn.execute(
        text("""
        DELETE FROM clients
        WHERE name = :name
        AND NOT EXISTS (SELECT 1 FROM api_keys WHERE client_id = clients.id)
        AND NOT EXISTS (SELECT 1 FROM http_targets WHERE client_id = clients.id)
        """),
        {"name": SEED_CLIENT_NAME}
    )

    # Suppression des seuils
    conn.execute(
        text("DELETE FROM thresholds WHERE name IN ('High CPU Load', 'High Memory Usage')")
    )
