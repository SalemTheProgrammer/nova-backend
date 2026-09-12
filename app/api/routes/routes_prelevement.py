"""Routes API pour les prélèvements et la libération de lots MP (BPF/DPM Tunisie)."""
from __future__ import annotations

from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import AppError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models.enums import StatutLot
from app.models.prelevement import PrelevementMP
from app.models.stock import LotMatierePremiere
from app.schemas.prelevement_schema import (
    PrelevementCreateRequest,
    PrelevementRead,
    PrelevementValiderRequest,
)
from app.services import prelevement_service

router = APIRouter(
    prefix="/prelevements",
    tags=["prelevements"],
    dependencies=[Depends(require_api_key)],
)


def _to_read(p: PrelevementMP) -> PrelevementRead:
    lot = p.lot
    matiere = lot.matiere_premiere if lot else None
    return PrelevementRead(
        id=p.id,
        numero=p.numero,
        lot_matiere_premiere_id=p.lot_matiere_premiere_id,
        numero_lot=lot.numero_lot if lot else None,
        code_matiere=matiere.code if matiere else None,
        nom_matiere=matiere.designation if matiere else None,
        quantite_prelevee=p.quantite_prelevee,
        unite=p.unite,
        preleveur=p.preleveur,
        zone_prelevement=p.zone_prelevement,
        date_prelevement=p.date_prelevement,
        statut=p.statut,
        date_analyse=p.date_analyse,
        analyste=p.analyste,
        bulletin_analyse_ref=p.bulletin_analyse_ref,
        commentaire=p.commentaire,
    )


@router.get("", response_model=list[PrelevementRead])
def lister_prelevements(db: Session = Depends(get_db)) -> list[PrelevementRead]:
    """Liste tous les prélèvements d'échantillons MP pour analyse laboratoire."""
    prelevements = (
        db.execute(
            select(PrelevementMP)
            .options(joinedload(PrelevementMP.lot).joinedload(LotMatierePremiere.matiere_premiere))
            .order_by(PrelevementMP.id.desc())
        )
        .scalars()
        .all()
    )
    return [_to_read(p) for p in prelevements]


@router.get("/lots-en-quarantaine")
def lister_lots_en_quarantaine(db: Session = Depends(get_db)):
    """Retourne les lots de matières premières nécessitant un prélèvement ou validation CQ."""
    lots = (
        db.execute(
            select(LotMatierePremiere)
            .options(joinedload(LotMatierePremiere.matiere_premiere))
            .where(LotMatierePremiere.statut == StatutLot.BLOQUE)
            .order_by(LotMatierePremiere.id.desc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": l.id,
            "numero_lot": l.numero_lot,
            "code_matiere": l.matiere_premiere.code if l.matiere_premiere else "",
            "nom_matiere": l.matiere_premiere.designation if l.matiere_premiere else "",
            "quantite_actuelle": float(l.quantite_actuelle),
            "unite": l.matiere_premiere.unite.value if l.matiere_premiere else "kg",
            "statut": l.statut.value,
            "date_peremption": l.date_peremption.isoformat() if l.date_peremption else None,
        }
        for l in lots
    ]


@router.post("", response_model=PrelevementRead)
def creer_prelevement(
    payload: PrelevementCreateRequest,
    db: Session = Depends(get_db),
) -> PrelevementRead:
    """Enregistre un prélèvement au SAS flux laminaire et met le lot en quarantaine."""
    try:
        p = prelevement_service.creer_prelevement(
            db,
            lot_matiere_premiere_id=payload.lot_id,
            quantite_prelevee=payload.quantite_prelevee,
            unite=payload.unite,
            preleveur=payload.preleveur,
            zone_prelevement=payload.zone_prelevement,
        )
        return _to_read(p)
    except AppError as e:
        raise HTTPException(e.status_code, e.message)


@router.post("/{prelevement_id}/valider", response_model=PrelevementRead)
def valider_analyse(
    prelevement_id: int,
    payload: PrelevementValiderRequest,
    db: Session = Depends(get_db),
) -> PrelevementRead:
    """Valide les résultats d'analyses CQ et libère le lot en DISPONIBLE selon les BPF."""
    try:
        p = prelevement_service.valider_analyse_cq(
            db,
            prelevement_id=prelevement_id,
            conforme=payload.conforme,
            analyste=payload.analyste,
            bulletin_analyse_ref=payload.bulletin_analyse_ref,
            commentaire=payload.commentaire,
        )
        return _to_read(p)
    except AppError as e:
        raise HTTPException(e.status_code, e.message)
