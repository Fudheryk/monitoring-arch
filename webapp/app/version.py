"""
Gestion de la version enrichie avec metadata CI/CD.

Ce fichier lit la version depuis pyproject.toml et ajoute des métadonnées Git.
PEP 440 format: {base}+{commit}.{build}.{timestamp}
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------
# 1. VERSION DE BASE - Lit directement depuis pyproject.toml
# ----------------------------------------------------------------------
def get_base_version() -> str:
    """Lit la version semver depuis le pyproject.toml du webapp."""
    try:
        # Python 3.11+ a tomllib intégré
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            # Fallback pour Python < 3.11
            import tomli as tomllib
        
        # Chemin vers pyproject.toml dans webapp/
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            
            # Supporte les deux formats courants
            if "project" in data and "version" in data["project"]:
                return data["project"]["version"]
            elif "tool" in data and "poetry" in data["tool"] and "version" in data["tool"]["poetry"]:
                return data["tool"]["poetry"]["version"]
            else:
                raise KeyError("Version non trouvée dans pyproject.toml")
                
    except Exception as e:
        # Fallback explicite avec indication d'erreur
        return f"0.0.0+error.{type(e).__name__}"

BASE_VERSION = get_base_version()

# ----------------------------------------------------------------------
# 2. MÉTADONNÉES GIT (optionnelles, enrichissent la version)
# ----------------------------------------------------------------------
def get_git_commit_hash() -> str:
    """Récupère le hash git court (7 caractères)."""
    try:
        # Remonte à la racine du monorepo (monitoring-arch/)
        repo_root = Path(__file__).parent.parent.parent.parent
        
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(repo_root),
        ).decode("utf-8").strip()
        
        # Vérifie si le dépôt a des modifications non commitées
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            cwd=str(repo_root),
        ).decode("utf-8").strip()
        
        # Ajoute .dirty si modifications en attente
        return f"{commit}.dirty" if status else commit
        
    except (subprocess.SubprocessError, FileNotFoundError):
        # Fallback aux variables d'environnement CI/CD
        return os.getenv("GIT_COMMIT", "unknown")

def get_build_timestamp() -> str:
    """Timestamp UTC ISO8601 pour le build."""
    return os.getenv("BUILD_TIMESTAMP", datetime.utcnow().isoformat() + "Z")

def get_build_number() -> str:
    """Numéro de build incrémental (CI/CD)."""
    return os.getenv("BUILD_NUMBER", "0")

# ----------------------------------------------------------------------
# 3. VERSION COMPLÈTE PEP 440
# ----------------------------------------------------------------------
def get_full_version() -> str:
    """
    Construit la version complète au format PEP 440.
    
    Formats:
    - Production: 1.0.0+abc123.42
    - Dev propre: 1.0.0+abc123.0
    - Dev sale:   1.0.0+abc123.dirty.0
    - Erreur:     0.0.0+error.ImportError
    """
    base_clean = BASE_VERSION.split('+')[0]  # Enlève les métadonnées existantes
    commit = get_git_commit_hash()
    build_num = get_build_number()
    
    if commit == "unknown":
        # Pas de Git disponible
        return BASE_VERSION
    elif "error" in BASE_VERSION:
        # Erreur de lecture pyproject.toml
        return BASE_VERSION
    elif ".dirty" in commit:
        # Développement avec modifications non commitées
        commit_clean = commit.replace(".dirty", "")
        return f"{base_clean}+dev.{commit_clean}.dirty.{build_num}"
    else:
        # Build propre (CI/CD ou dev commité)
        return f"{base_clean}+{commit}.{build_num}"

# ----------------------------------------------------------------------
# 4. VARIABLES EXPORTÉES (API publique)
# ----------------------------------------------------------------------
APP_VERSION = get_full_version()
GIT_COMMIT = get_git_commit_hash()
BUILD_TIMESTAMP = get_build_timestamp()
BUILD_NUMBER = get_build_number()
BASE_SEMVER = '.'.join(BASE_VERSION.split('+')[0].split('.')[:3])

# Pour cache busting des assets statiques
VERSION_CACHE_BUST = GIT_COMMIT if GIT_COMMIT != "unknown" else datetime.utcnow().strftime("%Y%m%d%H%M%S")

# ----------------------------------------------------------------------
# 5. DIAGNOSTIC (pour débogage, optionnel)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Version: {APP_VERSION}")
    print(f"Base:    {BASE_VERSION}")
    print(f"Commit:  {GIT_COMMIT}")
    print(f"Build:   {BUILD_NUMBER} ({BUILD_TIMESTAMP})")
    print(f"Cache:   {VERSION_CACHE_BUST}")