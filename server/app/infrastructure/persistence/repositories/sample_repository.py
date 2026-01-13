from __future__ import annotations

from typing import Iterable, Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


class SampleRepository:
    """
    Repository responsable de l'écriture des samples uniquement.
    
    STRATÉGIE seq :
    - Version actuelle : ON CONFLICT DO NOTHING (accepte une seule valeur par (metric_instance, ts))
    - Alternative : Gérer seq auto-incrémenté (voir version commentée ci-dessous)
    
    La version ON CONFLICT est recommandée si :
    - Vous recevez un seul payload par timestamp (cas normal)
    - Vous voulez éviter les doublons en cas de rejeu
    
    La version avec seq auto-incrémenté est utile si :
    - Vous recevez vraiment plusieurs valeurs distinctes pour la même métrique au même timestamp
    - Vous voulez garder l'historique de tous les envois
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _coerce_value_fields(self, m: dict) -> dict:
        """
        Convertit un dict métrique normalisé en colonnes tri-typées.
        
        Entrée attendue :
            {
                "id": "<metric_uuid>",
                "type": "numeric" | "boolean" | "string",
                "value": <valeur brute>,
            }
        """
        vtype = (m.get("type") or "").lower()
        raw = m.get("value")

        # ---- numeric ----
        if vtype in ("numeric", "number", "float", "int"):
            try:
                return {
                    "value_type": "numeric",
                    "num_value": float(raw),
                    "bool_value": None,
                    "str_value": None,
                }
            except Exception:
                return {
                    "value_type": "string",
                    "num_value": None,
                    "bool_value": None,
                    "str_value": str(raw),
                }

        # ---- bool ----
        if vtype in ("bool", "boolean"):
            if isinstance(raw, str):
                b = raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
            else:
                b = bool(raw)

            return {
                "value_type": "boolean",
                "num_value": None,
                "bool_value": b,
                "str_value": None,
            }

        # ---- string (par défaut) ----
        return {
            "value_type": "string",
            "num_value": None,
            "bool_value": None,
            "str_value": "" if raw is None else str(raw),
        }

    # ─────────────────────────────────────────────────────────────────────────────
    # Version ACTUELLE : ON CONFLICT DO NOTHING (recommandée)
    # ─────────────────────────────────────────────────────────────────────────────
    
    def write_batch(
        self,
        *,
        machine_id: str,
        metrics_payload: Iterable[dict],
        sent_at: Optional[str],
    ) -> None:
        """
        Insère une ligne par métrique dans la table `samples`.
        
        STRATÉGIE ACTUELLE :
        - Toujours seq=0
        - ON CONFLICT DO NOTHING pour éviter les doublons
        - Une seule valeur par (metric_instance_id, ts)
        
        Avantages :
        - Simple et performant
        - Idempotent (rejeu safe)
        - Suffisant si l'agent envoie un seul payload par seconde
        
        Inconvénients :
        - Perd les valeurs multiples si plusieurs payloads arrivent à la même seconde
        """
        if not metrics_payload:
            return

        if sent_at:
            ts_expr = ":ts"
            ts_bind = {"ts": sent_at}
        else:
            ts_expr = "NOW()"
            ts_bind = {}

        # Détection du dialecte
        dialect = self.session.bind.dialect.name if self.session.bind else "default"

        if dialect == "postgresql":
            insert_sql = f"""
                INSERT INTO samples
                    (metric_instance_id, ts, seq, value_type, num_value, bool_value, str_value)
                VALUES
                    (:metric_instance_id, {ts_expr}, 0,
                     :value_type, :num_value, :bool_value, :str_value)
                ON CONFLICT (metric_instance_id, ts, seq) DO NOTHING
            """
        else:
            # Fallback sans ON CONFLICT (sqlite, etc.)
            insert_sql = f"""
                INSERT INTO samples
                    (metric_instance_id, ts, seq, value_type, num_value, bool_value, str_value)
                VALUES
                    (:metric_instance_id, {ts_expr}, 0,
                     :value_type, :num_value, :bool_value, :str_value)
            """

        for m in metrics_payload:
            metric_instance_id = m["id"]
            values = self._coerce_value_fields(m)

            self.session.execute(
                text(insert_sql),
                {
                    "metric_instance_id": metric_instance_id,
                    **values,
                    **ts_bind,
                },
            )

    # ─────────────────────────────────────────────────────────────────────────────
    # Version ALTERNATIVE : seq auto-incrémenté (pour valeurs multiples)
    # ─────────────────────────────────────────────────────────────────────────────
    
    def write_batch_with_seq(
        self,
        *,
        machine_id: str,
        metrics_payload: Iterable[dict],
        sent_at: Optional[str],
    ) -> None:
        """
        Version alternative avec seq auto-incrémenté.
        
        STRATÉGIE :
        - Récupère MAX(seq) pour chaque (metric_instance_id, ts)
        - Incrémente seq pour chaque nouvelle valeur
        - Permet plusieurs valeurs par seconde
        
        Avantages :
        - Conserve toutes les valeurs multiples
        - Plus précis si l'agent envoie vraiment plusieurs fois par seconde
        
        Inconvénients :
        - Plus complexe
        - Nécessite un SELECT avant chaque INSERT
        - Moins performant (mais acceptable pour des volumes modérés)
        
        À utiliser si vous avez besoin de tracer plusieurs mesures par seconde.
        """
        if not metrics_payload:
            return

        if sent_at:
            ts_expr = ":ts"
            ts_bind = {"ts": sent_at}
        else:
            ts_expr = "NOW()"
            ts_bind = {}

        for m in metrics_payload:
            metric_instance_id = m["id"]
            values = self._coerce_value_fields(m)

            # Récupération du dernier seq pour cette métrique + timestamp
            if sent_at:
                last_seq_row = self.session.execute(
                    text("""
                        SELECT MAX(seq) AS maxseq
                        FROM samples
                        WHERE metric_instance_id = :metric_instance_id
                          AND ts = :ts
                    """),
                    {
                        "metric_instance_id": metric_instance_id,
                        "ts": sent_at,
                    }
                ).first()
            else:
                # NOW() → récupération du ts réel d'abord
                last_ts = self.session.execute(text("SELECT NOW()")).scalar()

                last_seq_row = self.session.execute(
                    text("""
                        SELECT MAX(seq) AS maxseq
                        FROM samples
                        WHERE metric_instance_id = :metric_instance_id
                          AND ts = :ts
                    """),
                    {
                        "metric_instance_id": metric_instance_id,
                        "ts": last_ts,
                    }
                ).first()

            # Calcul du prochain seq
            seq = (last_seq_row.maxseq + 1) if last_seq_row and last_seq_row.maxseq is not None else 0

            # INSERT avec seq dynamique
            self.session.execute(
                text(f"""
                    INSERT INTO samples
                        (metric_instance_id, ts, seq, value_type, num_value, bool_value, str_value)
                    VALUES
                        (:metric_instance_id, {ts_expr}, :seq,
                         :value_type, :num_value, :bool_value, :str_value)
                """),
                {
                    "metric_instance_id": metric_instance_id,
                    "seq": seq,
                    **values,
                    **ts_bind,
                },
            )