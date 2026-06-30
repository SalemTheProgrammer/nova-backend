"""Numérotation normalisée des OF et des lots produits."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import OrdreFabrication


def generer_numero_of(db: Session, *, annee: int | None = None) -> str:
    """Génère un numéro d'OF au format OF-YYYY-##### (séquence par année)."""
    annee = annee or date.today().year
    prefix = f"OF-{annee}-"
    count = db.execute(
        select(func.count())
        .select_from(OrdreFabrication)
        .where(OrdreFabrication.numero.like(f"{prefix}%"))
    ).scalar_one()
    return f"{prefix}{count + 1:05d}"


def generer_numero_lot_produit(
    db: Session, *, code_article: str, jour: date | None = None
) -> str:
    """Génère un n° de lot produit: {code_article}-YYYYMMDD-NN (séquence par jour)."""
    jour = jour or date.today()
    prefix = f"{code_article}-{jour:%Y%m%d}-"
    count = db.execute(
        select(func.count())
        .select_from(OrdreFabrication)
        .where(OrdreFabrication.numero_lot_produit.like(f"{prefix}%"))
    ).scalar_one()
    return f"{prefix}{count + 1:02d}"
