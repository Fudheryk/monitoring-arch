from __future__ import annotations

"""
Repository d'accès à ClientSettings.

Principes:
- Pas de commit() ici : le code appelant contrôle la transaction (unit of work).
- Expose des getters "effectifs" (avec fallback config) pour la lecture simple
  depuis les services/Tasks sans dupliquer la logique de repli.
- Upsert par (client_id) : crée ou met à jour l'entrée de configuration du client.
"""

import re

from typing import Optional, Iterable, Any
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.infrastructure.persistence.database.models.client_settings import ClientSettings
from app.core.config import settings

from urllib.parse import urlparse


class ClientSettingsRepository:
    """Accès et manipulation des préférences client."""

    def __init__(self, db: Session):
        self.db = db

    # ---------------------------
    # Lectures de base
    # ---------------------------

    def get_by_client_id(self, client_id: UUID) -> Optional[ClientSettings]:
        """Retourne l'entrée ClientSettings pour ce client, ou None."""
        return self.db.scalar(
            select(ClientSettings).where(ClientSettings.client_id == client_id)
        )

    def exists_for_client(self, client_id: UUID) -> bool:
        """True si un ClientSettings existe déjà pour ce client."""
        return self.get_by_client_id(client_id) is not None

    # ---------------------------
    # Upsert & mises à jour
    # ---------------------------

    def upsert(
        self,
        client_id: UUID,
        *,
        reminder_notification_seconds: Optional[int] = None,
        slack_webhook_url: Optional[str] = None,
        slack_channel_name: Optional[str] = None,
        notification_email: Optional[str] = None,
        alert_grouping_enabled: Optional[bool] = None,
        alert_grouping_window_seconds: Optional[int] = None,
        notify_on_resolve: Optional[bool] = None,
        grace_period_seconds: Optional[int] = None,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> ClientSettings:
        """
        Crée ou met à jour l'entrée ClientSettings du client.
        Ne commit PAS : à charge de l'appelant de gérer la transaction.

        extra_fields permet d'injecter d'autres colonnes éventuelles sans changer la signature.
        """
        cs = self.get_by_client_id(client_id)
        if cs is None:
            cs = ClientSettings(client_id=client_id)
            self.db.add(cs)

        if reminder_notification_seconds is not None:
            cs.reminder_notification_seconds = int(reminder_notification_seconds)
        if slack_webhook_url is not None:
            cs.slack_webhook_url = slack_webhook_url
        if slack_channel_name is not None:
            cs.slack_channel_name = (slack_channel_name or "").strip() or None
        if notification_email is not None:
            cs.notification_email = notification_email
        if alert_grouping_enabled is not None:
            cs.alert_grouping_enabled = bool(alert_grouping_enabled)
        if alert_grouping_window_seconds is not None:
            cs.alert_grouping_window_seconds = int(alert_grouping_window_seconds)
        if notify_on_resolve is not None:
            cs.notify_on_resolve = bool(notify_on_resolve)
        if grace_period_seconds is not None:
            cs.grace_period_seconds = int(grace_period_seconds)

        if extra_fields:
            for k, v in extra_fields.items():
                # Définition prudente uniquement si l'attribut existe
                if hasattr(cs, k):
                    setattr(cs, k, v)

        # Pas de commit ici — l'appelant gère s.commit()
        return cs

    def update_partial(
        self,
        client_id: UUID,
        fields: dict[str, Any],
        *,
        create_if_missing: bool = False,
    ) -> ClientSettings:
        """
        Met à jour un sous-ensemble de champs. Si create_if_missing=True et
        l'entrée n'existe pas, elle sera créée.
        """
        cs = self.get_by_client_id(client_id)
        if cs is None:
            if not create_if_missing:
                raise ValueError(f"No ClientSettings for client_id={client_id}")
            cs = ClientSettings(client_id=client_id)
            self.db.add(cs)

        for k, v in fields.items():
            if hasattr(cs, k):
                setattr(cs, k, v)

        return cs

    # Raccourcis ciblés

    def set_reminder_seconds(self, client_id: UUID, seconds: int, *, create_if_missing: bool = True) -> ClientSettings:
        """Fixe le rappel en SECONDES (anti-spam)."""
        if seconds <= 0:
            raise ValueError("reminder_notification_seconds must be > 0")
        return self.update_partial(
            client_id,
            {"reminder_notification_seconds": int(seconds)},
            create_if_missing=create_if_missing,
        )

    def set_slack_webhook(self, client_id: UUID, webhook_url: str, *, create_if_missing: bool = True) -> ClientSettings:
        """Enregistre l'URL de webhook Slack pour ce client."""
        parsed_url = urlparse(webhook_url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError(f"Invalid webhook URL: {webhook_url}")
        return self.update_partial(
            client_id,
            {"slack_webhook_url": webhook_url},
            create_if_missing=create_if_missing,
        )

    def set_slack_channel_name(
        self,
        client_id: UUID,
        slack_channel_name: Optional[str],
        *,
        create_if_missing: bool = True,
    ) -> ClientSettings:
        """
        Enregistre le nom du canal Slack par défaut pour ce client.

        Règles :
        - None ou chaîne vide => on stocke NULL (canal non défini)
        - Le canal doit commencer par '#'
        - Caractères autorisés : lettres, chiffres, tiret, underscore
        - Longueur raisonnable (<= 15 caractères)
        - La normalisation (ajout du '#') est faite ici pour garantir la cohérence DB

        Exemples valides :
        - "#alerts"
        - "#notif_webhook"
        - "#prod-ops"

        Exemples invalides :
        - "alerts"
        - "#"
        - "#alert!"
        - "# canal"
        """

        # ─────────────────────────────────────────────
        # 1) Normalisation des entrées "vides"
        # ─────────────────────────────────────────────
        if slack_channel_name is None:
            # NULL explicite → canal non défini
            return self.update_partial(
                client_id,
                {"slack_channel_name": None},
                create_if_missing=create_if_missing,
            )

        name = slack_channel_name.strip()

        if name == "":
            # Chaîne vide => on considère "non défini"
            return self.update_partial(
                client_id,
                {"slack_channel_name": None},
                create_if_missing=create_if_missing,
            )

        # ─────────────────────────────────────────────
        # 2) Normalisation : forcer le '#'
        # ─────────────────────────────────────────────
        if not name.startswith("#"):
            name = f"#{name}"

        # ─────────────────────────────────────────────
        # 3) Validation stricte du format Slack
        # ─────────────────────────────────────────────
        # Slack autorise : lowercase recommandé, chiffres, -, _
        # On accepte les majuscules mais on peut les normaliser si tu veux
        CHANNEL_REGEX = re.compile(r"^#[a-zA-Z0-9_-]{1,15}$")

        if not CHANNEL_REGEX.match(name):
            raise ValueError(f"Invalid Slack channel name: {slack_channel_name}")

        # ─────────────────────────────────────────────
        # 4) Persistance
        # ─────────────────────────────────────────────
        return self.update_partial(
            client_id,
            {"slack_channel_name": name},
            create_if_missing=create_if_missing,
        )

    def set_notification_email(self, client_id: UUID, email: str, *, create_if_missing: bool = True) -> ClientSettings:
        """Enregistre l'email de notification pour ce client."""
        return self.update_partial(
            client_id,
            {"notification_email": email},
            create_if_missing=create_if_missing,
        )

    def set_grouping(
        self,
        client_id: UUID,
        *,
        enabled: bool,
        window_seconds: Optional[int] = None,
        create_if_missing: bool = True,
    ) -> ClientSettings:
        """Active/désactive le grouping et met à jour éventuellement la fenêtre d'agrégation en secondes."""
        fields: dict[str, Any] = {"alert_grouping_enabled": bool(enabled)}
        if window_seconds is not None:
            if window_seconds <= 0:
                raise ValueError("alert_grouping_window_seconds must be > 0")
            fields["alert_grouping_window_seconds"] = int(window_seconds)
        return self.update_partial(client_id, fields, create_if_missing=create_if_missing)

    def set_grace_period_seconds(self, client_id: UUID, seconds: int, *, create_if_missing: bool = True) -> ClientSettings:
        """Fixe le délai de grâce en secondes pour ce client."""
        if seconds < 0:
            raise ValueError("grace_period_seconds must be >= 0")
        return self.update_partial(
            client_id,
            {"grace_period_seconds": int(seconds)},
            create_if_missing=create_if_missing,
        )

    # ---------------------------
    # Getters "effectifs" (avec fallback)
    # ---------------------------

    def get_effective_reminder_seconds(self, client_id: UUID) -> int:
        """
        Retourne la cadence de rappel anti-spam EN SECONDES pour ce client.
        Priorité :
        1) client_settings.reminder_notification_seconds (>0)
        2) settings.ALERT_REMINDER_MINUTES (minutes -> secondes)
        3) défaut dur = 30 minutes
        """
        DEFAULT_SECONDS = 30 * 60
        cs = self.get_by_client_id(client_id)
        if cs and isinstance(cs.reminder_notification_seconds, int) and cs.reminder_notification_seconds > 0:
            return cs.reminder_notification_seconds
        try:
            minutes = int(getattr(settings, "ALERT_REMINDER_MINUTES", 30))
            return max(1, minutes) * 60
        except Exception:
            return DEFAULT_SECONDS

    def get_effective_slack_webhook(self, client_id: UUID) -> Optional[str]:
        """
        RETOURNE SEULEMENT le webhook Slack du client. PAS de fallback global.
        """
        cs = self.get_by_client_id(client_id)
        if cs and (cs.slack_webhook_url or "").strip():
            return cs.slack_webhook_url.strip()
        # Plus de fallback → None si pas configuré au niveau client
        return None

    def get_effective_notification_email(self, client_id: UUID) -> Optional[str]:
        """Retourne l'email de notification à utiliser, s'il est configuré côté client."""
        cs = self.get_by_client_id(client_id)
        if cs and cs.notification_email:
            return cs.notification_email.strip()
        return None

    def get_effective_notify_on_resolve(self, client_id: UUID) -> bool:
        cs = self.get_by_client_id(client_id)
        # Fallback True si non trouvé ou champ NULL (sécurité)
        return True if not cs else bool(getattr(cs, "notify_on_resolve", True))

    def get_alert_grouping_settings(self, client_id: UUID) -> dict[str, Any]:
        """
        Retourne la config de grouping d'alertes:
        {
            "enabled": bool,
            "window_seconds": Optional[int]  # None -> pas de grouping
        }
        """
        cs = self.get_by_client_id(client_id)
        if not cs:
            return {"enabled": False, "window_seconds": None}

        enabled = bool(getattr(cs, "alert_grouping_enabled", False))

        raw = getattr(cs, "alert_grouping_window_seconds", None)
        try:
            window_seconds: Optional[int] = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            window_seconds = None  # valeur illisible -> désactive

        if window_seconds is not None and window_seconds <= 0:
            window_seconds = None  # normalise ≤0 en désactivation

        return {"enabled": enabled, "window_seconds": window_seconds}

    def get_effective_grace_period_seconds(self, client_id: UUID | None) -> int:
        """
        Retourne le délai de grâce en secondes (DB > ENV > défaut).
        - DB: client_settings.grace_period_seconds
        - ENV: settings.GRACE_PERIOD_SECONDS (optionnel)
        - défaut: 120
        """
        default_val = getattr(settings, "GRACE_PERIOD_SECONDS", 120)
        cs = self.get_by_client_id(client_id) if client_id else None
        try:
            val = int(getattr(cs, "grace_period_seconds", None))
            if val is None or val < 0:
                return default_val
            return val
        except Exception:
            return default_val

    def get_effective_metric_staleness_seconds(self, client_id: UUID) -> int:
        """
        Seuil d'absence de données pour les métriques ("no data") en SECONDES.

        Priorité :
        1) ClientSettings.heartbeat_threshold_minutes (par client, en minutes)
        2) settings.METRIC_STALENESS_SECONDS (global, en secondes)
        3) défaut dur : 300 secondes
        """
        DEFAULT_SECONDS = 300
        cs = self.get_by_client_id(client_id)

        # 1) Valeur par client (en minutes → secondes)
        if cs is not None:
            raw_minutes = getattr(cs, "heartbeat_threshold_minutes", None)
            try:
                if raw_minutes is not None:
                    minutes = int(raw_minutes)
                    if minutes > 0:
                        return minutes * 60
            except (TypeError, ValueError):
                # on ignore et on tombe sur le fallback global
                pass

        # 2) Fallback ENV global (déjà en secondes)
        try:
            raw = getattr(settings, "METRIC_STALENESS_SECONDS", DEFAULT_SECONDS)
            seconds = int(raw)
            if seconds > 0:
                return seconds
        except Exception:
            pass

        # 3) Défaut dur
        return DEFAULT_SECONDS

    # ---------------------------
    # Utilitaires divers
    # ---------------------------

    def ensure_many(self, client_ids: Iterable[UUID]) -> list[ClientSettings]:
        """
        Garantit l'existence d'une entrée ClientSettings pour chaque client_id fourni.
        Ne commit pas ; retourne la liste (créée ou existante).
        """
        created_or_found: list[ClientSettings] = []
        for cid in client_ids:
            cs = self.get_by_client_id(cid)
            if cs is None:
                cs = ClientSettings(client_id=cid)
                self.db.add(cs)
            created_or_found.append(cs)
        return created_or_found
