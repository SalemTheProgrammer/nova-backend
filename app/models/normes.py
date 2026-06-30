"""Documents normatifs (BPF/GMP, etc.) indexés dans Pinecone pour le RAG."""
from __future__ import annotations

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Norme(Base, TimestampMixin):
    """Métadonnées d'un document normatif. Les vecteurs vivent dans Pinecone."""

    __tablename__ = "norme"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    fichier: Mapped[str] = mapped_column(String(255), nullable=False)
    nb_pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nb_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    statut: Mapped[str] = mapped_column(String(20), default="INDEXEE", nullable=False)
    # Ids des vecteurs Pinecone, pour pouvoir les supprimer.
    vector_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
