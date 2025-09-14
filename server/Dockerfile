# ---------- IMAGE PROD ----------
FROM python:3.11-slim-bookworm

# Variables d'environnement Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app/server \
    PATH=/usr/local/bin:$PATH

# Installer runtime essentials et outils de compilation pour psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Créer utilisateur non-root
RUN useradd -ms /bin/bash appuser

WORKDIR /app/server

# Copier requirements et installer toutes les dépendances
COPY requirements.txt ./
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copier le code source
COPY server /app/server

# Installer l'app en editable pour que Alembic fonctionne
RUN pip install -e . \
    && pip install --no-cache-dir coverage

# Copier l'entrypoint et fixer les permissions
COPY server/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R appuser:appuser /app

# Switch user non-root
USER appuser

# Exposer le port de l'API
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
