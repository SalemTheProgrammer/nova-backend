"""Préparation d'un état de DÉMONSTRATION crédible, en un appel.

Pensé pour le bouton « Réinitialiser l'usine » de la page de connexion : remet
l'historique à zéro, garantit des valeurs de chiffrage réalistes, place des
échéances tenables sur les OF de démonstration, démarre 3 lignes, et injecte
quelques arrêts passés pour un Pareto parlant. Tout est ARRÊTÉ puis REDÉMARRÉ
proprement, et ce module tourne dans le process backend : il a donc accès à
l'hôte Sparkplug (les commandes machine partent réellement aux automates).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    Article,
    DowntimeEvent,
    Machine,
    MaintenanceEvent,
    OrdreFabrication,
    QualityEvent,
)
from app.models.enums import (
    CauseArret,
    CauseRebut,
    StatutMachine,
    StatutOF,
    TypeEvenementQualite,
    TypeMaintenance,
)
from app.protocols.sparkplug_b import runtime
from app.services import atelier_reset_service, machine_command_service

logger = get_logger(__name__)

# Marge par unité (TND) et coût horaire machine (TND/h) — réalistes pour une
# usine pharma tunisienne. Garantis à chaque préparation de démo.
MARGES = {
    "PARA500": "0.80", "PARA1000": "0.90", "ASPIRINE500": "0.70", "IBU400": "1.10",
    "AMOX500": "2.50", "CREME-DERM": "2.00", "MULTIVIT-GEL": "1.80", "PARA-SIROP": "1.20",
    "POUDRE-BEBE": "1.50", "SACHET-EFFER": "0.50", "TALC-POUDRE": "1.00", "TOUX-SIROP": "1.40",
    "VITC-SIROP": "1.10", "VITC1000": "0.90", "GELULE-VIDE-BTE": "0.30",
}
COUTS = {
    "M-01": "180", "M-02": "140", "M-03": "110", "M-04": "160", "M-05": "95",
    "M-06": "90", "M-07": "175", "M-08": "150", "M-09": "130", "M-10": "120",
}
# (code machine, numéro OF) — une ligne différente à chaque fois.
DEMARRAGES = [
    ("M-01", "OF-2026-00041"),
    ("M-04", "OF-2026-00025"),
    ("M-07", "OF-2026-00101"),
]
# Arrêts passés (terminés il y a 12–18 h : hors fenêtre TRS 8 h, dans les 24 h
# du Pareto) pour une distribution des arrêts crédible.
ARRETS_HISTORIQUES = [
    ("M-04", CauseArret.PANNE_MECANIQUE, 42, 16),
    ("M-01", CauseArret.CHANGEMENT_SERIE, 28, 14),
    ("M-07", CauseArret.REGLAGE_MACHINE, 15, 13),
    ("M-04", CauseArret.PANNE_ELECTRIQUE, 9, 12),
    ("M-01", CauseArret.NETTOYAGE, 6, 18),
]


# Machines qui « ont produit » ces derniers jours (pour l'historique riche).
MACHINES_HISTORIQUE = ["M-01", "M-02", "M-04", "M-05", "M-07"]
JOURS_HISTORIQUE = 7
CAUSES_ARRET_VARIEES = [
    CauseArret.PANNE_MECANIQUE, CauseArret.CHANGEMENT_SERIE, CauseArret.REGLAGE_MACHINE,
    CauseArret.PANNE_ELECTRIQUE, CauseArret.NETTOYAGE, CauseArret.MANQUE_OPERATEUR,
]
CAUSES_REBUT_VARIEES = [
    CauseRebut.DEFAUT_DIMENSIONNEL, CauseRebut.DEFAUT_VISUEL, CauseRebut.MAUVAIS_REGLAGE,
    CauseRebut.DEFAUT_MATIERE,
]


@dataclass(frozen=True)
class ResumeDemo:
    machines_demarrees: list[str]
    arrets_injectes: int
    articles_chiffres: int
    of_a_l_heure: int
    evenements_qualite: int
    maintenances: int
    of_termines: int


def _appliquer_parametres(db: Session) -> int:
    n = 0
    for a in db.execute(select(Article)).scalars():
        if a.code in MARGES:
            a.valeur_unitaire = Decimal(MARGES[a.code]); n += 1
    for m in db.execute(select(Machine)).scalars():
        if m.code in COUTS:
            m.cout_horaire = Decimal(COUTS[m.code])
    return n


def _semer_historique(db: Session) -> tuple[int, int, int]:
    """Sème un historique crédible sur les derniers jours pour que tous les écrans
    (qualité, arrêts, maintenance, historique OEE, OF terminés) soient vivants —
    et pas seulement la production en direct. Renvoie (qualité, maintenances, OF
    terminés). Rien dans les 8 dernières heures : le TRS courant reste propre."""
    rng = random.Random(42)
    now = datetime.utcnow()
    machines = {
        m.code: m
        for m in db.execute(select(Machine).where(Machine.code.in_(MACHINES_HISTORIQUE))).scalars()
    }

    q_events = 0
    arrets = 0
    # Un historique jour par jour (hors journée en cours, hors 8 dernières heures).
    for jour in range(1, JOURS_HISTORIQUE + 1):
        minuit = (now - timedelta(days=jour)).replace(hour=0, minute=0, second=0, microsecond=0)
        for code, m in machines.items():
            if m.temps_cycle_cible_s is None or rng.random() < 0.12:
                continue  # une machine peut être à l'arrêt certains jours
            cadence = 60.0 / float(m.temps_cycle_cible_s)  # unités / min
            # ~10 h de production par jour, en créneaux horaires.
            heure_debut = rng.randint(6, 8)
            for h in range(heure_debut, heure_debut + rng.randint(8, 11)):
                t = minuit + timedelta(hours=h, minutes=rng.randint(0, 40))
                if t > now - timedelta(hours=8):
                    continue
                bonnes = int(cadence * rng.uniform(38, 55))  # sous la cadence nominale
                db.add(QualityEvent(
                    machine_id=m.id, type=TypeEvenementQualite.BONNE,
                    quantite=bonnes, created_at=t,
                ))
                q_events += 1
                if rng.random() < 0.35:  # rebuts épisodiques
                    db.add(QualityEvent(
                        machine_id=m.id, type=TypeEvenementQualite.REBUT,
                        quantite=max(1, int(bonnes * rng.uniform(0.01, 0.05))),
                        cause=rng.choice(CAUSES_REBUT_VARIEES), created_at=t + timedelta(minutes=5),
                    ))
                    q_events += 1
            # 0 à 2 arrêts dans la journée.
            for _ in range(rng.randint(0, 2)):
                debut = minuit + timedelta(hours=rng.randint(6, 18), minutes=rng.randint(0, 59))
                if debut > now - timedelta(hours=8):
                    continue
                duree = rng.randint(5, 55)
                db.add(DowntimeEvent(
                    machine_id=m.id, cause=rng.choice(CAUSES_ARRET_VARIEES),
                    start_time=debut, end_time=debut + timedelta(minutes=duree),
                    operator_comment="Historique (démonstration)",
                ))
                arrets += 1

    # Interventions de maintenance : préventives terminées, une corrective, une en cours.
    maint = 0
    plan = [
        ("M-02", TypeMaintenance.PREVENTIVE, "Graissage et contrôle périodique", 4, 3, True),
        ("M-05", TypeMaintenance.CORRECTIVE, "Remplacement courroie d'entraînement", 2, 2, True),
        ("M-07", TypeMaintenance.PREVENTIVE, "Changement des filtres", 6, 5, True),
        ("M-09", TypeMaintenance.PREVENTIVE, "Calibration doseuse (en cours)", 0, None, False),
    ]
    for code, type_m, desc, il_y_a_j, duree_h, terminee in plan:
        m = machines.get(code) or db.execute(
            select(Machine).where(Machine.code == code)
        ).scalar_one_or_none()
        if m is None:
            continue
        debut = now - timedelta(days=il_y_a_j, hours=2)
        db.add(MaintenanceEvent(
            machine_id=m.id, type=type_m, description=desc, start_time=debut,
            end_time=(debut + timedelta(hours=duree_h)) if terminee and duree_h else None,
            prochaine_maintenance=(date.today() + timedelta(days=rng.randint(20, 60)))
            if type_m == TypeMaintenance.PREVENTIVE else None,
        ))
        maint += 1

    # Quelques OF marqués TERMINÉ avec des quantités réalistes (production passée).
    termines = 0
    candidats = db.execute(
        select(OrdreFabrication)
        .where(OrdreFabrication.statut == StatutOF.PLANIFIE)
        .order_by(OrdreFabrication.id.desc())
        .limit(6)
    ).scalars().all()
    for i, of in enumerate(candidats):
        planifiee = float(of.quantite_planifiee)
        rejets = int(planifiee * rng.uniform(0.01, 0.04))
        of.quantite_bonne = Decimal(int(planifiee) - rejets)
        of.quantite_rejetee = Decimal(rejets)
        of.statut = StatutOF.TERMINE
        of.date_debut_reelle = now - timedelta(days=i + 2, hours=6)
        of.date_fin_reelle = now - timedelta(days=i + 2, hours=1)
        termines += 1

    return q_events, maint, termines


def preparer(db: Session) -> ResumeDemo:
    """Prépare l'état de démonstration complet. `db` sert aux écritures MES ;
    les commandes machine ouvrent leurs propres sessions."""
    # 1. Historique à zéro (refuse si une machine produit → on force l'arrêt avant).
    for m in db.execute(
        select(Machine).where(Machine.statut.in_((StatutMachine.MARCHE, StatutMachine.PAUSE)))
    ).scalars():
        try:
            machine_command_service.arreter(m.id)
        except Exception:  # noqa: BLE001
            m.statut = StatutMachine.ARRET
    db.commit()
    atelier_reset_service.reinitialiser_historique(db)

    # 2. Chiffrage réaliste + échéances tenables sur les OF de démonstration.
    articles = _appliquer_parametres(db)
    echeance = date.today() + timedelta(days=5)
    a_l_heure = 0
    numeros = [n for _, n in DEMARRAGES]
    for of in db.execute(
        select(OrdreFabrication).where(OrdreFabrication.numero.in_(numeros))
    ).scalars():
        of.date_echeance = echeance
        a_l_heure += 1
    db.commit()

    # 3. Re-naissance Sparkplug puis démarrages (hôte disponible dans ce process).
    host = runtime.get_host()
    if host is not None and host.connected:
        host.request_rebirth_all()
    time.sleep(4)

    demarrees: list[str] = []
    for code, numero in DEMARRAGES:
        machine = db.execute(select(Machine).where(Machine.code == code)).scalar_one_or_none()
        of = db.execute(
            select(OrdreFabrication).where(OrdreFabrication.numero == numero)
        ).scalar_one_or_none()
        if machine is None or of is None:
            continue
        try:
            machine_command_service.demarrer(machine.id, ordre_fabrication_id=of.id)
            demarrees.append(code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("demo_demarrage_echec", machine=code, error=str(exc))
        time.sleep(1)

    # 4. Arrêts récents (Pareto 24 h parlant) + historique riche sur 7 jours
    #    (qualité, arrêts, maintenances, OF terminés) pour des écrans vivants.
    now = datetime.utcnow()
    codes = {m.code: m.id for m in db.execute(select(Machine)).scalars()}
    injectes = 0
    for code, cause, minutes, il_y_a_h in ARRETS_HISTORIQUES:
        if code not in codes:
            continue
        debut = now - timedelta(hours=il_y_a_h)
        db.add(DowntimeEvent(
            machine_id=codes[code], cause=cause,
            start_time=debut, end_time=debut + timedelta(minutes=minutes),
            operator_comment="Historique (démonstration)",
        ))
        injectes += 1
    q_events, maint, termines = _semer_historique(db)
    db.commit()

    logger.info(
        "demo_prepare", machines=demarrees, arrets=injectes,
        qualite=q_events, maintenances=maint, of_termines=termines,
    )
    return ResumeDemo(
        machines_demarrees=demarrees,
        arrets_injectes=injectes,
        articles_chiffres=articles,
        of_a_l_heure=a_l_heure,
        evenements_qualite=q_events,
        maintenances=maint,
        of_termines=termines,
    )
