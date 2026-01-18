from __future__ import annotations

"""
0003_fix_temperature_coretemp_dynamic_family
--------------------------------------------

Pourquoi ?
- La migration 0002 a seedé (ou a pu seeder) une métrique temperature sous une forme non dynamique :
    "temperature.coretemp.current"
- On veut désormais une famille dynamique par cœur CPU :
    "temperature.coretemp.<number>.current"
- Modifier 0002 ne mettra PAS à jour une base déjà migrée (prod).
  Donc on fait une migration dédiée 0003.

Stratégie :
1) Si l'ancienne définition existe (builtin) et que la nouvelle n'existe pas :
      - UPDATE de la ligne (on conserve le même id -> templates restent OK)
2) Si la nouvelle existe déjà et l'ancienne existe aussi :
      - On migre les éventuels threshold_templates vers la nouvelle définition
      - Puis on supprime l'ancienne définition
3) Si aucune des deux n'existe :
      - On INSERT la nouvelle définition

Remarque sur dynamic_dimension :
- On choisit "number" car le placeholder du pattern est "<number>" et
  l'ingestion extrait un index numérique. (Cohérent bout-en-bout.)
"""

from alembic import op
from sqlalchemy import text
import uuid

revision = "0003_fix_temp_coretemp"
down_revision = "0002_seed_builtin_metrics"
branch_labels = None
depends_on = None


OLD_NAME = "temperature.coretemp.current"
NEW_NAME = "temperature.coretemp.<number>.current"
VENDOR = "builtin"


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Vérifier existence ancienne et nouvelle définition
    old_id = conn.execute(
        text(
            """
            SELECT id
            FROM metric_definitions
            WHERE name = :name AND vendor = :vendor
            LIMIT 1
            """
        ),
        {"name": OLD_NAME, "vendor": VENDOR},
    ).scalar()

    new_id = conn.execute(
        text(
            """
            SELECT id
            FROM metric_definitions
            WHERE name = :name AND vendor = :vendor
            LIMIT 1
            """
        ),
        {"name": NEW_NAME, "vendor": VENDOR},
    ).scalar()

    # 2) Cas A : l'ancienne existe, la nouvelle n'existe pas -> UPDATE (préserve l'id)
    if old_id is not None and new_id is None:
        conn.execute(
            text(
                """
                UPDATE metric_definitions
                SET
                    name = :new_name,
                    is_dynamic_family = TRUE,
                    dynamic_dimension = :dyn_dim,
                    -- On garde les champs métier alignés (sans écraser d'éventuelles customisations majeures)
                    group_name = COALESCE(group_name, 'temperature'),
                    default_condition = COALESCE(default_condition, 'gt'),
                    is_suggested_critical = COALESCE(is_suggested_critical, TRUE)
                WHERE id = :id
                """
            ),
            {
                "id": old_id,
                "new_name": NEW_NAME,
                "dyn_dim": "number",
            },
        )
        return

    # 3) Cas B : les deux existent -> migrer les templates vers la nouvelle, puis supprimer l'ancienne
    if old_id is not None and new_id is not None:
        # Si threshold_templates référence metric_definitions via metric_definition_id
        # (si chez toi le nom diffère, adapte cette requête).
        conn.execute(
            text(
                """
                UPDATE threshold_templates
                SET metric_definition_id = :new_id
                WHERE metric_definition_id = :old_id
                """
            ),
            {"new_id": new_id, "old_id": old_id},
        )

        # Supprimer l'ancienne définition pour éviter les ambiguïtés
        conn.execute(
            text("DELETE FROM metric_definitions WHERE id = :old_id"),
            {"old_id": old_id},
        )

        # S'assurer que la nouvelle est bien marquée dynamique
        conn.execute(
            text(
                """
                UPDATE metric_definitions
                SET is_dynamic_family = TRUE,
                    dynamic_dimension = :dyn_dim
                WHERE id = :new_id
                """
            ),
            {"new_id": new_id, "dyn_dim": "number"},
        )
        return

    # 4) Cas C : aucune n'existe -> INSERT de la nouvelle
    if old_id is None and new_id is None:
        conn.execute(
            text(
                """
                INSERT INTO metric_definitions (
                    id,
                    name,
                    type,
                    group_name,
                    description,
                    vendor,
                    is_suggested_critical,
                    default_condition,
                    is_dynamic_family,
                    dynamic_dimension
                )
                VALUES (
                    CAST(:id AS UUID),
                    :name,
                    CAST(:type AS metric_type),
                    :group_name,
                    :description,
                    :vendor,
                    :is_suggested_critical,
                    :default_condition,
                    :is_dynamic_family,
                    :dynamic_dimension
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "name": NEW_NAME,
                "type": "numeric",
                "group_name": "temperature",
                "description": "Température actuelle d'un cœur du processeur.",
                "vendor": VENDOR,
                "is_suggested_critical": True,
                "default_condition": "gt",
                "is_dynamic_family": True,
                "dynamic_dimension": "number",
            },
        )
        return

    # 5) Cas D : la nouvelle existe, l'ancienne non -> juste garantir les flags
    if old_id is None and new_id is not None:
        conn.execute(
            text(
                """
                UPDATE metric_definitions
                SET is_dynamic_family = TRUE,
                    dynamic_dimension = :dyn_dim
                WHERE id = :new_id
                """
            ),
            {"new_id": new_id, "dyn_dim": "number"},
        )


def downgrade() -> None:
    """
    Downgrade "raisonnable" (sans casser les données runtime) :

    - Si NEW_NAME existe et OLD_NAME n'existe pas :
        on renomme NEW -> OLD et on désactive le flag dynamique.
      (Cela peut casser l'historique de dimensions en UI, mais reste le miroir logique.)

    - Si OLD_NAME existe déjà : on ne fait rien (idempotent).
    """
    conn = op.get_bind()

    old_id = conn.execute(
        text(
            """
            SELECT id
            FROM metric_definitions
            WHERE name = :name AND vendor = :vendor
            LIMIT 1
            """
        ),
        {"name": OLD_NAME, "vendor": VENDOR},
    ).scalar()

    new_id = conn.execute(
        text(
            """
            SELECT id
            FROM metric_definitions
            WHERE name = :name AND vendor = :vendor
            LIMIT 1
            """
        ),
        {"name": NEW_NAME, "vendor": VENDOR},
    ).scalar()

    if old_id is None and new_id is not None:
        conn.execute(
            text(
                """
                UPDATE metric_definitions
                SET
                    name = :old_name,
                    is_dynamic_family = FALSE,
                    dynamic_dimension = NULL
                WHERE id = :id
                """
            ),
            {"id": new_id, "old_name": OLD_NAME},
        )
