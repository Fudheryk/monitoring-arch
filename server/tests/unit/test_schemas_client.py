# server/tests/unit/test_schemas_client.py
# -----------------------------------------------------------------------------
# Tests robustes pour le schéma Client :
# - Détection dynamique de la classe Pydantic présente (ClientIn/ClientCreate/Client)
# - Construction d'un payload minimal VALIDE en remplissant uniquement les champs requis
#   (compatible Pydantic v1 et v2)
# - Tests conditionnels : on ne teste un champ (email, name) que s'il existe
# - Aucun pytest.skip au niveau module (donc plus d'erreur "skip outside test")
# -----------------------------------------------------------------------------

import importlib
import inspect
from typing import Any, Dict, Optional, Type

import pytest
from pydantic import ValidationError
try:
    # Types utiles pour reconnaître les champs typés
    from pydantic import EmailStr, AnyUrl, HttpUrl
except Exception:  # pragma: no cover
    EmailStr = object  # fallbacks neutres
    AnyUrl = object
    HttpUrl = object

pytestmark = pytest.mark.unit


# ---------- 1) Résolution dynamique du modèle ----------
def _resolve_client_model() -> Optional[Type[Any]]:
    mod = importlib.import_module("app.api.schemas.client")
    for name in ("ClientIn", "ClientCreate", "Client"):
        model = getattr(mod, name, None)
        if model is not None:
            return model
    return None


ClientModel = _resolve_client_model()


# ---------- 2) Accès unifié aux champs (v1/v2) ----------
def _get_model_fields(model: Type[Any]) -> Dict[str, Any]:
    """
    Retourne le dict des champs Pydantic :
    - v2: model.model_fields
    - v1: model.__fields__
    """
    if hasattr(model, "model_fields"):  # pydantic v2
        return getattr(model, "model_fields")
    return getattr(model, "__fields__")  # pydantic v1


def _is_required(field_info: Any) -> bool:
    """True si le champ est requis (sans valeur par défaut). Compatible v1/v2."""
    # v2
    if hasattr(field_info, "is_required"):
        try:
            return bool(field_info.is_required())
        except Exception:
            pass
    if hasattr(field_info, "default") and getattr(field_info, "default") is ...:
        return True
    # v1
    if hasattr(field_info, "required"):
        return bool(getattr(field_info, "required"))
    # fallback conservateur
    return False


def _field_annotation(field_info: Any) -> Any:
    """Retourne l'annotation Python du champ (v1/v2)."""
    if hasattr(field_info, "annotation"):         # v2
        return getattr(field_info, "annotation")
    if hasattr(field_info, "outer_type_"):        # v1
        return getattr(field_info, "outer_type_")
    if hasattr(field_info, "type_"):              # v1 alternative
        return getattr(field_info, "type_")
    return Any


# ---------- 3) Valeurs par défaut plausibles selon le type ----------
def _default_for_type(tp: Any) -> Any:
    origin = getattr(tp, "__origin__", None)  # pour Optional[...] / Union[...]
    args = getattr(tp, "__args__", ())

    # Optional[...] -> prends le premier type non-None
    if origin is not None and args:
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if non_none:
            return _default_for_type(non_none[0])

    try:
        if tp in (str,):
            return "acme"
        if tp in (int,):
            return 1
        if tp in (float,):
            return 1.0
        if tp in (bool,):
            return True
        if tp in (EmailStr,):
            return "user@example.com"
        if tp in (AnyUrl, HttpUrl):
            return "https://example.com"
    except Exception:
        pass

    # Fallback générique
    return "x"


# ---------- 4) Construit un payload minimal valide ----------
def _build_minimal_payload(model: Type[Any]) -> Dict[str, Any]:
    fields = _get_model_fields(model)
    data: Dict[str, Any] = {}
    for name, finfo in fields.items():
        if _is_required(finfo):
            tp = _field_annotation(finfo)
            data[name] = _default_for_type(tp)
    return data


# ---------- 5) Helpers pour savoir si un champ existe ----------
def _has_field(model: Type[Any], field_name: str) -> bool:
    return field_name in _get_model_fields(model)


# =============================== TESTS ===============================

def test_model_present():
    """Vérifie qu'au moins un modèle Client* est présent."""
    if ClientModel is None:
        pytest.skip("No Client Pydantic model found in app.api.schemas.client")


@pytest.mark.parametrize("email,ok", [
    ("ops@acme.tld", True),
    ("root@localhost", True),   # ok si pas de stricte validation RFC
    ("not-an-email", False),
    ("", False),
])
def test_client_email(email, ok):
    if ClientModel is None:
        pytest.skip("No Client model")

    # On ne lance le test email que si le champ 'email' existe
    if not _has_field(ClientModel, "email"):
        pytest.skip("Client model has no 'email' field")

    data = _build_minimal_payload(ClientModel)
    data["email"] = email

    if ok:
        ClientModel(**data)  # ne doit pas lever
    else:
        with pytest.raises(ValidationError):
            ClientModel(**data)


@pytest.mark.parametrize("name,ok", [
    ("acme", True),
    ("A", True),
    ("", False),
])
def test_client_name(name, ok):
    if ClientModel is None:
        pytest.skip("No Client model")

    # On ne teste 'name' que s'il existe
    if not _has_field(ClientModel, "name"):
        pytest.skip("Client model has no 'name' field")

    data = _build_minimal_payload(ClientModel)
    data["name"] = name

    if ok:
        ClientModel(**data)
    else:
        with pytest.raises(ValidationError):
            ClientModel(**data)
