"""
Gestion de la version enrichie avec metadata CI/CD.
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Tuple

# Version de base depuis importlib.metadata
try:
    import importlib.metadata
    BASE_VERSION = importlib.metadata.version("neonmonitor-web")
except importlib.metadata.PackageNotFoundError:
    # Fallback pour dev sans package installé
    BASE_VERSION = "0.0.0+dev.local"

def get_git_commit_hash() -> str:
    """Récupère le hash git court."""
    try:
        # webapp/ est dans monitoring-arch/webapp/
        repo_root = Path(__file__).parent.parent.parent.parent
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(repo_root),
        ).decode("utf-8").strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return os.getenv("GIT_COMMIT", "unknown")

def get_build_timestamp() -> str:
    """Timestamp UTC ISO."""
    return os.getenv("BUILD_TIMESTAMP", datetime.utcnow().isoformat() + "Z")

def get_build_number() -> str:
    """Numéro de build CI."""
    return os.getenv("BUILD_NUMBER", "0")

def get_full_version() -> str:
    """
    Version complète PEP 440 : {base}+{commit}.{build_num}.{timestamp}
    """
    commit = get_git_commit_hash()
    build_num = get_build_number()
    timestamp = get_build_timestamp()[:19].replace(":", "")
    
    if commit != "unknown":
        return f"{BASE_VERSION}+{commit}.{build_num}.{timestamp}"
    return BASE_VERSION

# Variables exportées
APP_VERSION = get_full_version()
GIT_COMMIT = get_git_commit_hash()
BUILD_TIMESTAMP = get_build_timestamp()
BUILD_NUMBER = get_build_number()
BASE_SEMVER = BASE_VERSION.split('+')[0]
VERSION_CACHE_BUST = GIT_COMMIT if GIT_COMMIT != "unknown" else "dev"