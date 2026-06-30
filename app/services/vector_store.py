"""Pinecone vector store service for RAG."""
from __future__ import annotations

from functools import lru_cache

from langchain_core.documents import Document
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError, VectorStoreError
from app.core.logging import get_logger
from app.services.llm import get_embeddings

logger = get_logger(__name__)

# Namespace isolating regulatory norm documents from the general knowledge base.
NORMES_NAMESPACE = "normes"


@lru_cache
def _get_pinecone_client() -> Pinecone:
    settings = get_settings()
    if not settings.pinecone_api_key:
        raise ConfigurationError("PINECONE_API_KEY is not configured")
    return Pinecone(api_key=settings.pinecone_api_key)


def ensure_index_exists() -> None:
    """Create the Pinecone index if it does not already exist. Idempotent."""
    settings = get_settings()
    client = _get_pinecone_client()
    existing = {idx["name"] for idx in client.list_indexes()}
    if settings.pinecone_index_name in existing:
        return
    logger.info("creating_pinecone_index", index=settings.pinecone_index_name)
    client.create_index(
        name=settings.pinecone_index_name,
        dimension=settings.pinecone_embedding_dimension,
        metric="cosine",
        spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region),
    )


@lru_cache
def get_vector_store() -> PineconeVectorStore:
    settings = get_settings()
    _get_pinecone_client()  # validates configuration
    return PineconeVectorStore(
        index_name=settings.pinecone_index_name,
        embedding=get_embeddings(),
        pinecone_api_key=settings.pinecone_api_key,
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def similarity_search(query: str, *, top_k: int | None = None) -> list[Document]:
    settings = get_settings()
    k = top_k or settings.retrieval_top_k
    try:
        return get_vector_store().similarity_search(query, k=k)
    except Exception as exc:  # noqa: BLE001
        logger.error("similarity_search_failed", error=str(exc))
        raise VectorStoreError("Vector store query failed") from exc


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def add_documents(documents: list[Document]) -> list[str]:
    try:
        return get_vector_store().add_documents(documents)
    except Exception as exc:  # noqa: BLE001
        logger.error("add_documents_failed", error=str(exc))
        raise VectorStoreError("Failed to write documents to vector store") from exc


# --------------------------------------------------------------------------- #
# Normes (regulatory documents) — isolated namespace with page-level citations
# --------------------------------------------------------------------------- #
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def add_norme_documents(documents: list[Document]) -> list[str]:
    """Index norm-document chunks in the dedicated 'normes' namespace."""
    try:
        return get_vector_store().add_documents(documents, namespace=NORMES_NAMESPACE)
    except Exception as exc:  # noqa: BLE001
        logger.error("add_norme_documents_failed", error=str(exc))
        raise VectorStoreError("Failed to index norm document") from exc


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def search_normes(query: str, *, top_k: int = 5) -> list[tuple[Document, float]]:
    """Search the norms namespace, returning (document, score) pairs for citations."""
    try:
        return get_vector_store().similarity_search_with_score(
            query, k=top_k, namespace=NORMES_NAMESPACE
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("search_normes_failed", error=str(exc))
        raise VectorStoreError("Norm search failed") from exc


def delete_norme_vectors(ids: list[str]) -> None:
    """Remove a norm document's vectors from the 'normes' namespace by id."""
    if not ids:
        return
    try:
        get_vector_store().delete(ids=ids, namespace=NORMES_NAMESPACE)
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_norme_vectors_failed", error=str(exc))
        raise VectorStoreError("Failed to delete norm vectors") from exc
