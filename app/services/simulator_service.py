"""Validation des transitions demandées par le simulateur avant ingestion.

Chaque fonction vérifie que la transition a du sens compte tenu de l'état courant de la
machine, puis délègue à `event_service.enregistrer_evenement` (log + état + alertes).
"""
from __future__ import annotations

from app.core.exceptions import FabricationError
from app.models import Machine
from app.models.enums import StatutMachine, TypeEvenementMachine
from app.services import event_service
from sqlalchemy.orm import Session


def _verifier(condition: bool, message: str) -> None:
    if not condition:
        raise FabricationError(message)


def demarrer(db: Session, machine: Machine, *, ordre_fabrication_id: int | None) -> None:
    # Une machine MARCHE sans OF est « libre » (idle) : seule la présence d'un OF
    # déjà attaché signale une vraie occupation. Voir `line_scoring_service.
    # machine_libre_sur_ligne` et `auto_simulator._amorcer`, qui partagent cette
    # même définition de « libre ».
    _verifier(
        machine.ordre_fabrication_id is None,
        f"{machine.code} est déjà en marche avec un OF en cours.",
    )
    if ordre_fabrication_id is None:
        _verifier(machine.statut != StatutMachine.MARCHE, f"{machine.code} est déjà en marche.")
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.MACHINE_STARTED,
        payload={"ordre_fabrication_id": ordre_fabrication_id} if ordre_fabrication_id else {},
    )


def arreter(db: Session, machine: Machine) -> None:
    _verifier(machine.statut != StatutMachine.ARRET, f"{machine.code} est déjà arrêtée.")
    event_service.enregistrer_evenement(
        db, machine=machine, type_evenement=TypeEvenementMachine.MACHINE_STOPPED
    )


def mettre_en_pause(db: Session, machine: Machine) -> None:
    _verifier(machine.statut == StatutMachine.MARCHE, f"{machine.code} n'est pas en marche.")
    event_service.enregistrer_evenement(
        db, machine=machine, type_evenement=TypeEvenementMachine.MACHINE_IDLE, payload={"pause": True}
    )
    machine.statut = StatutMachine.PAUSE


def declencher_alarme(db: Session, machine: Machine, *, message: str | None) -> None:
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.MACHINE_ALARM,
        payload={"message": message} if message else {},
    )


def changer_temps_cycle(db: Session, machine: Machine, *, temps_cycle_s: float) -> None:
    _verifier(temps_cycle_s > 0, "Le temps de cycle doit être positif.")
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.CYCLE_TIME_CHANGED,
        payload={"temps_cycle_s": temps_cycle_s},
    )


def produire_bonne(db: Session, machine: Machine, *, quantite: int) -> None:
    _verifier(
        machine.statut == StatutMachine.MARCHE, f"{machine.code} doit être en marche pour produire."
    )
    _verifier(quantite > 0, "La quantité produite doit être positive.")
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.GOOD_UNIT_PRODUCED,
        payload={"quantite": quantite},
    )


def produire_rebut(db: Session, machine: Machine, *, quantite: int, cause: str) -> None:
    _verifier(
        machine.statut == StatutMachine.MARCHE, f"{machine.code} doit être en marche pour produire."
    )
    _verifier(quantite > 0, "La quantité rejetée doit être positive.")
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.SCRAP_UNIT_PRODUCED,
        payload={"quantite": quantite, "cause": cause},
    )


def declencher_arret(db: Session, machine: Machine, *, cause: str, comment: str | None) -> None:
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.DOWNTIME_STARTED,
        payload={"cause": cause, "comment": comment},
    )


def resoudre_arret(db: Session, machine: Machine, *, comment: str | None) -> None:
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.DOWNTIME_RESOLVED,
        payload={"comment": comment} if comment else {},
    )


def demarrer_maintenance(
    db: Session, machine: Machine, *, type_maintenance: str, description: str | None
) -> None:
    _verifier(
        machine.statut != StatutMachine.MAINTENANCE, f"{machine.code} est déjà en maintenance."
    )
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.MAINTENANCE_STARTED,
        payload={"type": type_maintenance, "description": description},
    )


def terminer_maintenance(
    db: Session, machine: Machine, *, prochaine_maintenance: str | None
) -> None:
    _verifier(machine.statut == StatutMachine.MAINTENANCE, f"{machine.code} n'est pas en maintenance.")
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.MAINTENANCE_ENDED,
        payload={"prochaine_maintenance": prochaine_maintenance} if prochaine_maintenance else {},
    )


def envoyer_tag(db: Session, machine: Machine, *, tag: str, valeur: float | str) -> None:
    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.SENSOR_TAG_UPDATED,
        payload={"tag": tag, "valeur": valeur},
    )
