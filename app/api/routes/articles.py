"""CRUD articles + gestion de la nomenclature (formule)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Article, MatierePremiere, Nomenclature, NomenclatureLigne
from app.schemas.manufacturing import (
    ArticleCreate,
    ArticleRead,
    ArticleUpdate,
    NomenclatureCreate,
    NomenclatureLigneRead,
    NomenclatureRead,
)

router = APIRouter(
    prefix="/articles",
    tags=["articles"],
    dependencies=[Depends(require_api_key), Depends(require_category("Fabrication"))],
)


def _get_or_404(db: Session, article_id: int) -> Article:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    return article


@router.get("", response_model=list[ArticleRead])
def lister(db: Session = Depends(get_db)) -> list[Article]:
    return list(db.execute(select(Article).order_by(Article.code)).scalars())


@router.post("", response_model=ArticleRead, status_code=status.HTTP_201_CREATED)
def creer(payload: ArticleCreate, db: Session = Depends(get_db)) -> Article:
    if db.execute(select(Article).where(Article.code == payload.code)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Code déjà utilisé : {payload.code}")
    article = Article(**payload.model_dump())
    db.add(article)
    db.flush()
    return article


@router.get("/{article_id}", response_model=ArticleRead)
def detail(article_id: int, db: Session = Depends(get_db)) -> Article:
    return _get_or_404(db, article_id)


@router.patch("/{article_id}", response_model=ArticleRead)
def modifier(
    article_id: int, payload: ArticleUpdate, db: Session = Depends(get_db)
) -> Article:
    article = _get_or_404(db, article_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(article, key, value)
    db.flush()
    return article


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer(article_id: int, db: Session = Depends(get_db)) -> None:
    article = _get_or_404(db, article_id)
    db.delete(article)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Article référencé par un OF — désactivez-le (actif=false) au lieu de le supprimer.",
        )


# --------------------------- Nomenclature --------------------------- #
def _nomenclature_read(nom: Nomenclature) -> NomenclatureRead:
    lignes = [
        NomenclatureLigneRead(
            id=l.id,
            matiere_premiere_id=l.matiere_premiere_id,
            code_mp=l.matiere_premiere.code,
            designation_mp=l.matiere_premiere.designation,
            unite=l.matiere_premiere.unite,
            quantite_par_unite=l.quantite_par_unite,
        )
        for l in nom.lignes
    ]
    return NomenclatureRead(
        id=nom.id, article_id=nom.article_id, version=nom.version, actif=nom.actif, lignes=lignes
    )


@router.get("/{article_id}/nomenclature", response_model=NomenclatureRead)
def get_nomenclature(article_id: int, db: Session = Depends(get_db)) -> NomenclatureRead:
    _get_or_404(db, article_id)
    nom = db.execute(
        select(Nomenclature)
        .where(Nomenclature.article_id == article_id, Nomenclature.actif.is_(True))
        .order_by(Nomenclature.version.desc())
    ).scalars().first()
    if nom is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aucune nomenclature active")
    return _nomenclature_read(nom)


@router.post(
    "/{article_id}/nomenclature",
    response_model=NomenclatureRead,
    status_code=status.HTTP_201_CREATED,
)
def creer_nomenclature(
    article_id: int, payload: NomenclatureCreate, db: Session = Depends(get_db)
) -> NomenclatureRead:
    """Crée une nouvelle version active de nomenclature ; désactive les anciennes."""
    _get_or_404(db, article_id)
    for mp_id in {ligne.matiere_premiere_id for ligne in payload.lignes}:
        if db.get(MatierePremiere, mp_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"MP introuvable : id={mp_id}")

    anciennes = db.execute(
        select(Nomenclature).where(Nomenclature.article_id == article_id)
    ).scalars().all()
    version = 1 + max((n.version for n in anciennes), default=0)
    for n in anciennes:
        n.actif = False

    nom = Nomenclature(article_id=article_id, version=version, actif=True)
    db.add(nom)
    db.flush()
    for ligne in payload.lignes:
        db.add(
            NomenclatureLigne(
                nomenclature_id=nom.id,
                matiere_premiere_id=ligne.matiere_premiere_id,
                quantite_par_unite=ligne.quantite_par_unite,
            )
        )
    db.flush()
    db.refresh(nom)
    return _nomenclature_read(nom)
