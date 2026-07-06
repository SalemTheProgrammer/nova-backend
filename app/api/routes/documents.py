"""Base documentaire : upload PDF (indexation Pinecone), liste, suppression, recherche.

Accepte tout document d'entreprise : normes BPF/GMP, procédures qualité, manuels
machines, fiches techniques… Le PDF source est conservé pour la consultation
dans le viewer (citations surlignées).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import DocumentRag
from app.schemas.documents import DocumentPassage, DocumentRead, DocumentSearchResult
from app.services.citation_service import extraire_phrases_pertinentes
from app.services.document_ingest import ingerer_pdf
from app.services.vector_store import delete_document_vectors, search_documents

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_key)])

CITATION_MAX = 500


def _chemin_pdf(document_id: int) -> Path:
    """Chemin de stockage du PDF source d'un document (convention : {id}.pdf)."""
    return Path(get_settings().documents_pdf_dir) / f"{document_id}.pdf"


@router.get("", response_model=list[DocumentRead])
def lister(db: Session = Depends(get_db)) -> list[DocumentRag]:
    return list(db.execute(select(DocumentRag).order_by(DocumentRag.id.desc())).scalars())


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def uploader(
    nom: str = Form(...),
    categorie: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DocumentRag:
    """Téléverse un PDF, l'indexe dans Pinecone (chunks + page), et l'enregistre."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Seuls les fichiers PDF sont acceptés.")
    contenu = await file.read()
    if not contenu:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Fichier vide.")

    document = DocumentRag(
        nom=nom,
        fichier=file.filename or "document.pdf",
        categorie=(categorie or "").strip() or None,
        statut="EN_COURS",
    )
    db.add(document)
    db.flush()  # obtenir document.id pour les métadonnées des vecteurs

    try:
        nb_pages, ids = ingerer_pdf(
            contenu, document_id=document.id, document_nom=nom, fichier=document.fichier
        )
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)

    document.nb_pages = nb_pages
    document.nb_chunks = len(ids)
    document.vector_ids = ids
    document.statut = "INDEXEE"
    db.flush()

    # Conserve le PDF source : consultation dans le viewer + citations vérifiables.
    chemin = _chemin_pdf(document.id)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(contenu)
    return document


@router.get("/{document_id}/pdf")
def telecharger_pdf(document_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """Renvoie le PDF source d'un document (pour le viewer avec surlignage des citations)."""
    document = db.get(DocumentRag, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document introuvable")
    chemin = _chemin_pdf(document_id)
    if not chemin.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "PDF non disponible : ce document a été indexé avant le stockage des "
            "fichiers sources. Retéléversez-le pour activer la consultation.",
        )
    return FileResponse(chemin, media_type="application/pdf", filename=document.fichier)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer(document_id: int, db: Session = Depends(get_db)) -> None:
    document = db.get(DocumentRag, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document introuvable")
    try:
        delete_document_vectors(list(document.vector_ids or []))
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    _chemin_pdf(document_id).unlink(missing_ok=True)
    db.delete(document)


@router.get("/recherche", response_model=DocumentSearchResult)
def recherche(
    q: str = Query(..., min_length=2),
    top_k: int = Query(5, ge=1, le=10),
    _: Session = Depends(get_db),
) -> DocumentSearchResult:
    """Recherche sémantique dans la base documentaire, avec page et citation (pour tester le RAG)."""
    try:
        resultats = search_documents(q, top_k=top_k)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    extraits_par_passage = extraire_phrases_pertinentes(
        q, [doc.page_content for doc, _ in resultats]
    )
    passages = [
        DocumentPassage(
            document_id=doc.metadata.get("document_id"),
            document_nom=doc.metadata.get("document_nom", "—"),
            source=doc.metadata.get("source", "—"),
            page=doc.metadata.get("page"),
            score=round(float(score), 4),
            citation=doc.page_content[:CITATION_MAX],
            extraits=extraits_par_passage[i],
        )
        for i, (doc, score) in enumerate(resultats)
    ]
    return DocumentSearchResult(query=q, passages=passages)
