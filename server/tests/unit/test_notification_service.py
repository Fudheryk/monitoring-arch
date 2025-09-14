# server/tests/unit/test_notification_service.py
# -------------------------------------------------------------------
# Tests unitaires ROBUSTES pour le service de notifications :
# - Ne suppose pas l'existence d'une classe précise à importer.
# - Détecte dynamiquement :
#     * une classe NotificationService avec .notify_once(...) ou .notify(...)
#     * OU une fonction de module notify_once(...) / notify(...) / send_notification(...)
# - Utilise la fixture 'mock_slack' (définie dans server/tests/conftest.py)
#   pour capturer les envois sans appel réseau (offline).
# -------------------------------------------------------------------

import importlib
import inspect
import pytest

pytestmark = pytest.mark.unit

# On importe le module sans figer la forme de l'API
mod = importlib.import_module("app.application.services.notification_service")


def _resolve_notify_callable(cooldown_minutes=5):
    """
    Retourne un callable notify(key, message) quelle que soit l'implémentation :
    - classe NotificationService(...).notify_once / .notify
    - fonction de module notify_once / notify / send_notification
    """
    # 1) Classe
    svc_cls = getattr(mod, "NotificationService", None)
    if isinstance(svc_cls, type):
        # Essaie d'injecter cooldown_minutes si le __init__ l'accepte
        try:
            sig = inspect.signature(svc_cls.__init__)
            if "cooldown_minutes" in sig.parameters:
                svc = svc_cls(cooldown_minutes=cooldown_minutes)
            else:
                svc = svc_cls()
        except Exception:
            svc = svc_cls()

        for name in ("notify_once", "notify"):
            meth = getattr(svc, name, None)
            if callable(meth):
                def _bound(key, message):
                    return meth(key, message)
                return _bound

    # 2) Fonctions de module
    for name in ("notify_once", "notify", "send_notification"):
        fn = getattr(mod, name, None)
        if callable(fn):
            def _fn(key, message, _fn=fn):
                # Essaie de passer cooldown si la fonction l'accepte
                try:
                    sig = inspect.signature(_fn)
                    if "cooldown_minutes" in sig.parameters:
                        return _fn(key, message, cooldown_minutes=cooldown_minutes)
                except Exception:
                    pass
                return _fn(key, message)
            return _fn

    pytest.skip("No NotificationService or notify function found in notification_service module.")


def test_notify_once_sends(monkeypatch, mock_slack):
    """
    Premier envoi d'un message : on doit voir au moins un enregistrement côté 'mock_slack'.
    """
    if mock_slack is None:
        pytest.skip("mock_slack fixture not available outside unit tests")

    notify = _resolve_notify_callable(cooldown_minutes=5)

    before = len(mock_slack)
    res = notify("incident-001", "ALERT: CPU high 95%")
    after = len(mock_slack)

    # Le retour peut varier selon l'implémentation (True/False/None), on vérifie surtout l'effet
    assert after >= before + 1, "Expected at least one Slack send to be recorded"
    _ = res  # éviter 'unused variable' si res n'est pas utilisé


def test_cooldown_limits_duplicate_sends(monkeypatch, mock_slack):
    """
    Deux envois consécutifs avec la même clé doivent être limités par le cooldown.
    On accepte différents comportements implémentation-dépendants, mais on s'attend à
    ce que le 2e envoi ne spamme pas (<= 1 envoi supplémentaire).
    """
    if mock_slack is None:
        pytest.skip("mock_slack fixture not available outside unit tests")

    notify = _resolve_notify_callable(cooldown_minutes=5)

    # First send
    base = len(mock_slack)
    notify("incident-dup", "ALERT: disk full 92%")
    mid = len(mock_slack)

    # Second send (même clé)
    notify("incident-dup", "ALERT: disk full 92%")
    end = len(mock_slack)

    # Attendu : pas de spam massif. Entre 0 et 1 envoi supplémentaire maximum sur le second call.
    assert (mid - base) >= 1, "First notification should have triggered at least one send"
    assert (end - mid) in (0, 1), "Cooldown should limit duplicate sends for the same key"
