"""Ordres de fabrication : faisabilité, création (avec consommation FEFO), suivi."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import OrdreFabrication
from app.schemas.manufacturing import (
    BesoinRead,
    FaisabiliteRead,
    OFConsommationRead,
    OFCreate,
    OFRead,
    OFStatutUpdate,
)
from app.services import manufacturing as svc

router = APIRouter(
    prefix="/ordres-fabrication",
    tags=["ordres-fabrication"],
    dependencies=[Depends(require_api_key)],
)


def _of_read(of: OrdreFabrication) -> OFRead:
    consommations = [
        OFConsommationRead(
            matiere_premiere_id=c.matiere_premiere_id,
            code_mp=c.matiere_premiere.code if c.matiere_premiere else str(c.matiere_premiere_id),
            lot_id=c.lot_matiere_premiere_id,
            numero_lot=c.lot.numero_lot if c.lot else "",
            quantite_consommee=c.quantite_consommee,
        )
        for c in of.consommations
    ]
    return OFRead(
        id=of.id,
        numero=of.numero,
        article_id=of.article_id,
        code_article=of.article.code,
        designation_article=of.article.designation,
        quantite_planifiee=of.quantite_planifiee,
        unite=of.unite,
        statut=of.statut,
        numero_lot_produit=of.numero_lot_produit,
        date_fin_prevue=of.date_fin_prevue,
        ligne_production_id=of.ligne_production_id,
        date_creation=of.date_creation,
        date_debut_reelle=of.date_debut_reelle,
        date_fin_reelle=of.date_fin_reelle,
        quantite_bonne=of.quantite_bonne,
        quantite_rejetee=of.quantite_rejetee,
        consommations=consommations,
    )


def _get_or_404(db: Session, of_id: int) -> OrdreFabrication:
    of = db.get(OrdreFabrication, of_id)
    if of is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OF introuvable")
    return of


@router.get("/faisabilite", response_model=FaisabiliteRead)
def faisabilite(
    article_id: int = Query(...),
    quantite: float = Query(..., gt=0),
    db: Session = Depends(get_db),
) -> FaisabiliteRead:
    """Calcule les besoins MP et le verdict de faisabilité (lecture seule)."""
    try:
        f = svc.verifier_faisabilite(db, article_id, quantite)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    return FaisabiliteRead(
        article_id=f.article_id,
        quantite=f.quantite,
        faisable=f.faisable,
        besoins=[
            BesoinRead(
                matiere_premiere_id=b.matiere_premiere_id,
                code=b.code,
                designation=b.designation,
                unite=b.unite,
                quantite_requise=b.quantite_requise,
                quantite_disponible=b.quantite_disponible,
                manquant=b.manquant,
                suffisant=b.suffisant,
            )
            for b in f.besoins
        ],
    )


@router.get("", response_model=list[OFRead])
def lister(db: Session = Depends(get_db)) -> list[OFRead]:
    ofs = db.execute(
        select(OrdreFabrication).order_by(OrdreFabrication.id.desc())
    ).scalars()
    return [_of_read(of) for of in ofs]


@router.get("/{of_id}", response_model=OFRead)
def detail(of_id: int, db: Session = Depends(get_db)) -> OFRead:
    return _of_read(_get_or_404(db, of_id))


@router.post("", response_model=OFRead, status_code=status.HTTP_201_CREATED)
def creer(payload: OFCreate, db: Session = Depends(get_db)) -> OFRead:
    """Crée un OF : vérifie la faisabilité, décrémente les MP (FEFO), trace la généalogie."""
    try:
        of = svc.creer_ordre_fabrication(
            db,
            article_id=payload.article_id,
            quantite=payload.quantite,
            date_fin_prevue=payload.date_fin_prevue,
            ligne_production_id=payload.ligne_production_id,
            cree_par=payload.cree_par or "dashboard",
        )
    except AppError as exc:
        raise HTTPException(
            exc.status_code, exc.message if not exc.details else f"{exc.message} {exc.details}"
        )
    db.flush()
    db.refresh(of)
    return _of_read(of)


@router.patch("/{of_id}/statut", response_model=OFRead)
def changer_statut(
    of_id: int, payload: OFStatutUpdate, db: Session = Depends(get_db)
) -> OFRead:
    of = _get_or_404(db, of_id)
    of.statut = payload.statut
    db.flush()
    return _of_read(of)
