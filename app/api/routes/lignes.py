"""CRUD lignes de production."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import require_api_key
from app.db.session import get_db
from app.models import LigneProduction
from app.schemas.manufacturing import (
    LigneProductionCreate,
    LigneProductionRead,
    LigneProductionUpdate,
)

router = APIRouter(
    prefix="/lignes-production",
    tags=["lignes-production"],
    dependencies=[Depends(require_api_key)],
)


def _get_or_404(db: Session, lid: int) -> LigneProduction:
    ligne = db.get(LigneProduction, lid)
    if ligne is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ligne de production introuvable")
    return ligne


@router.get("", response_model=list[LigneProductionRead])
def lister(db: Session = Depends(get_db)) -> list[LigneProduction]:
    return list(db.execute(select(LigneProduction).order_by(LigneProduction.code)).scalars())


@router.post("", response_model=LigneProductionRead, status_code=status.HTTP_201_CREATED)
def creer(payload: LigneProductionCreate, db: Session = Depends(get_db)) -> LigneProduction:
    if db.execute(
        select(LigneProduction).where(LigneProduction.code == payload.code)
    ).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Code déjà utilisé : {payload.code}")
    ligne = LigneProduction(**payload.model_dump())
    db.add(ligne)
    db.flush()
    return ligne


@router.patch("/{lid}", response_model=LigneProductionRead)
def modifier(
    lid: int, payload: LigneProductionUpdate, db: Session = Depends(get_db)
) -> LigneProduction:
    ligne = _get_or_404(db, lid)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(ligne, key, value)
    db.flush()
    return ligne


@router.delete("/{lid}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer(lid: int, db: Session = Depends(get_db)) -> None:
    ligne = _get_or_404(db, lid)
    db.delete(ligne)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ligne référencée par un OF.")
