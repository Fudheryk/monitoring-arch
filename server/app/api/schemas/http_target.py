from __future__ import annotations
"""
server/app/api/schemas/http_target.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic schemas pour les HTTP targets.

- Valide strictement le schéma d’URL (http/https).
- Restreint la méthode HTTP à un Enum (GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS).
- Normalise les méthodes en majuscules côté schéma (ex: "get" -> "GET").
- Contraint les bornes des entiers (status code, timeouts, intervalle).
"""

from enum import Enum
from pydantic import BaseModel, Field, HttpUrl, field_validator


class HTTPMethod(str, Enum):
    """Méthodes HTTP autorisées côté API."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class HttpTargetIn(BaseModel):
    """
    Payload d’entrée pour la création/mise à jour d’une cible HTTP.

    Notes de validation :
    - `url` : refus des schémas non http/https (ex: ftp://).
    - `method` : Enum => rejet automatique d’une valeur inconnue (ex: "FETCH").
                 Un pré-validateur met en majuscule les chaînes pour permettre "get" → "GET".
    - `accepted_status_codes` : liste de paires [début, fin] pour définir des ranges.
      NULL = mode simple (tout code <500 accepté). Ex: [[200,299],[404,404]] = 2xx + 404 accepté.
    - `timeout_seconds` : borne raisonnable (1..120s).
    - `check_interval_seconds` : intervalle d’exécution (10s..24h).
    """

    # Nom lisible de la cible (affichage / tri). Borné pour éviter des valeurs déraisonnables.
    name: str = Field(..., min_length=1, max_length=200)

    # HttpUrl (Pydantic) + validateur custom pour restreindre à http(s) uniquement et
    # expliciter le message d’erreur.
    url: HttpUrl

    # Méthode HTTP restreinte à l’Enum. La valeur par défaut est GET.
    method: HTTPMethod = Field(default=HTTPMethod.GET, description="HTTP method")

    # Code de statut accepté.
    accepted_status_codes: list[list[int]] | None = Field(
        default=None,
        description="List of accepted HTTP status code ranges. Example: [[200,299],[404,404]]. NULL = simple mode (<500)."
    )

    # Délai d’attente de la requête.
    timeout_seconds: int = Field(default=30, ge=1, le=120)

    # Fréquence de vérification de la cible.
    check_interval_seconds: int = Field(default=300, ge=10, le=86_400)

    # Active/désactive la cible.
    is_active: bool = True

    # --- Validators ---------------------------------------------------------

    @field_validator("url")
    @classmethod
    def only_http_https(cls, v: HttpUrl) -> HttpUrl:
        """
        Restreint explicitement le schéma à http/https pour produire un message d’erreur clair.
        (HttpUrl est déjà http/https en pratique, mais ce validateur garantit le message.)
        """
        if v.scheme not in ("http", "https"):
            # Message lisible côté 422 pour tes tests
            raise ValueError("URL scheme should be 'http' or 'https'")
        return v

    @field_validator("method", mode="before")
    @classmethod
    def upper_if_str(cls, v):
        """
        Autorise les entrées non normalisées (ex: "get") en les passant en majuscules
        avant le cast automatique vers l’Enum (qui rejettera toute valeur inconnue).
        """
        return v.upper() if isinstance(v, str) else v

    model_config = {
        # Exemple utilisé par la doc OpenAPI (/docs) et pour les snapshots éventuels.
        "json_schema_extra": {
            "example": {
                "name": "Example Target",
                "url": "https://example.com/health",
                "method": "GET",
                "timeout_seconds": 10,
                "check_interval_seconds": 60,
                "is_active": True,
            }
        }
    }
