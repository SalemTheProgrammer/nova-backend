"""CRUD matières premières + réception/consultation des lots + état du stock."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.exceptions import AppError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import LotMatierePremiere, MatierePremiere
from app.schemas.manufacturing import (
    LotCreate,
    LotRead,
    MatierePremiereCreate,
    MatierePremiereRead,
    MatierePremiereUpdate,
    StockMPRead,
)
from app.services import manufacturing as svc

router = APIRouter(
    prefix="/matieres-premieres",
    tags=["matieres-premieres"],
    dependencies=[Depends(require_api_key), Depends(require_category("Fabrication"))],
)


def _get_or_404(db: Session, mp_id: int) -> MatierePremiere:
    mp = db.get(MatierePremiere, mp_id)
    if mp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matière première introuvable")
    return mp


def _read(db: Session, mp: MatierePremiere) -> MatierePremiereRead:
    out = MatierePremiereRead.model_validate(mp)
    out.stock_disponible = svc.stock_disponible_mp(db, mp.id)
    return out


@router.get("", response_model=list[MatierePremiereRead])
def lister(db: Session = Depends(get_db)) -> list[MatierePremiereRead]:
    mps = db.execute(select(MatierePremiere).order_by(MatierePremiere.code)).scalars()
    return [_read(db, mp) for mp in mps]


@router.post("", response_model=MatierePremiereRead, status_code=status.HTTP_201_CREATED)
def creer(payload: MatierePremiereCreate, db: Session = Depends(get_db)) -> MatierePremiereRead:
    if db.execute(
        select(MatierePremiere).where(MatierePremiere.code == payload.code)
    ).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Code déjà utilisé : {payload.code}")
    mp = MatierePremiere(**payload.model_dump())
    db.add(mp)
    db.flush()
    return _read(db, mp)


@router.get("/{mp_id}", response_model=MatierePremiereRead)
def detail(mp_id: int, db: Session = Depends(get_db)) -> MatierePremiereRead:
    return _read(db, _get_or_404(db, mp_id))


@router.patch("/{mp_id}", response_model=MatierePremiereRead)
def modifier(
    mp_id: int, payload: MatierePremiereUpdate, db: Session = Depends(get_db)
) -> MatierePremiereRead:
    mp = _get_or_404(db, mp_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(mp, key, value)
    db.flush()
    return _read(db, mp)


@router.delete("/{mp_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer(mp_id: int, db: Session = Depends(get_db)) -> None:
    mp = _get_or_404(db, mp_id)
    db.delete(mp)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "MP référencée (lots/nomenclature) — désactivez-la au lieu de la supprimer.",
        )


# --------------------------- Lots --------------------------- #
@router.get("/{mp_id}/lots", response_model=list[LotRead])
def lister_lots(mp_id: int, db: Session = Depends(get_db)) -> list[LotMatierePremiere]:
    _get_or_404(db, mp_id)
    return list(
        db.execute(
            select(LotMatierePremiere)
            .where(LotMatierePremiere.matiere_premiere_id == mp_id)
            .order_by(LotMatierePremiere.date_peremption.asc().nullslast())
        ).scalars()
    )


@router.post("/{mp_id}/lots", response_model=LotRead, status_code=status.HTTP_201_CREATED)
def receptionner_lot(
    mp_id: int, payload: LotCreate, db: Session = Depends(get_db)
) -> LotMatierePremiere:
    _get_or_404(db, mp_id)
    try:
        lot = svc.receptionner_mp(
            db,
            matiere_premiere_id=mp_id,
            numero_lot=payload.numero_lot,
            quantite=payload.quantite,
            date_reception=payload.date_reception,
            date_peremption=payload.date_peremption,
            fournisseur_id=payload.fournisseur_id,
        )
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    db.flush()
    return lot


# --------------------------- État du stock --------------------------- #
@router.get("/stock/etat", response_model=list[StockMPRead])
def etat_stock(db: Session = Depends(get_db)) -> list[StockMPRead]:
    """Vue d'ensemble : disponible par MP, seuil, nombre de lots."""
    out: list[StockMPRead] = []
    for mp in db.execute(select(MatierePremiere).order_by(MatierePremiere.code)).scalars():
        dispo = svc.stock_disponible_mp(db, mp.id)
        nb_lots = len(
            db.execute(
                select(LotMatierePremiere.id).where(
                    LotMatierePremiere.matiere_premiere_id == mp.id
                )
            ).all()
        )
        out.append(
            StockMPRead(
                matiere_premiere_id=mp.id,
                code=mp.code,
                designation=mp.designation,
                unite=mp.unite,
                disponible=dispo,
                seuil_alerte=mp.seuil_alerte,
                sous_seuil=mp.seuil_alerte is not None and dispo < mp.seuil_alerte,
                nb_lots=nb_lots,
            )
        )
    return out
