"""Documents normatifs : upload PDF (indexation Pinecone), liste, suppression, recherche."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Norme
from app.schemas.normes import NormePassage, NormeRead, NormeSearchResult
from app.services.normes_ingest import ingerer_pdf
from app.services.vector_store import delete_norme_vectors, search_normes

router = APIRouter(prefix="/normes", tags=["normes"], dependencies=[Depends(require_api_key)])

CITATION_MAX = 500


@router.get("", response_model=list[NormeRead])
def lister(db: Session = Depends(get_db)) -> list[Norme]:
    return list(db.execute(select(Norme).order_by(Norme.id.desc())).scalars())


@router.post("", response_model=NormeRead, status_code=status.HTTP_201_CREATED)
async def uploader(
    nom: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Norme:
    """Téléverse un PDF normatif, l'indexe dans Pinecone (chunks + page), et l'enregistre."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Seuls les fichiers PDF sont acceptés.")
    contenu = await file.read()
    if not contenu:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Fichier vide.")

    norme = Norme(nom=nom, fichier=file.filename or "document.pdf", statut="EN_COURS")
    db.add(norme)
    db.flush()  # obtenir norme.id pour les métadonnées des vecteurs

    try:
        nb_pages, ids = ingerer_pdf(
            contenu, norme_id=norme.id, norme_nom=nom, fichier=norme.fichier
        )
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)

    norme.nb_pages = nb_pages
    norme.nb_chunks = len(ids)
    norme.vector_ids = ids
    norme.statut = "INDEXEE"
    db.flush()
    return norme


@router.delete("/{norme_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer(norme_id: int, db: Session = Depends(get_db)) -> None:
    norme = db.get(Norme, norme_id)
    if norme is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Norme introuvable")
    try:
        delete_norme_vectors(list(norme.vector_ids or []))
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    db.delete(norme)


@router.get("/recherche", response_model=NormeSearchResult)
def recherche(
    q: str = Query(..., min_length=2),
    top_k: int = Query(5, ge=1, le=10),
    _: Session = Depends(get_db),
) -> NormeSearchResult:
    """Recherche sémantique dans les normes, avec page et citation (pour tester le RAG)."""
    try:
        resultats = search_normes(q, top_k=top_k)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    passages = [
        NormePassage(
            norme_id=doc.metadata.get("norme_id"),
            norme_nom=doc.metadata.get("norme_nom", "—"),
            source=doc.metadata.get("source", "—"),
            page=doc.metadata.get("page"),
            score=round(float(score), 4),
            citation=doc.page_content[:CITATION_MAX],
        )
        for doc, score in resultats
    ]
    return NormeSearchResult(query=q, passages=passages)
