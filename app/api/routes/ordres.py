"""Ordres de fabrication : faisabilité, création (avec consommation FEFO), suivi."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access import require_category
from app.core.exceptions import AppError, FabricationError
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import Machine, OrdreFabrication
from app.schemas.manufacturing import (
    BesoinRead,
    ContexteLigneRead,
    FaisabiliteRead,
    OccupationMachineRead,
    OFConsommationRead,
    OFCreate,
    OFEnFileRead,
    OFLancerRequest,
    OFLigneUpdate,
    OFMiseEnFileRequest,
    OFRead,
    OFStatutUpdate,
)
from app.services import broadcast_service, line_queue_service, line_scoring_service
from app.services import manufacturing as svc
from app.services.line_queue_service import DispositionPreemption

router = APIRouter(
    prefix="/ordres-fabrication",
    tags=["ordres-fabrication"],
    dependencies=[
        Depends(require_api_key),
        Depends(require_category("Fabrication", "Actions machine")),
    ],
)


def _of_read(of: OrdreFabrication) -> OFRead:
    consommations = [
        OFConsommationRead(
            matiere_premiere_id=c.matiere_premiere_id,
            code_mp=c.matiere_premiere.code if c.matiere_premiere else str(c.matiere_premiere_id),
            lot_id=c.lot_matiere_premiere_id,
            numero_lot=c.lot.numero_lot if c.lot else "",
            quantite_consommee=c.quantite_consommee,
        )
        for c in of.consommations
    ]
    return OFRead(
        id=of.id,
        numero=of.numero,
        article_id=of.article_id,
        code_article=of.article.code,
        designation_article=of.article.designation,
        quantite_planifiee=of.quantite_planifiee,
        unite=of.unite,
        statut=of.statut,
        numero_lot_produit=of.numero_lot_produit,
        date_echeance=of.date_echeance,
        date_debut_prevue=of.date_debut_prevue,
        date_fin_prevue=of.date_fin_prevue,
        ligne_production_id=of.ligne_production_id,
        date_creation=of.date_creation,
        date_debut_reelle=of.date_debut_reelle,
        date_fin_reelle=of.date_fin_reelle,
        quantite_bonne=of.quantite_bonne,
        quantite_rejetee=of.quantite_rejetee,
        consommations=consommations,
    )


def _get_or_404(db: Session, of_id: int) -> OrdreFabrication:
    of = db.get(OrdreFabrication, of_id)
    if of is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OF introuvable")
    return of


@router.get("/faisabilite", response_model=FaisabiliteRead)
def faisabilite(
    article_id: int = Query(...),
    quantite: float = Query(..., gt=0),
    db: Session = Depends(get_db),
) -> FaisabiliteRead:
    """Calcule les besoins MP et le verdict de faisabilité (lecture seule)."""
    try:
        f = svc.verifier_faisabilite(db, article_id, quantite)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    return FaisabiliteRead(
        article_id=f.article_id,
        quantite=f.quantite,
        faisable=f.faisable,
        besoins=[
            BesoinRead(
                matiere_premiere_id=b.matiere_premiere_id,
                code=b.code,
                designation=b.designation,
                unite=b.unite,
                quantite_requise=b.quantite_requise,
                quantite_disponible=b.quantite_disponible,
                manquant=b.manquant,
                suffisant=b.suffisant,
            )
            for b in f.besoins
        ],
    )


@router.get("", response_model=list[OFRead])
def lister(db: Session = Depends(get_db)) -> list[OFRead]:
    ofs = db.execute(
        select(OrdreFabrication).order_by(OrdreFabrication.id.desc())
    ).scalars()
    return [_of_read(of) for of in ofs]


@router.get("/{of_id}", response_model=OFRead)
def detail(of_id: int, db: Session = Depends(get_db)) -> OFRead:
    return _of_read(_get_or_404(db, of_id))


@router.post("", response_model=OFRead, status_code=status.HTTP_201_CREATED)
def creer(payload: OFCreate, db: Session = Depends(get_db)) -> OFRead:
    """Crée un OF : vérifie la faisabilité, décrémente les MP (FEFO), trace la généalogie."""
    try:
        of = svc.creer_ordre_fabrication(
            db,
            article_id=payload.article_id,
            quantite=payload.quantite,
            date_echeance=payload.date_echeance,
            ligne_production_id=payload.ligne_production_id,
            cree_par=payload.cree_par or "dashboard",
        )
    except AppError as exc:
        raise HTTPException(
            exc.status_code, exc.message if not exc.details else f"{exc.message} {exc.details}"
        )
    db.flush()
    db.refresh(of)
    return _of_read(of)


@router.patch("/{of_id}/statut", response_model=OFRead)
def changer_statut(
    of_id: int, payload: OFStatutUpdate, db: Session = Depends(get_db)
) -> OFRead:
    of = _get_or_404(db, of_id)
    of.statut = payload.statut
    db.flush()
    return _of_read(of)


@router.patch("/{of_id}/ligne", response_model=OFRead)
def affecter_ligne(
    of_id: int, payload: OFLigneUpdate, db: Session = Depends(get_db)
) -> OFRead:
    """Affecte (ou retire) la ligne de production d'un OF déjà créé.

    Un changement de ressource invalide le créneau calculé précédemment : les
    dates prévues sont donc effacées et devront être recalculées par
    l'ordonnanceur avec la nouvelle capacité.
    """
    of = _get_or_404(db, of_id)
    if of.ligne_production_id != payload.ligne_production_id:
        of.ligne_production_id = payload.ligne_production_id
        of.date_debut_prevue = None
        of.date_fin_prevue = None
    db.flush()
    return _of_read(of)


# --------------------------------------------------------------------------- #
# File d'attente de ligne : préemption et mise en file (lignes occupées)
# --------------------------------------------------------------------------- #


def _reste(of: OrdreFabrication) -> int:
    return max(0, int(of.quantite_planifiee - of.quantite_bonne - of.quantite_rejetee))


def _contexte_ligne(db: Session, ligne_id: int) -> ContexteLigneRead:
    occupations = line_queue_service.occupations_ligne(db, ligne_id)
    file = line_queue_service.file_attente(db, ligne_id)
    libre = line_scoring_service.machine_libre_sur_ligne(db, ligne_id)
    return ContexteLigneRead(
        ligne_production_id=ligne_id,
        machines_libres=1 if libre is not None else 0,
        occupations=[
            OccupationMachineRead(
                machine_id=occ.machine.id,
                machine_code=occ.machine.code,
                of_id=occ.ordre.id,
                of_numero=occ.ordre.numero,
                code_article=occ.ordre.article.code if occ.ordre.article else "",
                reste_a_produire=_reste(occ.ordre),
                date_echeance=occ.ordre.date_echeance,
            )
            for occ in occupations
        ],
        file_attente=[
            OFEnFileRead(
                id=of.id,
                numero=of.numero,
                code_article=of.article.code if of.article else "",
                quantite_planifiee=of.quantite_planifiee,
                reste_a_produire=_reste(of),
                date_echeance=of.date_echeance,
            )
            for of in file
        ],
    )


@router.get("/lignes/{ligne_id}/contexte", response_model=ContexteLigneRead)
def contexte_ligne(ligne_id: int, db: Session = Depends(get_db)) -> ContexteLigneRead:
    """État d'occupation d'une ligne : OF en cours, file d'attente (EDD), place libre.

    Sert à l'UI pour décider s'il faut proposer une préemption ou une mise en file.
    """
    return _contexte_ligne(db, ligne_id)


@router.post("/{of_id}/lancer", response_model=OFRead)
def lancer(
    of_id: int, payload: OFLancerRequest, db: Session = Depends(get_db)
) -> OFRead:
    """Lance un OF sur sa ligne, en préemptant un OF en cours si nécessaire.

    Sans machine libre et sans `preempt_disposition`, renvoie 409 : l'UI proposera
    alors préemption (requeue / pause / cancel) ou mise en file.
    """
    of = _get_or_404(db, of_id)
    disposition: DispositionPreemption | None = None
    if payload.preempt_disposition is not None:
        try:
            disposition = DispositionPreemption(payload.preempt_disposition)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Disposition inconnue : {payload.preempt_disposition}. "
                "Choix : requeue, pause, cancel.",
            )
    try:
        machine = line_queue_service.lancer_of_sur_ligne(
            db, of, preempt_disposition=disposition
        )
    except FabricationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message)
    except AppError as exc:
        raise HTTPException(exc.status_code, exc.message)
    db.flush()
    db.refresh(of)
    broadcast_service.diffuser_machine(db, machine)
    broadcast_service.diffuser(
        {"type": "ordres_update", "raison": "lancement", "numero": of.numero}
    )
    return _of_read(of)


@router.post("/{of_id}/mettre-en-file", response_model=OFRead)
def mettre_en_file(
    of_id: int, payload: OFMiseEnFileRequest, db: Session = Depends(get_db)
) -> OFRead:
    """Met l'OF en file d'attente sur une ligne (rattachement + PLANIFIE, sans démarrage)."""
    of = _get_or_404(db, of_id)
    try:
        line_queue_service.mettre_en_file(db, of, payload.ligne_production_id)
    except FabricationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message)
    db.flush()
    db.refresh(of)
    broadcast_service.diffuser(
        {"type": "ordres_update", "raison": "mise_en_file", "numero": of.numero}
    )
    return _of_read(of)
