"""CRUD fournisseurs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Fournisseur
from app.schemas.manufacturing import (
    FournisseurCreate,
    FournisseurRead,
    FournisseurUpdate,
)

router = APIRouter(
    prefix="/fournisseurs", tags=["fournisseurs"], dependencies=[Depends(require_api_key)]
)


def _get_or_404(db: Session, fid: int) -> Fournisseur:
    f = db.get(Fournisseur, fid)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fournisseur introuvable")
    return f


@router.get("", response_model=list[FournisseurRead])
def lister(db: Session = Depends(get_db)) -> list[Fournisseur]:
    return list(db.execute(select(Fournisseur).order_by(Fournisseur.code)).scalars())


@router.post("", response_model=FournisseurRead, status_code=status.HTTP_201_CREATED)
def creer(payload: FournisseurCreate, db: Session = Depends(get_db)) -> Fournisseur:
    if db.execute(
        select(Fournisseur).where(Fournisseur.code == payload.code)
    ).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Code déjà utilisé : {payload.code}")
    f = Fournisseur(**payload.model_dump())
    db.add(f)
    db.flush()
    return f


@router.patch("/{fid}", response_model=FournisseurRead)
def modifier(fid: int, payload: FournisseurUpdate, db: Session = Depends(get_db)) -> Fournisseur:
    f = _get_or_404(db, fid)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(f, key, value)
    db.flush()
    return f


@router.delete("/{fid}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer(fid: int, db: Session = Depends(get_db)) -> None:
    f = _get_or_404(db, fid)
    db.delete(f)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Fournisseur référencé par des lots.")
