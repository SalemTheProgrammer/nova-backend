"""File d'attente d'une ligne de production : occupation, file EDD, préemption.

Le problème métier : une ligne n'a qu'un nombre limité de machines et chacune ne
porte qu'un OF à la fois. Vouloir lancer un OF sur une ligne déjà occupée était
donc bloquant. Deux issues, sans nouveau schéma de base :

- **Préempter** : arrêter l'OF en cours pour lancer le nouveau tout de suite. Le
  sort de l'OF interrompu est choisi à chaque fois (`DispositionPreemption`).
- **Mettre en file** : rattacher l'OF (statut PLANIFIE) à la ligne. La file est
  *dérivée* — aucun champ dédié — et triée par échéance (EDD) puis id.

Ce module ne porte que la partie MES (lecture de l'occupation, sort de l'OF
préempté). Les commandes machine — arrêt de la machine préemptée, démarrage du
nouvel OF — sont orchestrées par `production_control_service`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import FabricationError
from app.models import Machine, OrdreFabrication
from app.models.enums import StatutOF


class DispositionPreemption(str, Enum):
    """Sort réservé à l'OF interrompu lors d'une préemption (choisi à chaque fois)."""

    REQUEUE = "requeue"  # remis en file sur la même ligne, reprend le reliquat
    PAUSE = "pause"  # remis en PLANIFIE mais retiré de la ligne (sort de la file)
    CANCEL = "cancel"  # annulé


@dataclass
class OccupationMachine:
    """Une machine occupée par un OF sur la ligne."""

    machine: Machine
    ordre: OrdreFabrication


def reste_a_produire(of: OrdreFabrication) -> int:
    """Reliquat = planifié − (bonnes + rejetées), jamais négatif (même règle que l'UI)."""
    fait = of.quantite_bonne + of.quantite_rejetee
    return max(0, int(of.quantite_planifiee - fait))


def occupations_ligne(db: Session, ligne_id: int) -> list[OccupationMachine]:
    """Machines de la ligne actuellement porteuses d'un OF."""
    machines = (
        db.execute(
            select(Machine)
            .where(
                Machine.ligne_production_id == ligne_id,
                Machine.actif.is_(True),
                Machine.ordre_fabrication_id.is_not(None),
            )
            .order_by(Machine.code)
        )
        .scalars()
        .all()
    )
    resultat: list[OccupationMachine] = []
    for m in machines:
        of = db.get(OrdreFabrication, m.ordre_fabrication_id)
        if of is not None:
            resultat.append(OccupationMachine(machine=m, ordre=of))
    return resultat


def cle_edd(of: OrdreFabrication) -> tuple[date, int]:
    """Tri EDD : échéance la plus proche d'abord ; sans échéance → repoussé en fin."""
    echeance = of.date_echeance or date.max
    return (echeance, of.id)


def file_attente(db: Session, ligne_id: int) -> list[OrdreFabrication]:
    """OF en attente sur la ligne : PLANIFIE, reliquat > 0, pas déjà sur une machine.

    Ordonnée par échéance (EDD) puis id — cohérent avec l'ordonnanceur.
    """
    sur_machine = {occ.ordre.id for occ in occupations_ligne(db, ligne_id)}
    candidats = (
        db.execute(
            select(OrdreFabrication).where(
                OrdreFabrication.ligne_production_id == ligne_id,
                OrdreFabrication.statut == StatutOF.PLANIFIE,
            )
        )
        .scalars()
        .all()
    )
    file = [of for of in candidats if of.id not in sur_machine and reste_a_produire(of) > 0]
    file.sort(key=cle_edd)
    return file


def prochain_of(db: Session, ligne_id: int) -> OrdreFabrication | None:
    """Tête de file de la ligne (le prochain OF à lancer selon EDD), ou None."""
    file = file_attente(db, ligne_id)
    return file[0] if file else None


def mettre_en_file(db: Session, of: OrdreFabrication, ligne_id: int) -> OrdreFabrication:
    """Rattache l'OF à la ligne et le met en attente (PLANIFIE).

    Ne démarre rien : l'OF prend sa place dans la file dérivée et sera lancé plus
    tard (préemption, ou notification à la libération de la ligne).
    """
    if of.statut in (StatutOF.TERMINE, StatutOF.ANNULE):
        raise FabricationError(
            f"Impossible de mettre {of.numero} en file : statut {of.statut.value}."
        )
    if of.statut == StatutOF.EN_COURS:
        raise FabricationError(
            f"L'OF {of.numero} est déjà en cours ; arrêtez-le avant de le remettre en file."
        )
    of.ligne_production_id = ligne_id
    of.statut = StatutOF.PLANIFIE
    # Un changement de ressource invalide le créneau projeté (même règle que l'API ligne).
    of.date_debut_prevue = None
    of.date_fin_prevue = None
    db.flush()
    return of


def appliquer_disposition(
    db: Session,
    *,
    machine_id: int,
    of_id: int,
    disposition: DispositionPreemption,
) -> None:
    """Partie MES d'une préemption, APRÈS l'arrêt confirmé de la machine :
    libère la machine et applique le sort choisi à l'OF interrompu.

    - REQUEUE : l'OF repasse PLANIFIE en gardant sa ligne (reprend son reliquat).
    - PAUSE   : PLANIFIE mais détaché de la ligne (sort de la file).
    - CANCEL  : ANNULE.
    """
    machine = db.get(Machine, machine_id)
    if machine is not None and machine.ordre_fabrication_id == of_id:
        machine.ordre_fabrication_id = None

    of = db.get(OrdreFabrication, of_id)
    if of is None:
        return
    if disposition == DispositionPreemption.CANCEL:
        of.statut = StatutOF.ANNULE
    else:
        of.statut = StatutOF.PLANIFIE
        if disposition == DispositionPreemption.PAUSE:
            of.ligne_production_id = None
    of.date_debut_prevue = None
    of.date_fin_prevue = None
    db.flush()
