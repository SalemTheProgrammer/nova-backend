"""Agrégations pour le tableau de bord MES temps réel — tout est lu depuis la DB."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, DowntimeEvent, Machine, MachineEvent, OrdreFabrication, QualityEvent
from app.models.enums import StatutMachine, StatutOF, TypeEvenementQualite
from app.services import trs_service

FENETRE_DEFAUT = timedelta(hours=8)
BUCKET_MINUTES = 5
FENETRE_CADENCE = timedelta(minutes=5)
ACTIVITE_LIMITE = 30


@dataclass
class CauseArretResume:
    cause: str
    duree_s: Decimal


@dataclass
class PointSerie:
    horodatage: datetime
    quantite_bonne_cumulee: int


@dataclass
class ActiviteItem:
    id: int
    machine_id: int
    code_machine: str
    type: str
    payload: dict
    created_at: datetime


@dataclass
class DashboardResume:
    trs_global: Decimal
    disponibilite: Decimal
    performance: Decimal
    qualite: Decimal
    trs_detail: trs_service.TRSResult | None
    machines_en_marche: int
    machines_arretees: int
    machines_total: int
    ordres_actifs: int
    production_reelle: Decimal
    production_cible: Decimal
    quantite_bonne: Decimal
    quantite_rejetee: Decimal
    temps_arret_total_s: Decimal
    mttr_s: Decimal
    mtbf_s: Decimal
    nb_pannes: int
    top_causes_arret: list[CauseArretResume]
    alertes_actives: list[Alert]
    serie_production: list[PointSerie]
    cadence_actuelle_par_min: Decimal
    activite_recente: list[ActiviteItem]


def construire_resume(db: Session, *, depuis: datetime | None = None) -> DashboardResume:
    jusqua = datetime.utcnow()
    depuis = depuis or (jusqua - FENETRE_DEFAUT)

    machines = list(db.execute(select(Machine).where(Machine.actif.is_(True))).scalars())
    trs_detail = trs_service.calculer_trs_ligne(db, machines, depuis=depuis, jusqua=jusqua)
    trs_global = trs_detail.trs if trs_detail else Decimal("0")
    disponibilite = trs_detail.do if trs_detail else Decimal("0")
    performance = trs_detail.tp if trs_detail else Decimal("0")
    qualite = trs_detail.tq if trs_detail else Decimal("0")

    machines_en_marche = sum(1 for m in machines if m.statut == StatutMachine.MARCHE)
    machines_arretees = sum(
        1
        for m in machines
        if m.statut in (StatutMachine.ARRET, StatutMachine.PANNE, StatutMachine.MAINTENANCE)
    )

    ordres_actifs_stmt = select(OrdreFabrication).where(
        OrdreFabrication.statut == StatutOF.EN_COURS
    )
    ordres_actifs = list(db.execute(ordres_actifs_stmt).scalars())
    production_cible = sum((o.quantite_planifiee for o in ordres_actifs), Decimal("0"))
    quantite_bonne = sum((o.quantite_bonne for o in ordres_actifs), Decimal("0"))
    quantite_rejetee = sum((o.quantite_rejetee for o in ordres_actifs), Decimal("0"))
    production_reelle = quantite_bonne + quantite_rejetee

    downtimes = list(
        db.execute(
            select(DowntimeEvent).where(DowntimeEvent.start_time >= depuis)
        ).scalars()
    )
    temps_arret_total = Decimal("0")
    causes: dict[str, Decimal] = {}
    for d in downtimes:
        end = d.end_time or jusqua
        duree = Decimal(str((end - d.start_time).total_seconds()))
        temps_arret_total += duree
        causes[d.cause.value] = causes.get(d.cause.value, Decimal("0")) + duree
    top_causes = sorted(
        (CauseArretResume(cause=c, duree_s=d) for c, d in causes.items()),
        key=lambda x: x.duree_s,
        reverse=True,
    )[:5]

    # Fiabilité (MTTR / MTBF) sur la fenêtre observée.
    nb_pannes = len(downtimes)
    fenetre_s = Decimal(str((jusqua - depuis).total_seconds()))
    nb_machines = Decimal(len(machines) or 1)
    if nb_pannes > 0:
        # MTTR : durée moyenne d'une intervention (temps de réparation).
        mttr_s = (temps_arret_total / Decimal(nb_pannes)).quantize(Decimal("0.1"))
        # MTBF : temps de bon fonctionnement (machine-secondes dispo − arrêts) / nb pannes.
        temps_dispo = nb_machines * fenetre_s - temps_arret_total
        if temps_dispo < 0:
            temps_dispo = Decimal("0")
        mtbf_s = (temps_dispo / Decimal(nb_pannes)).quantize(Decimal("0.1"))
    else:
        mttr_s = Decimal("0")
        mtbf_s = (nb_machines * fenetre_s).quantize(Decimal("0.1"))

    alertes = list(
        db.execute(
            select(Alert).where(Alert.resolved.is_(False)).order_by(Alert.created_at.desc())
        ).scalars()
    )

    serie = _serie_production(db, depuis=depuis, jusqua=jusqua)
    cadence = _cadence_actuelle(db, jusqua=jusqua)
    activite = _activite_recente(db)

    return DashboardResume(
        trs_global=trs_global,
        disponibilite=disponibilite,
        performance=performance,
        qualite=qualite,
        trs_detail=trs_detail,
        machines_en_marche=machines_en_marche,
        machines_arretees=machines_arretees,
        machines_total=len(machines),
        ordres_actifs=len(ordres_actifs),
        production_reelle=production_reelle,
        production_cible=production_cible,
        quantite_bonne=quantite_bonne,
        quantite_rejetee=quantite_rejetee,
        temps_arret_total_s=temps_arret_total,
        mttr_s=mttr_s,
        mtbf_s=mtbf_s,
        nb_pannes=nb_pannes,
        top_causes_arret=top_causes,
        alertes_actives=alertes,
        serie_production=serie,
        cadence_actuelle_par_min=cadence,
        activite_recente=activite,
    )


def _activite_recente(db: Session, *, limit: int = ACTIVITE_LIMITE) -> list[ActiviteItem]:
    """Les derniers événements toutes machines confondues — le "tout ce qui se passe"."""
    events = db.execute(
        select(MachineEvent).order_by(MachineEvent.created_at.desc()).limit(limit)
    ).scalars()
    return [
        ActiviteItem(
            id=e.id,
            machine_id=e.machine_id,
            code_machine=e.machine.code,
            type=e.type.value,
            payload=e.payload,
            created_at=e.created_at,
        )
        for e in events
    ]


def _cadence_actuelle(db: Session, *, jusqua: datetime) -> Decimal:
    """Unités bonnes produites par minute sur la fenêtre glissante des 5 dernières minutes."""
    depuis = jusqua - FENETRE_CADENCE
    quantite = db.execute(
        select(QualityEvent).where(
            QualityEvent.type == TypeEvenementQualite.BONNE,
            QualityEvent.created_at >= depuis,
            QualityEvent.created_at <= jusqua,
        )
    ).scalars()
    total = sum(e.quantite for e in quantite)
    minutes = Decimal(str(FENETRE_CADENCE.total_seconds() / 60))
    return (Decimal(total) / minutes).quantize(Decimal("0.1"))


def _serie_production(db: Session, *, depuis: datetime, jusqua: datetime) -> list[PointSerie]:
    events = list(
        db.execute(
            select(QualityEvent)
            .where(
                QualityEvent.type == TypeEvenementQualite.BONNE,
                QualityEvent.created_at >= depuis,
                QualityEvent.created_at <= jusqua,
            )
            .order_by(QualityEvent.created_at.asc())
        ).scalars()
    )
    if not events:
        return []

    bucket = timedelta(minutes=BUCKET_MINUTES)
    points: list[PointSerie] = []
    cumul = 0
    bucket_start = depuis
    idx = 0
    while bucket_start <= jusqua:
        bucket_end = bucket_start + bucket
        while idx < len(events) and events[idx].created_at < bucket_end:
            cumul += events[idx].quantite
            idx += 1
        points.append(PointSerie(horodatage=bucket_end, quantite_bonne_cumulee=cumul))
        bucket_start = bucket_end
    return points
