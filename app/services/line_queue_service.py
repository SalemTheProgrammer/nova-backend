"""File d'attente d'une ligne de production : préemption et mise en file d'OF.

Le problème métier : une ligne n'a qu'un nombre limité de machines et chacune ne
porte qu'un OF à la fois. Vouloir lancer un OF sur une ligne déjà occupée était
donc bloquant. Ce service offre deux issues, sans nouveau schéma de base :

- **Préempter** : arrêter l'OF en cours pour lancer le nouveau tout de suite. Le
  sort de l'OF interrompu est choisi à chaque fois (`DispositionPreemption`).
- **Mettre en file** : rattacher l'OF (statut PLANIFIE) à la ligne. La file est
  *dérivée* — aucun champ dédié — et triée par échéance (EDD) puis id.

La libération automatique quand la quantité planifiée est atteinte existe déjà
(`machine_state_service`). Ce service fournit le « prochain » à lancer et la
préemption ; le démarrage effectif reste une commande explicite (notify & confirm).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import FabricationError
from app.models import Machine, OrdreFabrication
from app.models.enums import StatutMachine, StatutOF
from app.services import line_scoring_service, simulator_service


class DispositionPreemption(str, Enum):
    """Sort réservé à l'OF interrompu lors d'une préemption (choisi à chaque fois)."""

    REQUEUE = "requeue"  # remis en file sur la même ligne, reprend le reliquat
    PAUSE = "pause"      # remis en PLANIFIE mais retiré de la ligne (sort de la file)
    CANCEL = "cancel"    # annulé


@dataclass
class OccupationMachine:
    """Une machine occupée par un OF sur la ligne."""

    machine: Machine
    ordre: OrdreFabrication


def _reste_a_produire(of: OrdreFabrication) -> int:
    """Reliquat = planifié − (bonnes + rejetées), jamais négatif (même règle que l'UI)."""
    fait = of.quantite_bonne + of.quantite_rejetee
    return max(0, int(of.quantite_planifiee - fait))


def occupations_ligne(db: Session, ligne_id: int) -> list[OccupationMachine]:
    """Machines de la ligne actuellement porteuses d'un OF (EN_COURS)."""
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


def _cle_edd(of: OrdreFabrication) -> tuple[date, int]:
    """Tri EDD : échéance la plus proche d'abord ; sans échéance → repoussé en fin."""
    echeance = of.date_echeance or date.max
    return (echeance, of.id)


def file_attente(db: Session, ligne_id: int) -> list[OrdreFabrication]:
    """OF en attente sur la ligne : PLANIFIE, reliquat > 0, pas déjà sur une machine.

    Ordonnée par échéance (EDD) puis id — cohérent avec l'ordonnanceur.
    """
    sur_machine = {
        occ.ordre.id for occ in occupations_ligne(db, ligne_id)
    }
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
    file = [
        of
        for of in candidats
        if of.id not in sur_machine and _reste_a_produire(of) > 0
    ]
    file.sort(key=_cle_edd)
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


def preempter(
    db: Session,
    occupation: OccupationMachine,
    disposition: DispositionPreemption,
) -> None:
    """Arrête l'OF en cours sur une machine et applique le sort choisi.

    - REQUEUE : l'OF repasse PLANIFIE en gardant sa ligne (reprend son reliquat).
    - PAUSE   : PLANIFIE mais détaché de la ligne (sort de la file).
    - CANCEL  : ANNULE.

    L'arrêt machine détache déjà `ordre_fabrication_id` côté machine ; on ajuste
    ensuite l'OF. Aucun commit ici — l'appelant contrôle la transaction.
    """
    machine = occupation.machine
    of = occupation.ordre

    if machine.statut != StatutMachine.ARRET:
        # `arreter` lève si déjà à l'arrêt ; on ne l'appelle donc que si utile.
        simulator_service.arreter(db, machine)

    # La machine peut rester rattachée à l'OF (arrêt ≠ fin) : on la libère pour
    # que la ligne puisse accueillir le nouvel OF.
    machine.ordre_fabrication_id = None

    if disposition == DispositionPreemption.CANCEL:
        of.statut = StatutOF.ANNULE
    elif disposition == DispositionPreemption.PAUSE:
        of.statut = StatutOF.PLANIFIE
        of.ligne_production_id = None
        of.date_debut_prevue = None
        of.date_fin_prevue = None
    else:  # REQUEUE
        of.statut = StatutOF.PLANIFIE
        of.date_debut_prevue = None
        of.date_fin_prevue = None
    db.flush()


def lancer_of_sur_ligne(
    db: Session,
    of: OrdreFabrication,
    *,
    preempt_disposition: DispositionPreemption | None = None,
) -> Machine:
    """Lance l'OF sur une machine de sa ligne, en préemptant au besoin.

    - Si une machine est libre → démarrage immédiat dessus.
    - Sinon, si `preempt_disposition` est fourni → on préempte la 1re machine
      occupée (sort de l'OF interrompu = disposition) puis on démarre.
    - Sinon → `FabricationError` (l'appelant proposera préemption ou mise en file).

    Retourne la machine démarrée. Aucun commit ici.
    """
    if of.ligne_production_id is None:
        raise FabricationError(f"Aucune ligne n'est affectée à l'OF {of.numero}.")
    if of.statut in (StatutOF.TERMINE, StatutOF.ANNULE):
        raise FabricationError(f"Impossible de lancer {of.numero} : statut {of.statut.value}.")

    ligne_id = of.ligne_production_id
    machine = line_scoring_service.machine_libre_sur_ligne(db, ligne_id)

    if machine is None:
        if preempt_disposition is None:
            raise FabricationError(
                f"Aucune machine libre sur la ligne (id={ligne_id}) : "
                "préemptez un OF en cours ou mettez celui-ci en file."
            )
        occupations = occupations_ligne(db, ligne_id)
        if not occupations:
            raise FabricationError(
                f"Aucune machine libre ni occupée sur la ligne (id={ligne_id})."
            )
        # On préempte l'OF dont l'échéance est la plus lointaine (le moins urgent).
        cible = max(occupations, key=lambda occ: _cle_edd(occ.ordre))
        preempter(db, cible, preempt_disposition)
        machine = line_scoring_service.machine_libre_sur_ligne(db, ligne_id)
        if machine is None:
            raise FabricationError(
                f"La ligne (id={ligne_id}) reste occupée après préemption."
            )

    simulator_service.demarrer(db, machine, ordre_fabrication_id=of.id)
    db.flush()
    return machine
