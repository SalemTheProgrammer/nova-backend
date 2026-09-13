"""Calcul du TRS / TRG / TRE (AFNOR NF E60-182), toujours recalculé à la lecture.

Modèle de temps emboîtés : TT ⊇ TO ⊇ TR ⊇ TF ⊇ TN ⊇ TU.
    TR = TO × taux_charge − arrêts PLANIFIÉS     (ils ne pénalisent pas le TRS)
    TF = TR − arrêts NON planifiés (hors micro-arrêts)
    TN = pièces produites × temps de cycle de référence
    TU = pièces bonnes × temps de cycle de référence
    TQ = TU/TN (qualité)   TP = TN/TF (performance)   DO = TF/TR (disponibilité)
    TRS = TQ × TP × DO = TU/TR
    TRG = TU/TO = TRS × TR/TO
    TRE = TRG × taux_engagement (TO/TT)
Les micro-arrêts ne sont pas retirés de TF : ils ressortent comme écart de
cadence, donc en performance (TP), comme le veut la norme.
`taux_charge`/`taux_engagement` sont des multiplicateurs configurables par ligne (pas de
calendrier d'équipes en v1) : à 1.0 et sans arrêt planifié, TRS = TRG = TRE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import DowntimeEvent, Machine, MachineEvent, OrdreFabrication, QualityEvent
from app.models.enums import CauseArret, StatutMachine, TypeEvenementMachine, TypeEvenementQualite

FENETRE_DEFAUT = timedelta(hours=8)

_ZERO = Decimal("0")

# Classement des causes d'arrêt (NF E 60-182) — source unique, reprise par le
# tableau de bord : arrêts planifiés → retirés de TR (hors TRS), micro-arrêts →
# perte de performance, tout le reste → perte de disponibilité (TF).
CAUSES_PLANIFIEES = frozenset(
    {
        CauseArret.MAINTENANCE_PLANIFIEE,
        CauseArret.CHANGEMENT_SERIE,
        CauseArret.REGLAGE_MACHINE,
        CauseArret.NETTOYAGE,
        CauseArret.PRELEVEMENT_QUALITE,
    }
)
CAUSES_MICRO = frozenset({CauseArret.MICRO_ARRET})


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
    planifies = [d for d in downtimes if d.cause in CAUSES_PLANIFIEES]
    non_planifies = [
        d for d in downtimes if d.cause not in CAUSES_PLANIFIEES and d.cause not in CAUSES_MICRO
    ]
    tr = max(Decimal("0"), to * taux_charge - _duree_arrets(planifies, depuis, jusqua))
    tf = max(Decimal("0"), tr - _duree_arrets(non_planifies, depuis, jusqua))

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
    # TRG = TU/TO : TR/TO porte à la fois le taux de charge ET les arrêts planifiés.
    trg = trs * _ratio(tr, to)
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
        debut_activite = _trouver_debut_activite(db, machine.id)
        if debut_activite:
            # Session active de la machine bornée à la fenêtre d'équipe (8 h)
            depuis = max(jusqua - FENETRE_DEFAUT, debut_activite)
        elif machine.ordre_fabrication and machine.ordre_fabrication.date_debut_reelle:
            # Pas d'événement MACHINE_STARTED mais OF avec début réel connu
            depuis = max(jusqua - FENETRE_DEFAUT, machine.ordre_fabrication.date_debut_reelle)
        else:
            # Aucun événement de démarrage → machine jamais active
            return _zero_result()

    # Tout arrêt qui CHEVAUCHE la fenêtre (y compris un arrêt ouvert commencé
    # avant elle : sinon une machine arrêtée depuis longtemps affichait DO = 100 %).
    downtimes = list(
        db.execute(
            select(DowntimeEvent).where(
                DowntimeEvent.machine_id == machine.id,
                DowntimeEvent.start_time < jusqua,
                or_(DowntimeEvent.end_time.is_(None), DowntimeEvent.end_time > depuis),
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
    """TRS d'une ligne / de l'usine, par SOMME des temps des machines requises.

    On additionne TT, TO, TR, TF, TN et TU puis on en déduit les ratios. Faire la
    moyenne des ratios de chaque machine était faux sur deux points :
    - DO × TP × TQ ne redonnait plus le TRS affiché ;
    - une machine sans production (TQ = TP = 0 par convention) tirait la moyenne
      vers le bas : 2 machines, dont une à l'arrêt sans rien produire, donnaient
      une qualité de 50 % même sans aucun rebut.

    Une machine n'entre dans le calcul que si elle était REQUISE sur la fenêtre :
    elle y a produit, ou (fenêtre en cours) elle tourne ou porte un OF. Une
    machine au repos sans OF n'a pas de temps requis.
    """
    machines_avec_cycle = [m for m in machines if m.temps_cycle_cible_s]
    if not machines_avec_cycle:
        return None

    maintenant = datetime.utcnow()
    en_cours = jusqua is None or jusqua >= maintenant - timedelta(minutes=1)
    retenus: list[tuple[Machine, TRSResult]] = []
    for m in machines_avec_cycle:
        r = calculer_trs_machine(db, m, depuis=depuis, jusqua=jusqua)
        a_produit = r.quantite_bonne + r.quantite_rejetee > 0
        requise = en_cours and (m.statut == StatutMachine.MARCHE or m.ordre_fabrication_id is not None)
        if a_produit or requise:
            retenus.append((m, r))
    if not retenus:
        return _zero_result()

    resultats = [r for _, r in retenus]

    def somme_temps(attr: str) -> Decimal:
        return sum((getattr(r.temps, attr) for r in resultats), Decimal("0"))

    def somme_perte(attr: str) -> Decimal:
        return sum((getattr(r.pertes, attr) for r in resultats), Decimal("0"))

    tt, to, tr = somme_temps("tt"), somme_temps("to"), somme_temps("tr")
    tf, tn, tu = somme_temps("tf"), somme_temps("tn"), somme_temps("tu")
    tq, tp, do = _ratio(tu, tn), _ratio(tn, tf), _ratio(tf, tr)
    trs = tq * tp * do
    trg = trs * _ratio(tr, to)
    # Taux d'engagement : propre à chaque ligne, pondéré par le temps d'ouverture.
    engagement = (
        sum(
            (
                (m.ligne_production.taux_engagement if m.ligne_production else Decimal("1.0"))
                * r.temps.to
                for m, r in retenus
            ),
            Decimal("0"),
        )
        / to
        if to > 0
        else Decimal("1.0")
    )

    return TRSResult(
        temps=TempsModel(tt=tt, to=to, tr=tr, tf=tf, tn=tn, tu=tu),
        tq=tq, tp=tp, do=do,
        trs=trs, trg=trg, tre=trg * engagement,
        pertes=Pertes(
            disponibilite_s=somme_perte("disponibilite_s"),
            performance_s=somme_perte("performance_s"),
            qualite_s=somme_perte("qualite_s"),
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
