"""Préparation d'un état de DÉMONSTRATION crédible, en un appel.

Pensé pour le bouton « Réinitialiser l'usine » de la page de connexion : remet
l'historique à zéro, garantit des valeurs de chiffrage réalistes, place des
échéances tenables sur les OF de démonstration, démarre 3 lignes, et injecte
quelques arrêts passés pour un Pareto parlant. Tout est ARRÊTÉ puis REDÉMARRÉ
proprement, et ce module tourne dans le process backend : il a donc accès à
l'hôte Sparkplug (les commandes machine partent réellement aux automates).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Article, DowntimeEvent, Machine, OrdreFabrication
from app.models.enums import CauseArret, StatutMachine, StatutOF
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


@dataclass(frozen=True)
class ResumeDemo:
    machines_demarrees: list[str]
    arrets_injectes: int
    articles_chiffres: int
    of_a_l_heure: int


def _appliquer_parametres(db: Session) -> int:
    n = 0
    for a in db.execute(select(Article)).scalars():
        if a.code in MARGES:
            a.valeur_unitaire = Decimal(MARGES[a.code]); n += 1
    for m in db.execute(select(Machine)).scalars():
        if m.code in COUTS:
            m.cout_horaire = Decimal(COUTS[m.code])
    return n


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

    # 4. Arrêts historiques pour un Pareto parlant.
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
    db.commit()

    logger.info("demo_prepare", machines=demarrees, arrets=injectes)
    return ResumeDemo(
        machines_demarrees=demarrees,
        arrets_injectes=injectes,
        articles_chiffres=articles,
        of_a_l_heure=a_l_heure,
    )
