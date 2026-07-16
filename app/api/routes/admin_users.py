"""Administration des utilisateurs : réservée à l'administrateur.

Liste les numéros + nom complet, permet de créer/modifier/supprimer un numéro et
de cocher, par catégorie, les outils autorisés (voir `TOOL_CATALOG`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools import ALL_TOOL_NAMES, TOOL_CATALOG
from app.core.exceptions import AppError, NotFoundError
from app.core.security import require_admin
from app.db.session import get_db
from app.models.utilisateur import Utilisateur
from app.schemas.auth_schema import (
    OutilCatalogue,
    UtilisateurCreate,
    UtilisateurRead,
    UtilisateurUpdate,
)
from app.services import auth_service

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class AdminError(AppError):
    status_code = 400
    code = "admin_error"


def _filtrer_outils(noms: list[str]) -> list[str]:
    """Ne conserve que des noms d'outils réels (ignore l'inconnu), sans doublon."""
    vus: dict[str, None] = {}
    for nom in noms:
        if nom in ALL_TOOL_NAMES and nom not in vus:
            vus[nom] = None
    return list(vus)


@router.get("/tools", response_model=list[OutilCatalogue])
def list_tools() -> list[OutilCatalogue]:
    """Catalogue des outils (nom, catégorie, description) pour les cases à cocher."""
    return [OutilCatalogue(**o) for o in TOOL_CATALOG]


@router.get("/users", response_model=list[UtilisateurRead])
def list_users(db: Session = Depends(get_db)) -> list[UtilisateurRead]:
    users = db.execute(select(Utilisateur).order_by(Utilisateur.id)).scalars().all()
    return [UtilisateurRead.model_validate(u) for u in users]


@router.post("/users", response_model=UtilisateurRead, status_code=201)
def create_user(payload: UtilisateurCreate, db: Session = Depends(get_db)) -> UtilisateurRead:
    telephone = auth_service.normaliser_numero(payload.telephone)
    existe = db.execute(
        select(Utilisateur).where(Utilisateur.telephone == telephone)
    ).scalar_one_or_none()
    if existe is not None:
        raise AdminError(f"Le numéro {telephone} existe déjà.")
    user = Utilisateur(
        telephone=telephone,
        nom_complet=payload.nom_complet.strip(),
        is_admin=False,
        actif=True,
        outils_autorises=_filtrer_outils(payload.outils_autorises),
    )
    db.add(user)
    db.flush()
    return UtilisateurRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UtilisateurRead)
def update_user(
    user_id: int, payload: UtilisateurUpdate, db: Session = Depends(get_db)
) -> UtilisateurRead:
    user = db.get(Utilisateur, user_id)
    if user is None:
        raise NotFoundError("Utilisateur introuvable.")
    if payload.nom_complet is not None:
        user.nom_complet = payload.nom_complet.strip()
    if payload.actif is not None:
        if user.is_admin and not payload.actif:
            raise AdminError("Impossible de désactiver l'administrateur.")
        user.actif = payload.actif
    if payload.outils_autorises is not None:
        user.outils_autorises = _filtrer_outils(payload.outils_autorises)
    db.flush()
    return UtilisateurRead.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)) -> None:
    user = db.get(Utilisateur, user_id)
    if user is None:
        raise NotFoundError("Utilisateur introuvable.")
    if user.is_admin:
        raise AdminError("Impossible de supprimer l'administrateur.")
    db.delete(user)
