"""CRUD fournisseurs et interlocuteurs."""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Fournisseur, FournisseurContact
from app.schemas.manufacturing import (
    FournisseurContactCreate,
    FournisseurContactPage,
    FournisseurContactRead,
    FournisseurContactUpdate,
    FournisseurCreate,
    FournisseurRead,
    FournisseurUpdate,
)

router = APIRouter(
    prefix="/fournisseurs",
    tags=["fournisseurs"],
    dependencies=[Depends(require_api_key), Depends(require_category("Fabrication"))],
)


def _get_or_404(db: Session, fid: int) -> Fournisseur:
    f = db.get(Fournisseur, fid)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fournisseur introuvable")
    return f


def _get_contact_or_404(db: Session, cid: int) -> FournisseurContact:
    c = db.get(FournisseurContact, cid)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact introuvable")
    return c


def _to_contact_read(c: FournisseurContact) -> FournisseurContactRead:
    return FournisseurContactRead(
        id=c.id,
        fournisseur_id=c.fournisseur_id,
        nom=c.nom,
        role_level=c.role_level,
        poste=c.poste,
        telephone=c.telephone,
        email=c.email,
        fournisseur_nom=c.fournisseur.nom if c.fournisseur else None,
        fournisseur_code=c.fournisseur.code if c.fournisseur else None,
    )


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


# --------------------------- Contacts / Interlocuteurs --------------------------- #


@router.get("/contacts", response_model=FournisseurContactPage)
def lister_contacts(
    page: int = Query(default=1, ge=1, description="Numéro de page"),
    page_size: int = Query(default=10, ge=1, le=100, description="Taille de page"),
    role_level: str | None = Query(None, description="Filtre par niveau hiérarchique"),
    fournisseur_id: int | None = Query(None, description="Filtre par fournisseur"),
    search: str | None = Query(None, description="Recherche par nom, poste ou email"),
    db: Session = Depends(get_db),
) -> FournisseurContactPage:
    # 1. Compteurs globaux par niveau hiérarchique
    counts = {
        "tous": db.execute(select(func.count(FournisseurContact.id))).scalar_one() or 0,
        "directeur": db.execute(
            select(func.count(FournisseurContact.id)).where(FournisseurContact.role_level == "directeur")
        ).scalar_one() or 0,
        "sous_directeur": db.execute(
            select(func.count(FournisseurContact.id)).where(FournisseurContact.role_level == "sous_directeur")
        ).scalar_one() or 0,
        "chef": db.execute(
            select(func.count(FournisseurContact.id)).where(FournisseurContact.role_level == "chef")
        ).scalar_one() or 0,
        "employe": db.execute(
            select(func.count(FournisseurContact.id)).where(FournisseurContact.role_level == "employe")
        ).scalar_one() or 0,
    }

    # 2. Requête filtrée
    base_filter = select(FournisseurContact)
    count_filter = select(func.count(FournisseurContact.id))

    filters = []
    if role_level and role_level != "tous":
        filters.append(FournisseurContact.role_level == role_level)
    if fournisseur_id is not None:
        filters.append(FournisseurContact.fournisseur_id == fournisseur_id)
    if search:
        s = f"%{search.strip()}%"
        filters.append(
            or_(
                FournisseurContact.nom.ilike(s),
                FournisseurContact.poste.ilike(s),
                FournisseurContact.email.ilike(s),
                FournisseurContact.telephone.ilike(s),
            )
        )

    if filters:
        base_filter = base_filter.where(*filters)
        count_filter = count_filter.where(*filters)

    total = db.execute(count_filter).scalar_one() or 0
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1

    stmt = (
        base_filter
        .options(joinedload(FournisseurContact.fournisseur))
        .order_by(FournisseurContact.nom)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    contacts = list(db.execute(stmt).scalars())

    return FournisseurContactPage(
        items=[_to_contact_read(c) for c in contacts],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        counts=counts,
    )


@router.post("/contacts", response_model=FournisseurContactRead, status_code=status.HTTP_201_CREATED)
def creer_contact(
    payload: FournisseurContactCreate, db: Session = Depends(get_db)
) -> FournisseurContactRead:
    _get_or_404(db, payload.fournisseur_id)
    c = FournisseurContact(**payload.model_dump())
    db.add(c)
    db.flush()
    db.refresh(c)
    c = db.execute(
        select(FournisseurContact)
        .options(joinedload(FournisseurContact.fournisseur))
        .where(FournisseurContact.id == c.id)
    ).scalar_one()
    return _to_contact_read(c)


@router.patch("/contacts/{cid}", response_model=FournisseurContactRead)
def modifier_contact(
    cid: int, payload: FournisseurContactUpdate, db: Session = Depends(get_db)
) -> FournisseurContactRead:
    c = _get_contact_or_404(db, cid)
    data = payload.model_dump(exclude_unset=True)
    if "fournisseur_id" in data and data["fournisseur_id"] is not None:
        _get_or_404(db, data["fournisseur_id"])
    for key, value in data.items():
        setattr(c, key, value)
    db.flush()
    db.refresh(c)
    c = db.execute(
        select(FournisseurContact)
        .options(joinedload(FournisseurContact.fournisseur))
        .where(FournisseurContact.id == c.id)
    ).scalar_one()
    return _to_contact_read(c)


@router.delete("/contacts/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_contact(cid: int, db: Session = Depends(get_db)) -> None:
    c = _get_contact_or_404(db, cid)
    db.delete(c)
    db.flush()

