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
    return ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.openai_api_key,
        timeout=60,
        max_retries=2,
    )


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    settings = get_settings()
    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not configured")
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )
