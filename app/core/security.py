"""API-key authentication dependency."""
from __future__ import annotations

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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
