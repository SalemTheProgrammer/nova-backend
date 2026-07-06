"""Scoring des lignes de production pour l'affectation d'un OF.

Score composite (0..1) par ligne active :
    45 % TRS courant de la ligne (fenêtre TRS standard)
    35 % disponibilité machines (part des machines libres, hors PANNE/MAINTENANCE)
    20 % charge (part des machines SANS OF affecté — plus la ligne est libre, mieux c'est)

Utilisé par l'outil agent `choisir_meilleure_ligne` et par le superviseur autonome
pour proposer un re-routage quand une ligne est bloquée.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LigneProduction, Machine
from app.models.enums import StatutMachine
from app.services import trs_service

POIDS_TRS = Decimal("0.45")
POIDS_DISPONIBILITE = Decimal("0.35")
POIDS_CHARGE = Decimal("0.20")

# TRS neutre quand la ligne n'a pas encore d'historique (pas de cycle cible / pas d'événements).
TRS_NEUTRE = Decimal("0.5")


@dataclass
class LigneScore:
    ligne_id: int
    code: str
    designation: str
    score: Decimal
    trs: Decimal | None
    machines_total: int
    machines_libres: int
    machines_en_panne: int
    machines_avec_of: int
    raison: str


def _machines_par_ligne(db: Session) -> dict[int, list[Machine]]:
    machines = db.execute(select(Machine).where(Machine.actif.is_(True))).scalars().all()
    par_ligne: dict[int, list[Machine]] = {}
    for m in machines:
        par_ligne.setdefault(m.ligne_production_id, []).append(m)
    return par_ligne


def scorer_lignes(
    db: Session, *, exclure_ligne_id: int | None = None
) -> list[LigneScore]:
    """Toutes les lignes actives, triées du meilleur au pire score."""
    lignes = (
        db.execute(select(LigneProduction).where(LigneProduction.actif.is_(True)))
        .scalars()
        .all()
    )
    par_ligne = _machines_par_ligne(db)
    scores: list[LigneScore] = []

    for ligne in lignes:
        if exclure_ligne_id is not None and ligne.id == exclure_ligne_id:
            continue
        machines = par_ligne.get(ligne.id, [])
        total = len(machines)
        if total == 0:
            continue

        en_panne = sum(
            1 for m in machines if m.statut in (StatutMachine.PANNE, StatutMachine.MAINTENANCE)
        )
        libres = sum(
            1
            for m in machines
            if m.statut not in (StatutMachine.PANNE, StatutMachine.MAINTENANCE)
            and m.ordre_fabrication_id is None
        )
        avec_of = sum(1 for m in machines if m.ordre_fabrication_id is not None)

        resultat = trs_service.calculer_trs_ligne(db, machines)
        trs = resultat.trs if resultat is not None else None

        part_libre = Decimal(libres) / Decimal(total)
        part_sans_of = Decimal(total - avec_of) / Decimal(total)
        score = (
            POIDS_TRS * (trs if trs is not None else TRS_NEUTRE)
            + POIDS_DISPONIBILITE * part_libre
            + POIDS_CHARGE * part_sans_of
        )

        details = []
        if trs is not None:
            details.append(f"TRS {trs * 100:.0f}%")
        else:
            details.append("TRS inconnu (pas d'historique)")
        details.append(f"{libres}/{total} machine(s) libre(s)")
        if en_panne:
            details.append(f"{en_panne} en panne/maintenance")
        if avec_of:
            details.append(f"{avec_of} OF en cours")

        scores.append(
            LigneScore(
                ligne_id=ligne.id,
                code=ligne.code,
                designation=ligne.designation,
                score=score,
                trs=trs,
                machines_total=total,
                machines_libres=libres,
                machines_en_panne=en_panne,
                machines_avec_of=avec_of,
                raison=", ".join(details),
            )
        )

    scores.sort(key=lambda s: s.score, reverse=True)
    return scores


def meilleure_ligne_disponible(
    db: Session, *, exclure_ligne_id: int | None = None
) -> LigneScore | None:
    """La meilleure ligne ayant au moins une machine libre, ou None."""
    for s in scorer_lignes(db, exclure_ligne_id=exclure_ligne_id):
        if s.machines_libres > 0:
            return s
    return None


def machine_libre_sur_ligne(db: Session, ligne_id: int) -> Machine | None:
    """Première machine libre (ni en panne/maintenance, sans OF) de la ligne."""
    machines = (
        db.execute(
            select(Machine)
            .where(Machine.ligne_production_id == ligne_id, Machine.actif.is_(True))
            .order_by(Machine.code)
        )
        .scalars()
        .all()
    )
    for m in machines:
        if (
            m.statut not in (StatutMachine.PANNE, StatutMachine.MAINTENANCE)
            and m.ordre_fabrication_id is None
        ):
            return m
    return None
