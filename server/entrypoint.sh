#!/usr/bin/env bash
set -euo pipefail

if [ $# -eq 0 ] || [ "$1" = "api" ]; then
  # Démarrage API par défaut (ou si "api" est passé en arg)
  alembic upgrade head
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000
else
  # Pour worker/beat (ou toute autre commande)
  exec "$@"
fi
