# ---------- STAGE 1: Builder ----------
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Installer outils de compilation pour psycopg2 / autres packages compilés
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/server

# Copier requirements prod et installer
COPY requirements.txt ./
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copier le code serveur
COPY server /app/server

# Installer l'app dans /install pour multi-stage
RUN pip install --prefix=/install .

# ---------- STAGE 2: Image finale ----------
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/server \
    DEBIAN_FRONTEND=noninteractive

# Installer runtime essentials
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Créer un utilisateur non-root
RUN useradd -ms /bin/bash appuser

WORKDIR /app/server

# Copier les packages installés depuis le builder
COPY --from=builder /install /usr/local

# Coverage est safe même en prod
RUN pip install --no-cache-dir coverage

# Copier le code et l'entrypoint
COPY server /app/server
COPY server/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
