"""Service métier de prélèvement d'échantillons et libération de lots (BPF / DPM)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import FabricationError, NotFoundError
from app.core.logging import get_logger
from app.models import LotMatierePremiere, MouvementStock, OFConsommationMP, OrdreFabrication
from app.models.enums import StatutLot, TypeMouvement, Unite
from app.models.prelevement import PrelevementMP, StatutPrelevement
from app.services import broadcast_service

logger = get_logger(__name__)


def generer_numero_prelevement(db: Session) -> str:
    """Génère le prochain numéro séquentiel PRL-YYYY-XXXX."""
    annee = datetime.utcnow().year
    prefixe = f"PRL-{annee}-"
    dernier = db.execute(
        select(PrelevementMP.numero)
        .where(PrelevementMP.numero.like(f"{prefixe}%"))
        .order_by(PrelevementMP.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if not dernier:
        return f"{prefixe}0001"

    try:
        seq = int(dernier.split("-")[-1]) + 1
        return f"{prefixe}{seq:04d}"
    except (ValueError, IndexError):
        return f"{prefixe}0001"


def creer_prelevement(
    db: Session,
    *,
    lot_matiere_premiere_id: int,
    quantite_prelevee: Decimal,
    unite: Unite = Unite.G,
    preleveur: str,
    zone_prelevement: str = "SAS Prélèvement MP - Flux Laminaire ISO 5",
    bloquer_lot: bool = True,
) -> PrelevementMP:
    """Enregistre un prélèvement d'échantillon pour analyse CQ."""
    lot = db.get(LotMatierePremiere, lot_matiere_premiere_id)
    if not lot:
        raise NotFoundError(f"Lot #{lot_matiere_premiere_id} introuvable")

    if quantite_prelevee <= Decimal("0"):
        raise FabricationError("La quantité prélevée doit être supérieure à zéro")

    numero = generer_numero_prelevement(db)
    prelevement = PrelevementMP(
        numero=numero,
        lot_matiere_premiere_id=lot.id,
        quantite_prelevee=quantite_prelevee,
        unite=unite,
        preleveur=preleveur,
        zone_prelevement=zone_prelevement,
        statut=StatutPrelevement.EN_ATTENTE_CQ,
    )
    db.add(prelevement)

    # Selon les BPF : un lot sous contrôle qualité passe en quarantaine (BLOQUE)
    if bloquer_lot and lot.statut == StatutLot.DISPONIBLE:
        lot.statut = StatutLot.BLOQUE

    db.flush()
    db.refresh(prelevement)

    logger.info("prelevement_cree", numero=numero, lot=lot.numero_lot)
    broadcast_service.diffuser(
        {
            "type": "prelevement_cree",
            "prelevement": {
                "id": prelevement.id,
                "numero": prelevement.numero,
                "lot_numero": lot.numero_lot,
                "statut": prelevement.statut.value,
            },
        }
    )

    return prelevement


def valider_analyse_cq(
    db: Session,
    *,
    prelevement_id: int,
    conforme: bool,
    analyste: str,
    bulletin_analyse_ref: str | None = None,
    commentaire: str | None = None,
) -> PrelevementMP:
    """Valide l'analyse CQ du prélèvement. Si conforme, le lot MP est libéré (DISPONIBLE)."""
    prelevement = db.get(PrelevementMP, prelevement_id)
    if not prelevement:
        raise NotFoundError(f"Prélèvement #{prelevement_id} introuvable")

    prelevement.statut = StatutPrelevement.CONFORME if conforme else StatutPrelevement.NON_CONFORME
    prelevement.date_analyse = datetime.utcnow()
    prelevement.analyste = analyste
    prelevement.bulletin_analyse_ref = bulletin_analyse_ref
    prelevement.commentaire = commentaire

    lot = prelevement.lot
    if conforme:
        # Libération du lot pour la fabrication
        lot.statut = StatutLot.DISPONIBLE
        logger.info("lot_libere_par_cq", lot=lot.numero_lot, bulletin=bulletin_analyse_ref)
    else:
        # Rejet du lot
        lot.statut = StatutLot.BLOQUE
        logger.warning("lot_rejete_par_cq", lot=lot.numero_lot, motif=commentaire)

    db.flush()
    db.refresh(prelevement)

    broadcast_service.diffuser(
        {
            "type": "analyse_cq_validee",
            "conforme": conforme,
            "prelevement_numero": prelevement.numero,
            "lot_numero": lot.numero_lot,
            "statut_lot": lot.statut.value,
        }
    )

    return prelevement


def verifier_lot_dispensable(lot: LotMatierePremiere) -> None:
    """Vérifie la conformité BPF avant toute pesée ou incorporation."""
    if lot.statut != StatutLot.DISPONIBLE:
        raise FabricationError(
            f"Le lot {lot.numero_lot} est en statut {lot.statut.value} : "
            "interdiction stricte de l'incorporer avant libération par le contrôle qualité (BPF)."
        )
