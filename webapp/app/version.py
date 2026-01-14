"""
webapp/app/version.py
Gestion automatique de la version de l'application web.
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

def get_git_commit_hash() -> str:
    """Récupère le hash git court du commit actuel."""
    try:
        # Remonter à la racine du repo pour trouver .git
        current_dir = Path(__file__).parent.parent.parent
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(current_dir),
        ).decode("utf-8").strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return os.getenv("GIT_COMMIT", "dev")

def get_build_timestamp() -> str:
    """Timestamp de build en ISO format UTC."""
    build_time = os.getenv("BUILD_TIMESTAMP")
    if build_time:
        return build_time
    return datetime.utcnow().isoformat() + "Z"

def get_app_version() -> str:
    """
    Version au format PEP 440 :
    {major}.{minor}.{patch}+{commit}.{build_date}
    Exemples valides :
    - 1.2.3+a722137.20240114T103000Z  (recommandé, PEP 440 compatible)
    - 1.2.3+a722137.2024-01-14T103000Z (presque bon, mais "-" sont OK)
    """
    # Version semver - peut être overridé par env vars
    major = os.getenv("VERSION_MAJOR", "1")
    minor = os.getenv("VERSION_MINOR", "0")
    patch = os.getenv("VERSION_PATCH", "0")
    
    semver = f"{major}.{minor}.{patch}"
    commit_hash = get_git_commit_hash()
    
    # Format PEP 440 compatible (sans ":" et avec Z)
    build_date = get_build_timestamp()[:19].replace(":", "").replace("-", "")
    # → "20240114T103000Z"
    
    return f"{semver}+{commit_hash}.{build_date}"