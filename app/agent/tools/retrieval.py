"""Knowledge-base retrieval tool backed by Pinecone."""
from __future__ import annotations

from langchain_core.tools import tool

from app.core.logging import get_logger
from app.services.vector_store import similarity_search

logger = get_logger(__name__)


@tool
def search_knowledge_base(query: str) -> str:
    """Search the internal knowledge base for information relevant to the query.

    Use this whenever the user asks about domain-specific facts, documents, or
    anything that may be stored in the knowledge base. Returns the most relevant
    passages as text.
    """
    logger.info("tool_search_knowledge_base", query=query)
    documents = similarity_search(query)
    if not documents:
        return "No relevant information found in the knowledge base."
    parts: list[str] = []
    for i, doc in enumerate(documents, start=1):
        source = doc.metadata.get("source", "unknown")
        parts.append(f"[{i}] (source: {source})\n{doc.page_content}")
    return "\n\n".join(parts)
