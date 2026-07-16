"""Authentication dependencies : clé d'API (infra) + jeton utilisateur (numéro)."""
from __future__ import annotations

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, AuthenticationError
from app.db.session import get_db
from app.models.utilisateur import Utilisateur

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer = HTTPBearer(auto_error=False)


async def require_api_key(
    api_key: str | None = Security(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> str:
    # If no API keys are configured, auth is disabled (useful for local dev).
    if not settings.api_keys:
        return "anonymous"
    if api_key is None or api_key not in settings.api_keys:
        raise AuthenticationError("Invalid or missing API key")
    return api_key


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
    db: Session = Depends(get_db),
) -> Utilisateur:
    """Résout l'utilisateur à partir du jeton `Authorization: Bearer <token>`."""
    # Import local pour éviter un cycle (auth_service importe notify_service).
    from app.services import auth_service

    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Jeton d'authentification manquant.")
    payload = auth_service.verifier_token(credentials.credentials)
    user = db.get(Utilisateur, int(payload.get("sub", 0)))
    if user is None or not user.actif:
        raise AuthenticationError("Compte introuvable ou désactivé.")
    return user


async def require_admin(user: Utilisateur = Depends(get_current_user)) -> Utilisateur:
    """Réserve un accès aux administrateurs (page + API d'administration)."""
    if not user.is_admin:
        raise ForbiddenError("Accès réservé à l'administrateur.")
    return user
