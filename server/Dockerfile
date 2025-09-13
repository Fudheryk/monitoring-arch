# server/Dockerfile
# Image "prod" multi-étapes : on build + installe l'appli en layer dédié,
# puis on copie dans l'image finale. On ajoute "coverage" pour le mode API_COVERAGE=1.

FROM python:3.11-slim-bookworm AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/server
COPY server/pyproject.toml /app/server/
RUN pip install --upgrade pip wheel setuptools
COPY server /app/server

# Installe l'app dans /install
RUN pip install --prefix=/install .

# ─────────────────────────────────────────────

FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/server DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Utilisateur non-root
RUN useradd -ms /bin/bash appuser

WORKDIR /app/server

# Libs Python de l'app
COPY --from=builder /install /usr/local

# 'coverage' pour le mode API_COVERAGE=1 (safe même en prod ; peu coûteux)
RUN pip install --no-cache-dir coverage

# Code + entrypoint
COPY server /app/server
COPY server/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
