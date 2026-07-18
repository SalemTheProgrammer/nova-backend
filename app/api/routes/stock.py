"""CRUD stock : lots de matière première, ajustement d'inventaire, journal des mouvements."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.exceptions import AppError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import LotMatierePremiere, MatierePremiere
from app.models.enums import StatutLot
from app.schemas.manufacturing import (
    AjustementCreate,
    LotDetailRead,
    LotUpdate,
    MouvementRead,
    StockLotCreate,
)
from app.services import manufacturing as svc

router = APIRouter(
    prefix="/stock",
    tags=["stock"],
    dependencies=[Depends(require_api_key), Depends(require_category("Fabrication"))],
)


def _detail(lot: LotMatierePremiere) -> LotDetailRead:
    mp: MatierePremiere = lot.matiere_premiere
    return LotDetailRead(
        id=lot.id,
        numero_lot=lot.numero_lot,
        matiere_premiere_id=lot.matiere_premiere_id,
        fournisseur_id=lot.fournisseur_id,
        quantite_initiale=lot.quantite_initiale,
        quantite_restante=lot.quantite_restante,
        date_reception=lot.date_reception,
        date_peremption=lot.date_peremption,
        statut=lot.statut,
        code_mp=mp.code,
        designation_mp=mp.designation,
        unite=mp.unite,
    )


# --------------------------- Lots --------------------------- #
@router.get("", response_model=list[LotDetailRead])
def lister(
    matiere_premiere_id: int | None = Query(None),
    statut: StatutLot | None = Query(None),
    db: Session = Depends(get_db),
) -> list[LotDetailRead]:
    lots = svc.lister_lots(db, matiere_premiere_id=matiere_premiere_id, statut=statut)
    return [_detail(lot) for lot in lots]


@router.post("", response_model=LotDetailRead, status_code=status.HTTP_201_CREATED)
def creer(payload: StockLotCreate, db: Session = Depends(get_db)) -> LotDetailRead:
    try:
        lot = svc.receptionner_mp(
            db,
            matiere_premiere_id=payload.matiere_premiere_id,
            numero_lot=payload.numero_lot,
            quantite=payload.quantite,
            date_reception=payload.date_reception,
            date_peremption=payload.date_peremption,
            fournisseur_id=payload.fournisseur_id,
        )
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    db.flush()
    return _detail(lot)


# --------------------------- Mouvements (avant /{lot_id}) --------------------------- #
@router.get("/mouvements", response_model=list[MouvementRead])
def journal(
    matiere_premiere_id: int | None = Query(None),
    lot_id: int | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[MouvementRead]:
    return svc.lister_mouvements(
        db, matiere_premiere_id=matiere_premiere_id, lot_id=lot_id, limit=limit
    )


@router.get("/{lot_id}", response_model=LotDetailRead)
def detail(lot_id: int, db: Session = Depends(get_db)) -> LotDetailRead:
    try:
        return _detail(svc.get_lot(db, lot_id))
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)


@router.patch("/{lot_id}", response_model=LotDetailRead)
def modifier(lot_id: int, payload: LotUpdate, db: Session = Depends(get_db)) -> LotDetailRead:
    try:
        lot = svc.get_lot(db, lot_id)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(lot, key, value)
    db.flush()
    return _detail(lot)


@router.post("/{lot_id}/ajustement", response_model=LotDetailRead)
def ajuster(
    lot_id: int, payload: AjustementCreate, db: Session = Depends(get_db)
) -> LotDetailRead:
    try:
        lot = svc.ajuster_stock(
            db,
            lot_id=lot_id,
            quantite_restante=payload.quantite_restante,
            commentaire=payload.commentaire,
        )
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    return _detail(lot)


@router.delete("/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer(lot_id: int, db: Session = Depends(get_db)) -> None:
    try:
        svc.supprimer_lot(db, lot_id)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
