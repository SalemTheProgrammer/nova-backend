"""LLM and embedding client factories."""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError


@lru_cache
def get_chat_model() -> ChatOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not configured")
    modele = settings.llm_model
    options: dict = {
        "model": modele,
        "temperature": settings.llm_temperature,
        "api_key": settings.openai_api_key,
        "timeout": 60,
        "max_retries": 2,
    }
    # Les modèles de raisonnement gpt-5.x refusent les function tools sur
    # /v1/chat/completions sans reasoning_effort="none" (erreur 400). On force
    # donc l'exécution directe, sans réflexion étendue : c'est ce qu'on veut pour
    # un agent d'atelier rapide qui enchaîne des appels d'outils.
    if modele.startswith("gpt-5"):
        options["reasoning_effort"] = "none"
    return ChatOpenAI(**options)


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    settings = get_settings()
    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not configured")
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )
