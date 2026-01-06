# server/app/infrastructure/notifications/templates/loader.py
from __future__ import annotations
"""
Chargement de templates depuis le paquet (utilisé en PR5 pour email/slack).
"""
from importlib.resources import files


def load_template(name: str) -> str:
    """
    Lit un fichier template situé dans le même package.
    Ex: load_template("email_incident.html")
    """
    return files(__package__).joinpath(name).read_text(encoding="utf-8")
