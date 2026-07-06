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

# Namespace de la base documentaire (normes, procédures, manuels…).
DOCUMENTS_NAMESPACE = "documents"


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


# --------------------------------------------------------------------------- #
# Base documentaire — namespace dédié avec citations page par page
# --------------------------------------------------------------------------- #
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def add_document_chunks(documents: list[Document]) -> list[str]:
    """Index document chunks in the dedicated 'documents' namespace."""
    try:
        return get_vector_store().add_documents(documents, namespace=DOCUMENTS_NAMESPACE)
    except Exception as exc:  # noqa: BLE001
        logger.error("add_document_chunks_failed", error=str(exc))
        raise VectorStoreError("Failed to index document") from exc


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def search_documents(query: str, *, top_k: int = 5) -> list[tuple[Document, float]]:
    """Search the documents namespace, returning (document, score) pairs for citations."""
    try:
        return get_vector_store().similarity_search_with_score(
            query, k=top_k, namespace=DOCUMENTS_NAMESPACE
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("search_documents_failed", error=str(exc))
        raise VectorStoreError("Document search failed") from exc


def delete_document_vectors(ids: list[str]) -> None:
    """Remove a document's vectors from the 'documents' namespace by id."""
    if not ids:
        return
    try:
        get_vector_store().delete(ids=ids, namespace=DOCUMENTS_NAMESPACE)
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_document_vectors_failed", error=str(exc))
        raise VectorStoreError("Failed to delete document vectors") from exc
