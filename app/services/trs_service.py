"""Calcul du TRS / TRG / TRE (AFNOR NF E60-182), toujours recalculé à la lecture.

Modèle de temps emboîtés : TT ⊇ TO ⊇ TR ⊇ TF ⊇ TN ⊇ TU.
    TQ = TU/TN (qualité)   TP = TN/TF (performance)   DO = TF/TR (disponibilité)
    TRS = TQ × TP × DO
    TRG = TRS × taux_charge (TR/TO)
    TRE = TRG × taux_engagement (TO/TT)
`taux_charge`/`taux_engagement` sont des multiplicateurs configurables par ligne (pas de
calendrier d'équipes en v1) : à 1.0, TRS = TRG = TRE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DowntimeEvent, Machine, MachineEvent, OrdreFabrication, QualityEvent
from app.models.enums import StatutMachine, TypeEvenementMachine, TypeEvenementQualite

FENETRE_DEFAUT = timedelta(hours=8)

_ZERO = Decimal("0")


def _zero_result() -> TRSResult:
    """Résultat TRS neutre (aucune activité)."""
    return TRSResult(
        temps=TempsModel(tt=_ZERO, to=_ZERO, tr=_ZERO, tf=_ZERO, tn=_ZERO, tu=_ZERO),
        tq=_ZERO, tp=_ZERO, do=_ZERO,
        trs=_ZERO, trg=_ZERO, tre=_ZERO,
        pertes=Pertes(disponibilite_s=_ZERO, performance_s=_ZERO, qualite_s=_ZERO),
        quantite_bonne=0, quantite_rejetee=0,
    )


@dataclass
class TempsModel:
    tt: Decimal
    to: Decimal
    tr: Decimal
    tf: Decimal
    tn: Decimal
    tu: Decimal


@dataclass
class Pertes:
    disponibilite_s: Decimal
    performance_s: Decimal
    qualite_s: Decimal

    @property
    def principale(self) -> str:
        pertes = {
            "disponibilite": self.disponibilite_s,
            "performance": self.performance_s,
            "qualite": self.qualite_s,
        }
        return max(pertes, key=lambda k: pertes[k])


@dataclass
class TRSResult:
    temps: TempsModel
    tq: Decimal
    tp: Decimal
    do: Decimal
    trs: Decimal
    trg: Decimal
    tre: Decimal
    pertes: Pertes
    quantite_bonne: int
    quantite_rejetee: int


def _ratio(numerateur: Decimal, denominateur: Decimal) -> Decimal:
    if denominateur <= 0:
        return Decimal("0")
    return max(Decimal("0"), min(Decimal("1"), numerateur / denominateur))


def _duree_arrets(downtimes: list[DowntimeEvent], depuis: datetime, jusqua: datetime) -> Decimal:
    total = Decimal("0")
    for d in downtimes:
        start = max(d.start_time, depuis)
        end = min(d.end_time or jusqua, jusqua)
        if end > start:
            total += Decimal(str((end - start).total_seconds()))
    return total


def _calculer(
    *,
    depuis: datetime,
    jusqua: datetime,
    downtimes: list[DowntimeEvent],
    quality_events: list[QualityEvent],
    cycle_cible_s: Decimal,
    taux_charge: Decimal,
    taux_engagement: Decimal,
) -> TRSResult:
    if depuis >= jusqua:
        depuis = jusqua - timedelta(seconds=1)

    tt = Decimal(str((jusqua - depuis).total_seconds()))
    to = tt
    tr = to * taux_charge
    tf = max(Decimal("0"), tr - _duree_arrets(downtimes, depuis, jusqua))

    qte_bonne = sum((q.quantite for q in quality_events if q.type == TypeEvenementQualite.BONNE), 0)
    qte_rejetee = sum(
        (q.quantite for q in quality_events if q.type == TypeEvenementQualite.REBUT), 0
    )

    tn = Decimal(qte_bonne + qte_rejetee) * cycle_cible_s
    tu = Decimal(qte_bonne) * cycle_cible_s

    tq = _ratio(tu, tn)
    tp = _ratio(tn, tf)
    do = _ratio(tf, tr)
    trs = tq * tp * do
    trg = trs * taux_charge
    tre = trg * taux_engagement

    pertes = Pertes(
        disponibilite_s=max(Decimal("0"), tr - tf),
        performance_s=max(Decimal("0"), tf - tn),
        qualite_s=max(Decimal("0"), tn - tu),
    )

    return TRSResult(
        temps=TempsModel(tt=tt, to=to, tr=tr, tf=tf, tn=tn, tu=tu),
        tq=tq,
        tp=tp,
        do=do,
        trs=trs,
        trg=trg,
        tre=tre,
        pertes=pertes,
        quantite_bonne=qte_bonne,
        quantite_rejetee=qte_rejetee,
    )


def _trouver_debut_activite(db: Session, machine_id: int) -> datetime | None:
    """Trouve le timestamp du dernier MACHINE_STARTED pour borner le calcul."""
    return db.execute(
        select(MachineEvent.created_at)
        .where(
            MachineEvent.machine_id == machine_id,
            MachineEvent.type == TypeEvenementMachine.MACHINE_STARTED,
        )
        .order_by(MachineEvent.created_at.desc())
        .limit(1)
    ).scalar()


def calculer_trs_machine(
    db: Session, machine: Machine, *, depuis: datetime | None = None, jusqua: datetime | None = None
) -> TRSResult:
    jusqua = jusqua or datetime.utcnow()

    if depuis is None:
        if machine.ordre_fabrication and machine.ordre_fabrication.date_debut_reelle:
            # Meilleur cas : on connaît le début réel de l'OF
            depuis = machine.ordre_fabrication.date_debut_reelle
        else:
            # Pas d'OF actif → chercher le dernier démarrage machine
            debut_activite = _trouver_debut_activite(db, machine.id)
            if debut_activite:
                depuis = debut_activite
            else:
                # Aucun événement de démarrage → machine jamais active
                return _zero_result()

    downtimes = list(
        db.execute(
            select(DowntimeEvent).where(
                DowntimeEvent.machine_id == machine.id,
                DowntimeEvent.start_time >= depuis,
                DowntimeEvent.start_time < jusqua,
            )
        ).scalars()
    )
    quality_events = list(
        db.execute(
            select(QualityEvent).where(
                QualityEvent.machine_id == machine.id,
                QualityEvent.created_at >= depuis,
                QualityEvent.created_at <= jusqua,
            )
        ).scalars()
    )
    ligne = machine.ligne_production
    return _calculer(
        depuis=depuis,
        jusqua=jusqua,
        downtimes=downtimes,
        quality_events=quality_events,
        cycle_cible_s=machine.temps_cycle_cible_s or Decimal("0"),
        taux_charge=ligne.taux_charge if ligne else Decimal("1.0"),
        taux_engagement=ligne.taux_engagement if ligne else Decimal("1.0"),
    )


def calculer_trs_ligne(
    db: Session, machines: list[Machine], *, depuis: datetime | None = None, jusqua: datetime | None = None
) -> TRSResult | None:
    """TRS d'une ligne = moyenne des machines de la ligne.

    Si certaines machines sont en marche ou ont déjà produit, on calcule la moyenne
    sur ces machines actives pour que l'activation d'une machine se traduise
    immédiatement par un TRS visible et dynamique sur le tableau de bord.
    """
    machines_avec_cycle = [m for m in machines if m.temps_cycle_cible_s]
    if not machines_avec_cycle:
        return None

    machines_actives = [
        m for m in machines_avec_cycle
        if m.statut == StatutMachine.MARCHE or (m.quantite_produite or 0) > 0
    ]
    cibles = machines_actives if machines_actives else machines_avec_cycle

    resultats = [
        calculer_trs_machine(db, m, depuis=depuis, jusqua=jusqua)
        for m in cibles
    ]
    if not resultats:
        return None
    n = Decimal(len(resultats))

    def moyenne(attr: str) -> Decimal:
        return sum((getattr(r, attr) for r in resultats), Decimal("0")) / n

    def moyenne_temps(attr: str) -> Decimal:
        return sum((getattr(r.temps, attr) for r in resultats), Decimal("0")) / n

    def moyenne_perte(attr: str) -> Decimal:
        return sum((getattr(r.pertes, attr) for r in resultats), Decimal("0")) / n

    return TRSResult(
        temps=TempsModel(
            tt=moyenne_temps("tt"), to=moyenne_temps("to"), tr=moyenne_temps("tr"),
            tf=moyenne_temps("tf"), tn=moyenne_temps("tn"), tu=moyenne_temps("tu"),
        ),
        tq=moyenne("tq"), tp=moyenne("tp"), do=moyenne("do"),
        trs=moyenne("trs"), trg=moyenne("trg"), tre=moyenne("tre"),
        pertes=Pertes(
            disponibilite_s=moyenne_perte("disponibilite_s"),
            performance_s=moyenne_perte("performance_s"),
            qualite_s=moyenne_perte("qualite_s"),
        ),
        quantite_bonne=sum(r.quantite_bonne for r in resultats),
        quantite_rejetee=sum(r.quantite_rejetee for r in resultats),
    )


def calculer_trs_ordre_fenetre(
    db: Session, of: OrdreFabrication, *, depuis: datetime, jusqua: datetime
) -> TRSResult:
    """TRS d'un OF restreint à une fenêtre de temps (pour les courbes horaires)."""
    downtimes = list(
        db.execute(
            select(DowntimeEvent).where(
                DowntimeEvent.ordre_fabrication_id == of.id, DowntimeEvent.start_time < jusqua
            )
        ).scalars()
    )
    quality_events = list(
        db.execute(
            select(QualityEvent).where(
                QualityEvent.ordre_fabrication_id == of.id,
                QualityEvent.created_at >= depuis,
                QualityEvent.created_at <= jusqua,
            )
        ).scalars()
    )
    return _calculer(
        depuis=depuis,
        jusqua=jusqua,
        downtimes=downtimes,
        quality_events=quality_events,
        cycle_cible_s=of.article.temps_cycle_cible_s or Decimal("0"),
        taux_charge=of.ligne_production.taux_charge if of.ligne_production else Decimal("1.0"),
        taux_engagement=of.ligne_production.taux_engagement if of.ligne_production else Decimal("1.0"),
    )


def calculer_trs_ordre(db: Session, of: OrdreFabrication) -> TRSResult:
    """TRS pour un OF donné, tous événements liés à cet OF (indépendamment de la machine)."""
    jusqua = of.date_fin_reelle or datetime.utcnow()
    depuis = of.date_debut_reelle or jusqua - FENETRE_DEFAUT

    downtimes = list(
        db.execute(
            select(DowntimeEvent).where(
                DowntimeEvent.ordre_fabrication_id == of.id, DowntimeEvent.start_time < jusqua
            )
        ).scalars()
    )
    quality_events = list(
        db.execute(
            select(QualityEvent).where(QualityEvent.ordre_fabrication_id == of.id)
        ).scalars()
    )
    return _calculer(
        depuis=depuis,
        jusqua=jusqua,
        downtimes=downtimes,
        quality_events=quality_events,
        cycle_cible_s=of.article.temps_cycle_cible_s or Decimal("0"),
        taux_charge=of.ligne_production.taux_charge if of.ligne_production else Decimal("1.0"),
        taux_engagement=of.ligne_production.taux_engagement if of.ligne_production else Decimal("1.0"),
    )
