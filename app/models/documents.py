"""Documents d'entreprise (normes, procédures, manuels…) indexés dans Pinecone pour le RAG."""
from __future__ import annotations

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DocumentRag(Base, TimestampMixin):
    """Métadonnées d'un document indexé. Les vecteurs vivent dans Pinecone."""

    __tablename__ = "document_rag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    fichier: Mapped[str] = mapped_column(String(255), nullable=False)
    # Catégorie libre : norme, procédure, manuel machine, fiche technique…
    categorie: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nb_pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nb_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    statut: Mapped[str] = mapped_column(String(20), default="INDEXEE", nullable=False)
    # Ids des vecteurs Pinecone, pour pouvoir les supprimer.
    vector_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
