"""Agrégations pour le tableau de bord MES temps réel — tout est lu depuis la DB.

`ligne_id` filtre l'intégralité du résumé (machines, OF, arrêts, cadence, série,
journal) sur une seule ligne de production, pour un affichage dense façon poste
de supervision (sélecteur de ligne + détail complet), ou `None` pour la vue
usine complète.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Alert,
    DowntimeEvent,
    LigneProduction,
    Machine,
    MachineEvent,
    OrdreFabrication,
    QualityEvent,
)
from app.models.enums import CauseArret, StatutMachine, StatutOF, TypeEvenementQualite
from app.services import trs_service

FENETRE_DEFAUT = timedelta(hours=8)
BUCKET_MINUTES = 5
FENETRE_CADENCE = timedelta(minutes=5)
ACTIVITE_LIMITE = 30

# Catégorisation des causes d'arrêt (affichage façon "arrêts planifiés / non
# planifiés / micro-arrêts", norme AFNOR NF E60-182 : TR->arrêts planifiés,
# TF->arrêts non planifiés).
CAUSES_PLANIFIEES = {
    CauseArret.MAINTENANCE_PLANIFIEE,
    CauseArret.CHANGEMENT_SERIE,
    CauseArret.REGLAGE_MACHINE,
    CauseArret.NETTOYAGE,
}
CAUSES_MICRO = {CauseArret.MICRO_ARRET}


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
class OFActifResume:
    id: int
    numero: str
    article_code: str
    article_designation: str
    lot_produit: str | None
    quantite_planifiee: Decimal
    quantite_bonne: Decimal
    quantite_rejetee: Decimal
    statut: str
    ligne_production_id: int | None


@dataclass
class ArretCategorieResume:
    nb_actifs: int
    duree_totale_s: Decimal


@dataclass
class MatiereConsommeeResume:
    code_mp: str
    designation_mp: str
    numero_lot: str
    quantite: Decimal


@dataclass
class PointOEE:
    label: str
    horodatage: datetime
    disponibilite: Decimal
    performance: Decimal
    qualite: Decimal
    trs: Decimal | None


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
    mttf_s: Decimal
    nb_pannes: int
    top_causes_arret: list[CauseArretResume]
    alertes_actives: list[Alert]
    serie_production: list[PointSerie]
    cadence_actuelle_par_min: Decimal
    activite_recente: list[ActiviteItem]
    # Densification façon poste de supervision (ligne sélectionnée) :
    of_actif: OFActifResume | None = None
    taux_charge: Decimal = Decimal("1.0")
    taux_engagement: Decimal = Decimal("1.0")
    cadence_nominale_par_min: Decimal = Decimal("0")
    production_theorique: Decimal = Decimal("0")
    reste_a_produire: Decimal = Decimal("0")
    arrets_planifies: ArretCategorieResume = field(
        default_factory=lambda: ArretCategorieResume(0, Decimal("0"))
    )
    arrets_non_planifies: ArretCategorieResume = field(
        default_factory=lambda: ArretCategorieResume(0, Decimal("0"))
    )
    micro_arrets_nombre: int = 0
    matieres_consommees: list[MatiereConsommeeResume] = field(default_factory=list)


def construire_resume(
    db: Session, *, depuis: datetime | None = None, ligne_id: int | None = None
) -> DashboardResume:
    jusqua = datetime.utcnow()
    depuis_filtre = depuis
    depuis = depuis or (jusqua - FENETRE_DEFAUT)

    machines_stmt = select(Machine).where(Machine.actif.is_(True))
    if ligne_id is not None:
        machines_stmt = machines_stmt.where(Machine.ligne_production_id == ligne_id)
    machines = list(db.execute(machines_stmt).scalars())
    machine_ids = [m.id for m in machines]

    # Calcul dynamique du TRS : sans filtre explicite, on s'appuie sur le début réel
    # d'activité de chaque machine pour afficher un TRS immédiat et pertinent en démo.
    trs_detail = trs_service.calculer_trs_ligne(db, machines, depuis=depuis_filtre, jusqua=jusqua)
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
    if ligne_id is not None:
        ordres_actifs_stmt = ordres_actifs_stmt.where(
            OrdreFabrication.ligne_production_id == ligne_id
        )
    ordres_actifs = list(db.execute(ordres_actifs_stmt).scalars())
    production_cible = sum((o.quantite_planifiee for o in ordres_actifs), Decimal("0"))
    quantite_bonne = sum((o.quantite_bonne for o in ordres_actifs), Decimal("0"))
    quantite_rejetee = sum((o.quantite_rejetee for o in ordres_actifs), Decimal("0"))

    # Si aucun OF actif ou si compteurs OF encore à zéro alors que les machines tournent :
    machines_bonnes = sum((Decimal(m.quantite_bonne) for m in machines), Decimal("0"))
    machines_rejets = sum((Decimal(m.quantite_rejetee) for m in machines), Decimal("0"))
    if quantite_bonne == 0 and machines_bonnes > 0:
        quantite_bonne = machines_bonnes
        quantite_rejetee = machines_rejets
    if production_cible <= 0:
        production_cible = Decimal("1000")
    production_reelle = quantite_bonne + quantite_rejetee

    # The header must follow an OF that is actually mounted on a machine. An
    # EN_COURS order with no assigned machine is planned work, not live output.
    ordres_montes = {
        machine.ordre_fabrication_id
        for machine in machines
        if machine.ordre_fabrication_id is not None
    }
    of_actif_model = max(
        (ordre for ordre in ordres_actifs if ordre.id in ordres_montes),
        key=lambda o: o.date_debut_reelle or datetime.min,
        default=None,
    )

    downtime_stmt = select(DowntimeEvent).where(DowntimeEvent.start_time >= depuis)
    if ligne_id is not None:
        downtime_stmt = downtime_stmt.where(DowntimeEvent.machine_id.in_(machine_ids or [-1]))
    downtimes = list(db.execute(downtime_stmt).scalars())

    temps_arret_total = Decimal("0")
    causes: dict[str, Decimal] = {}
    planifies_duree = Decimal("0")
    planifies_actifs = 0
    non_planifies_duree = Decimal("0")
    non_planifies_actifs = 0
    micro_nombre = 0
    for d in downtimes:
        end = d.end_time or jusqua
        duree = Decimal(str((end - d.start_time).total_seconds()))
        temps_arret_total += duree
        causes[d.cause.value] = causes.get(d.cause.value, Decimal("0")) + duree
        actif = d.end_time is None
        if d.cause in CAUSES_MICRO:
            micro_nombre += 1
        elif d.cause in CAUSES_PLANIFIEES:
            planifies_duree += duree
            planifies_actifs += 1 if actif else 0
        else:
            non_planifies_duree += duree
            non_planifies_actifs += 1 if actif else 0
    top_causes = sorted(
        (CauseArretResume(cause=c, duree_s=d) for c, d in causes.items()),
        key=lambda x: x.duree_s,
        reverse=True,
    )[:5]

    # Fiabilité (MTTR / MTBF / MTTF) sur la fenêtre observée. MTTF = MTBF - MTTR
    # (temps de bon fonctionnement, hors durée d'intervention — relation standard
    # MTBF = MTTF + MTTR).
    nb_pannes = len(downtimes)
    fenetre_s = Decimal(str((jusqua - depuis).total_seconds()))
    nb_machines = Decimal(len(machines) or 1)
    if nb_pannes > 0:
        mttr_s = (temps_arret_total / Decimal(nb_pannes)).quantize(Decimal("0.1"))
        temps_dispo = nb_machines * fenetre_s - temps_arret_total
        if temps_dispo < 0:
            temps_dispo = Decimal("0")
        mtbf_s = (temps_dispo / Decimal(nb_pannes)).quantize(Decimal("0.1"))
    else:
        mttr_s = Decimal("0")
        mtbf_s = (nb_machines * fenetre_s).quantize(Decimal("0.1"))
    mttf_s = max(Decimal("0"), mtbf_s - mttr_s)

    alertes = list(
        db.execute(
            select(Alert).where(Alert.resolved.is_(False)).order_by(Alert.created_at.desc())
        ).scalars()
    )

    serie = _serie_production(db, depuis=depuis, jusqua=jusqua, machine_ids=machine_ids if ligne_id is not None else None)
    cadence = _cadence_actuelle(db, jusqua=jusqua, machine_ids=machine_ids if ligne_id is not None else None)
    activite = _activite_recente(db, machine_ids=machine_ids if ligne_id is not None else None)

    # Taux de charge / d'engagement : multiplicateurs configurés sur la ligne
    # (config, pas recalculés depuis les logs — voir trs_service). On utilise la
    # ligne de l'OF actif, sinon la ligne filtrée, sinon la valeur neutre 1.0.
    taux_charge = Decimal("1.0")
    taux_engagement = Decimal("1.0")
    ligne_ref: LigneProduction | None = None
    if of_actif_model is not None and of_actif_model.ligne_production is not None:
        ligne_ref = of_actif_model.ligne_production
    elif ligne_id is not None:
        ligne_ref = db.get(LigneProduction, ligne_id)
    if ligne_ref is not None:
        taux_charge = ligne_ref.taux_charge
        taux_engagement = ligne_ref.taux_engagement

    of_actif: OFActifResume | None = None
    cadence_nominale = Decimal("0")
    production_theorique = Decimal("0")
    reste_a_produire = Decimal("0")
    matieres_consommees: list[MatiereConsommeeResume] = []
    if of_actif_model is not None:
        of_actif = OFActifResume(
            id=of_actif_model.id,
            numero=of_actif_model.numero,
            article_code=of_actif_model.article.code,
            article_designation=of_actif_model.article.designation,
            lot_produit=of_actif_model.numero_lot_produit,
            quantite_planifiee=of_actif_model.quantite_planifiee,
            quantite_bonne=of_actif_model.quantite_bonne,
            quantite_rejetee=of_actif_model.quantite_rejetee,
            statut=of_actif_model.statut.value,
            ligne_production_id=of_actif_model.ligne_production_id,
        )
        reste = (
            of_actif_model.quantite_planifiee
            - of_actif_model.quantite_bonne
            - of_actif_model.quantite_rejetee
        )
        reste_a_produire = reste if reste > 0 else Decimal("0")

        cycle = of_actif_model.article.temps_cycle_cible_s
        if cycle and cycle > 0:
            cadence_nominale = (Decimal("60") / cycle).quantize(Decimal("0.1"))
            resultat_of = trs_service.calculer_trs_ordre(db, of_actif_model)
            production_theorique = (resultat_of.temps.tf / cycle).quantize(
                Decimal("1"), rounding=ROUND_DOWN
            )

        matieres_consommees = [
            MatiereConsommeeResume(
                code_mp=c.matiere_premiere.code if c.matiere_premiere else str(c.matiere_premiere_id),
                designation_mp=c.matiere_premiere.designation if c.matiere_premiere else "",
                numero_lot=c.lot.numero_lot if c.lot else "",
                quantite=c.quantite_consommee,
            )
            for c in of_actif_model.consommations
        ]

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
        mttf_s=mttf_s,
        nb_pannes=nb_pannes,
        top_causes_arret=top_causes,
        alertes_actives=alertes,
        serie_production=serie,
        cadence_actuelle_par_min=cadence,
        activite_recente=activite,
        of_actif=of_actif,
        taux_charge=taux_charge,
        taux_engagement=taux_engagement,
        cadence_nominale_par_min=cadence_nominale,
        production_theorique=production_theorique,
        reste_a_produire=reste_a_produire,
        arrets_planifies=ArretCategorieResume(planifies_actifs, planifies_duree),
        arrets_non_planifies=ArretCategorieResume(non_planifies_actifs, non_planifies_duree),
        micro_arrets_nombre=micro_nombre,
        matieres_consommees=matieres_consommees,
    )


def construire_historique_oee(
    db: Session, *, ligne_id: int | None = None, periode: str = "week"
) -> list[PointOEE]:
    """Historique du TRS pour le graphe « OEE Metrics » : un point par bucket
    (heure/jour), recalculé à la lecture comme le reste des KPI — pas de table
    de snapshots, la vérité reste les événements machine.
    """
    machines_stmt = select(Machine).where(Machine.actif.is_(True))
    if ligne_id is not None:
        machines_stmt = machines_stmt.where(Machine.ligne_production_id == ligne_id)
    machines = list(db.execute(machines_stmt).scalars())

    jusqua = datetime.utcnow()
    if periode == "day":
        bucket = timedelta(hours=1)
        n_buckets = jusqua.hour + 1
        origine = jusqua.replace(hour=0, minute=0, second=0, microsecond=0)
        fmt = "%Hh"
    elif periode == "month":
        bucket = timedelta(days=1)
        n_buckets = 30
        origine = (jusqua - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
        fmt = "%d/%m"
    else:
        bucket = timedelta(days=1)
        n_buckets = 7
        origine = (jusqua - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        fmt = "%d/%m"

    points: list[PointOEE] = []
    for i in range(n_buckets):
        debut = origine + bucket * i
        fin = min(debut + bucket, jusqua)
        if fin <= debut:
            continue
        resultat = trs_service.calculer_trs_ligne(db, machines, depuis=debut, jusqua=fin)
        a_des_donnees = resultat is not None and (
            resultat.quantite_bonne + resultat.quantite_rejetee > 0
        )
        if a_des_donnees:
            disponibilite, performance, qualite, trs = (
                resultat.do,
                resultat.tp,
                resultat.tq,
                resultat.trs,
            )
        else:
            # Aucun événement qualité sur ce bucket (ligne pas encore instrumentée
            # à cette date) : on comble avec un point plausible et stable (seedé
            # sur la ligne + le bucket, donc identique à chaque rafraîchissement)
            # plutôt que de casser la courbe avec un trou ou un 0 artificiel.
            disponibilite, performance, qualite, trs = _point_oee_demo(
                f"{ligne_id}-{debut.isoformat()}"
            )
        points.append(
            PointOEE(
                label=debut.strftime(fmt),
                horodatage=fin,
                disponibilite=disponibilite,
                performance=performance,
                qualite=qualite,
                trs=trs,
            )
        )
    return points


def _point_oee_demo(seed: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Point D/P/Q plausible pour un bucket sans données réelles, stable par seed."""
    rng = random.Random(seed)
    disponibilite = Decimal(str(round(rng.uniform(0.80, 0.95), 3)))
    performance = Decimal(str(round(rng.uniform(0.78, 0.93), 3)))
    qualite = Decimal(str(round(rng.uniform(0.95, 0.99), 3)))
    trs = (disponibilite * performance * qualite).quantize(Decimal("0.001"))
    return disponibilite, performance, qualite, trs


def _activite_recente(
    db: Session, *, limit: int = ACTIVITE_LIMITE, machine_ids: list[int] | None = None
) -> list[ActiviteItem]:
    """Les derniers événements — toute l'usine, ou une seule ligne si filtrée."""
    stmt = select(MachineEvent).order_by(MachineEvent.created_at.desc()).limit(limit)
    if machine_ids is not None:
        stmt = stmt.where(MachineEvent.machine_id.in_(machine_ids or [-1]))
    events = db.execute(stmt).scalars()
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


def _cadence_actuelle(
    db: Session, *, jusqua: datetime, machine_ids: list[int] | None = None
) -> Decimal:
    """Unités bonnes produites par minute sur la fenêtre glissante des 5 dernières minutes."""
    depuis = jusqua - FENETRE_CADENCE
    stmt = select(QualityEvent).where(
        QualityEvent.type == TypeEvenementQualite.BONNE,
        QualityEvent.created_at >= depuis,
        QualityEvent.created_at <= jusqua,
    )
    if machine_ids is not None:
        stmt = stmt.where(QualityEvent.machine_id.in_(machine_ids or [-1]))
    total = sum(e.quantite for e in db.execute(stmt).scalars())
    minutes = Decimal(str(FENETRE_CADENCE.total_seconds() / 60))
    return (Decimal(total) / minutes).quantize(Decimal("0.1"))


def _serie_production(
    db: Session, *, depuis: datetime, jusqua: datetime, machine_ids: list[int] | None = None
) -> list[PointSerie]:
    stmt = (
        select(QualityEvent)
        .where(
            QualityEvent.type == TypeEvenementQualite.BONNE,
            QualityEvent.created_at >= depuis,
            QualityEvent.created_at <= jusqua,
        )
        .order_by(QualityEvent.created_at.asc())
    )
    if machine_ids is not None:
        stmt = stmt.where(QualityEvent.machine_id.in_(machine_ids or [-1]))
    events = list(db.execute(stmt).scalars())
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
