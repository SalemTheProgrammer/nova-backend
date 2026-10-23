"""PostgreSQL + pgvector vector store service for RAG."""
from __future__ import annotations

import json
import uuid

import psycopg
from langchain_core.documents import Document
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError, VectorStoreError
from app.core.logging import get_logger
from app.services.llm import get_embeddings

logger = get_logger(__name__)

# Namespace de la base documentaire (normes, procédures, manuels…).
DOCUMENTS_NAMESPACE = "documents"


def _connect() -> psycopg.Connection:
    settings = get_settings()
    if not settings.vector_database_url:
        raise ConfigurationError("VECTOR_DATABASE_URL is not configured")
    return psycopg.connect(settings.vector_database_url)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"


def ensure_index_exists() -> None:
    """Create the pgvector extension, table and index if missing. Idempotent."""
    settings = get_settings()
    table = settings.vector_table_name
    dim = int(settings.vector_embedding_dimension)
    with _connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id text PRIMARY KEY,
                namespace text NOT NULL,
                content text NOT NULL,
                metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                embedding vector({dim}) NOT NULL
            )
            """
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_namespace_idx ON {table} (namespace)")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {table}_embedding_idx "
            f"ON {table} USING hnsw (embedding vector_cosine_ops)"
        )


# --------------------------------------------------------------------------- #
# Base documentaire — namespace dédié avec citations page par page
# --------------------------------------------------------------------------- #
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def add_document_chunks(documents: list[Document]) -> list[str]:
    """Index document chunks in the dedicated 'documents' namespace."""
    try:
        table = get_settings().vector_table_name
        embeddings = get_embeddings().embed_documents([d.page_content for d in documents])
        ids = [str(uuid.uuid4()) for _ in documents]
        with _connect() as conn, conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {table} (id, namespace, content, metadata, embedding) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::vector)",
                [
                    (
                        doc_id,
                        DOCUMENTS_NAMESPACE,
                        doc.page_content,
                        json.dumps(doc.metadata, default=str),
                        _vector_literal(vec),
                    )
                    for doc_id, doc, vec in zip(ids, documents, embeddings, strict=True)
                ],
            )
        return ids
    except Exception as exc:  # noqa: BLE001
        logger.error("add_document_chunks_failed", error=str(exc))
        raise VectorStoreError("Failed to index document") from exc


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def search_documents(query: str, *, top_k: int = 5) -> list[tuple[Document, float]]:
    """Search the documents namespace, returning (document, score) pairs for citations.

    The score is the cosine similarity (1 = identical), as returned by Pinecone before.
    """
    try:
        table = get_settings().vector_table_name
        vec = _vector_literal(get_embeddings().embed_query(query))
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT content, metadata, 1 - (embedding <=> %s::vector) AS score "
                f"FROM {table} WHERE namespace = %s "
                f"ORDER BY embedding <=> %s::vector LIMIT %s",
                (vec, DOCUMENTS_NAMESPACE, vec, top_k),
            ).fetchall()
        return [(Document(page_content=c, metadata=m or {}), float(s)) for c, m, s in rows]
    except Exception as exc:  # noqa: BLE001
        logger.error("search_documents_failed", error=str(exc))
        raise VectorStoreError("Document search failed") from exc


def delete_document_vectors(ids: list[str]) -> None:
    """Remove a document's vectors from the 'documents' namespace by id."""
    if not ids:
        return
    try:
        table = get_settings().vector_table_name
        with _connect() as conn:
            conn.execute(
                f"DELETE FROM {table} WHERE namespace = %s AND id = ANY(%s)",
                (DOCUMENTS_NAMESPACE, ids),
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_document_vectors_failed", error=str(exc))
        raise VectorStoreError("Failed to delete document vectors") from exc
