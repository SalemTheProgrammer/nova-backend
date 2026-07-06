"""Ingestion de documents (normes, procédures, manuels…) : extraction PDF par page,
découpage, indexation.

Chaque chunk conserve son numéro de page pour permettre des citations précises.
"""
from __future__ import annotations

import io

from langchain_core.documents import Document
from pypdf import PdfReader

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.services.vector_store import add_document_chunks

logger = get_logger(__name__)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def extraire_pages(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Extrait le texte page par page. Renvoie [(numero_page, texte), ...] (1-indexé)."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:  # noqa: BLE001
        raise AppError("PDF illisible ou corrompu.") from exc
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        texte = (page.extract_text() or "").strip()
        if texte:
            pages.append((i, texte))
    return pages


def _decouper(texte: str) -> list[str]:
    """Découpe un texte en chunks ~CHUNK_SIZE caractères, avec recouvrement.

    Sépare d'abord sur les paragraphes pour garder des unités sémantiques.
    """
    paragraphes = [p.strip() for p in texte.split("\n") if p.strip()]
    chunks: list[str] = []
    courant = ""
    for para in paragraphes:
        if len(courant) + len(para) + 1 <= CHUNK_SIZE:
            courant = f"{courant}\n{para}".strip()
        else:
            if courant:
                chunks.append(courant)
            if len(para) <= CHUNK_SIZE:
                courant = para
            else:
                # paragraphe trop long : découpage glissant
                for start in range(0, len(para), CHUNK_SIZE - CHUNK_OVERLAP):
                    chunks.append(para[start : start + CHUNK_SIZE])
                courant = ""
    if courant:
        chunks.append(courant)
    return chunks


def construire_documents(
    pages: list[tuple[int, str]], *, document_id: int, document_nom: str, fichier: str
) -> list[Document]:
    """Transforme les pages en chunks LangChain avec métadonnées (page incluse)."""
    docs: list[Document] = []
    for numero_page, texte in pages:
        for j, chunk in enumerate(_decouper(texte)):
            docs.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "type": "document",
                        "document_id": document_id,
                        "document_nom": document_nom,
                        "source": fichier,
                        "page": numero_page,
                        "chunk": j,
                    },
                )
            )
    return docs


def ingerer_pdf(
    pdf_bytes: bytes, *, document_id: int, document_nom: str, fichier: str
) -> tuple[int, list[str]]:
    """Extrait, découpe et indexe un PDF. Renvoie (nb_pages, ids_vecteurs)."""
    pages = extraire_pages(pdf_bytes)
    if not pages:
        raise AppError("Aucun texte extractible (PDF scanné ? OCR requis).")
    docs = construire_documents(
        pages, document_id=document_id, document_nom=document_nom, fichier=fichier
    )
    ids = add_document_chunks(docs)
    logger.info("document_indexe", document=document_nom, pages=len(pages), chunks=len(ids))
    return len(pages), ids
