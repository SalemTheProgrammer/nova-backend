"""Graphe de flux des lignes : articles supportés par ligne + liens ligne->ligne.

Alimente l'éditeur visuel (façon n8n) côté frontend. Partage le préfixe
`/lignes-production` avec le CRUD lignes, mais n'expose que des sous-chemins
dédiés (`/flux`, `/{id}/articles`, `/liens`) — aucun conflit de route.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Article, LigneLien, LigneProduction
from app.schemas.ligne_flux import (
    ArticleMini,
    ArticlesSet,
    FluxRead,
    LienCreate,
    LigneLienRead,
    LigneNode,
)

router = APIRouter(
    prefix="/lignes-production",
    tags=["lignes-flux"],
    dependencies=[Depends(require_api_key), Depends(require_category("Fabrication"))],
)


def _node(ligne: LigneProduction) -> LigneNode:
    return LigneNode(
        id=ligne.id,
        code=ligne.code,
        designation=ligne.designation,
        actif=ligne.actif,
        article_ids=[a.id for a in ligne.articles],
    )


def _get_ligne_or_404(db: Session, lid: int) -> LigneProduction:
    ligne = db.get(LigneProduction, lid)
    if ligne is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ligne de production introuvable")
    return ligne


@router.get("/flux", response_model=FluxRead)
def flux(db: Session = Depends(get_db)) -> FluxRead:
    """Tout le graphe : lignes (avec leurs articles), liens, et le catalogue d'articles."""
    lignes = list(db.execute(select(LigneProduction).order_by(LigneProduction.code)).scalars())
    liens = list(db.execute(select(LigneLien)).scalars())
    articles = list(
        db.execute(select(Article).where(Article.actif.is_(True)).order_by(Article.code)).scalars()
    )
    return FluxRead(
        lignes=[_node(l) for l in lignes],
        liens=[LigneLienRead.model_validate(x) for x in liens],
        articles=[ArticleMini.model_validate(a) for a in articles],
    )


@router.put("/{lid}/articles", response_model=LigneNode)
def definir_articles(
    lid: int, payload: ArticlesSet, db: Session = Depends(get_db)
) -> LigneNode:
    """Remplace la liste des articles que cette ligne sait produire."""
    ligne = _get_ligne_or_404(db, lid)
    if payload.article_ids:
        articles = list(
            db.execute(select(Article).where(Article.id.in_(payload.article_ids))).scalars()
        )
        trouves = {a.id for a in articles}
        manquants = set(payload.article_ids) - trouves
        if manquants:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Article(s) introuvable(s) : {sorted(manquants)}",
            )
        ligne.articles = articles
    else:
        ligne.articles = []
    db.flush()
    db.refresh(ligne)
    return _node(ligne)


@router.post("/liens", response_model=LigneLienRead, status_code=status.HTTP_201_CREATED)
def creer_lien(payload: LienCreate, db: Session = Depends(get_db)) -> LigneLien:
    """Crée un lien de flux : la sortie de `source` alimente `target`."""
    if payload.source_id == payload.target_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Une ligne ne peut pas s'alimenter elle-même."
        )
    _get_ligne_or_404(db, payload.source_id)
    _get_ligne_or_404(db, payload.target_id)
    existant = db.execute(
        select(LigneLien).where(
            LigneLien.source_id == payload.source_id,
            LigneLien.target_id == payload.target_id,
        )
    ).scalar_one_or_none()
    if existant is not None:
        return existant
    lien = LigneLien(source_id=payload.source_id, target_id=payload.target_id)
    db.add(lien)
    db.flush()
    db.refresh(lien)
    return lien


@router.delete("/liens/{lien_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_lien(lien_id: int, db: Session = Depends(get_db)) -> None:
    lien = db.get(LigneLien, lien_id)
    if lien is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lien introuvable")
    db.delete(lien)
    db.flush()
