from __future__ import annotations
"""
server/app/workers/scheduler/beat_schedule.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Planification des tâches périodiques Celery (Beat).

Chaque entrée de `beat_schedule` définit :
    - le nom logique de la tâche
    - le chemin Celery de la tâche ("task")
    - l'intervalle d'exécution ("schedule", en secondes)

Les workers Celery consommeront ces tâches automatiquement.
"""

beat_schedule = {

    # ----------------------------------------------------------------------
    # 1) Évaluation des métriques / seuils
    # ----------------------------------------------------------------------
    # Cette tâche parcourt toutes les machines et vérifie
    # s'il existe une violation de seuils (warning / critical / etc.).
    #
    # NOTE :
    #   Tu avais aussi "evaluate-metrics-every-60s".
    #   On garde uniquement cette version toutes les 10s (tests + réactivité).
    # ----------------------------------------------------------------------
    "evaluate-all-every-10s": {
        "task": "tasks.evaluate",
        "schedule": 10.0,
    },

    # ----------------------------------------------------------------------
    # 2) Heartbeat machine (NO-DATA / STALE / DOWN)
    # ----------------------------------------------------------------------
    # Appelle tasks.heartbeat → machine_status_service.update_all_machine_statuses()
    # Permet de suivre l'activité des machines même si aucune métrique n'est envoyée.
    # ----------------------------------------------------------------------
    "check-heartbeats-every-120s": {
        "task": "tasks.heartbeat",
        "schedule": 120.0,
    },

    # ----------------------------------------------------------------------
    # 3) Monitoring HTTP (pour les "http_targets")
    # ----------------------------------------------------------------------
    # Tâche périodique de supervision HTTP :
    #   - vérifie code HTTP
    #   - ouvre alertes si statut != attendu
    #   - gère cooldown et grace period
    # ----------------------------------------------------------------------
    "check-http-targets-every-120s": {
        "task": "tasks.http",
        "schedule": 120.0,
    },

    # ----------------------------------------------------------------------
    # 4) Outbox delivery (système de notifications)
    # ----------------------------------------------------------------------
    # Ce mécanisme de "transaction outbox" expédie les notifications :
    #   - emails
    #   - slack
    #   - etc.
    # après validation transactionnelle.
    # ----------------------------------------------------------------------
    "deliver-outbox-every-30s": {
        "task": "outbox.deliver",
        "schedule": 30.0,
    },

    # ----------------------------------------------------------------------
    # 4bis) Rappels groupés d'incidents ouverts
    # ----------------------------------------------------------------------
    # Déclenche périodiquement le reminder groupé pour les clients
    # qui ont alert_grouping_enabled=true.
    "grouped-incident-reminders-every-60s": {
        "task": "tasks.grouped_reminders",
         "schedule": 60.0,
        # Important: éviter que ça parte sur la queue par défaut ("ingest")
        # si aucune route n'est définie côté celery_app.py
        "options": {"queue": "notify"},
    },

    # ----------------------------------------------------------------------
    # 4ter) Rappels non-groupés d'incidents ouverts
    # ----------------------------------------------------------------------
    # Déclenche périodiquement le reminder non-groupé pour les clients
    # qui ont alert_grouping_enabled=false.
    "incident-reminders-every-60s": {
        "task": "tasks.incident_reminders",
        "schedule": 60.0,
        "options": {"queue": "notify"},
    },

    # ----------------------------------------------------------------------
    # 5) Recalcule fréquent du statut des machines
    # ----------------------------------------------------------------------
    # Pour s'assurer que les statuts des machines sont toujours à jour,
    # même en l'absence de heartbeats ou de métriques.
    # ----------------------------------------------------------------------
    "machine-status-every-30s": {
        "task": "tasks.machine_status",
        "schedule": 30.0,
    },

    # ----------------------------------------------------------------------
    # 6) Vérification des métriques "no data" (staleness)
    # ----------------------------------------------------------------------
    # Nouveau pipeline unifié :
    #   - tasks.check_metrics_no_data → app.application.services.metric_freshness_service.check_metrics_no_data()
    #   - s'appuie sur MetricInstance.updated_at + ClientSettings.metric_staleness_seconds
    #   - ouvre/résout des incidents "Metric no data: <metric.name>"
    #   - délègue les notifications à tasks.notify (cooldown centralisé)
    #
    # Cette tâche est volontairement plus fréquente (60s) pour garder une
    # détection quasi temps réel de l'absence de nouvelles données.
    # ----------------------------------------------------------------------
    "check-metrics-no-data": {
        "task": "tasks.check_metrics_no_data",
        "schedule": 60.0,   # boucle rapide (near real-time) toutes les 60s
        "options": {"queue": "ingest"},
    },

    # ----------------------------------------------------------------------
    # 7) Auto-resolve des incidents threshold "stales" (optionnel)
    # ----------------------------------------------------------------------
    # Objectif :
    #   - éviter que des incidents threshold restent OPEN indéfiniment
    #     alors que la métrique n'a plus de données fraîches.
    #   - se base sur Sample.ts (source de vérité) + staleness_threshold_sec client.
    # Recommandation :
    #   - cadence quotidienne (ou toutes les 6h si tu veux plus agressif).
    # ----------------------------------------------------------------------
    "auto-resolve-stale-threshold-incidents-daily": {
        "task": "tasks.auto_resolve_stale_threshold_incidents",
        "schedule": 24 * 60 * 60,  # 1 jour
        "args": (24,),             # max_age_hours
    },


    "purge-samples-every-300s": {
        "task": "tasks.purge_samples",
        "schedule": 300.0,
        "args": (120, 200000),  # 2h de rétention, batch 200k
        "options": {"queue": "ingest"},
    },


}
