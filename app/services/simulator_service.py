"""Validation des transitions demandées par le simulateur avant ingestion.

Chaque fonction vérifie que la transition a du sens compte tenu de l'état courant de la
machine, puis délègue à `event_service.enregistrer_evenement` (log + état + alertes).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import FabricationError
from app.models import Machine, OrdreFabrication
from app.models.enums import StatutMachine, StatutOF, TypeEvenementMachine
from app.services import event_service


def _verifier(condition: bool, message: str) -> None:
    if not condition:
        raise FabricationError(message)


def demarrer(db: Session, machine: Machine, *, ordre_fabrication_id: int | None) -> None:
    if machine.statut == StatutMachine.MARCHE:
        return

    of_id = ordre_fabrication_id or machine.ordre_fabrication_id
    if of_id is None and machine.ligne_production_id:
        # Trouver un OF planifié pour cette ligne
        of = db.execute(
            select(OrdreFabrication).where(
                OrdreFabrication.ligne_production_id == machine.ligne_production_id,
                OrdreFabrication.statut.in_([StatutOF.PLANIFIE, StatutOF.BROUILLON]),
            ).order_by(OrdreFabrication.id.asc())
        ).scalars().first()
        if of:
            of.statut = StatutOF.EN_COURS
            of.date_debut_reelle = of.date_debut_reelle or datetime.utcnow()
            of_id = of.id
            machine.ordre_fabrication_id = of.id
            db.flush()

    event_service.enregistrer_evenement(
        db,
        machine=machine,
        type_evenement=TypeEvenementMachine.MACHINE_STARTED,
        payload={"ordre_fabrication_id": of_id} if of_id else {},
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


async def reinitialiser_atelier(db: Session) -> dict:
    """Remet à zéro l'ensemble de la ligne, des compteurs et de l'historique d'événements."""
    from sqlalchemy import delete, select
    from app.services.auto_simulator import auto_simulator
    from app.models import (
        Machine,
        OrdreFabrication,
        MachineEvent,
        DowntimeEvent,
        QualityEvent,
        MaintenanceEvent,
        Alert,
        AgentProposal,
        OFConsommationMP,
    )
    from app.models.enums import StatutOF
    from app.api.routes.routes_machines import machine_read
    from app.api.routes.routes_kpi import clear_kpi_caches
    from app.services.websocket_manager import manager

    # 1. Arrêter le simulateur autonome s'il tourne et vider ses compteurs internes
    auto_simulator.arreter()
    auto_simulator._credit.clear()
    auto_simulator._micro_fin.clear()
    auto_simulator._temperature.clear()
    auto_simulator._tick_compteur = 0

    # 2. Supprimer tous les événements dynamiques de production
    db.execute(delete(MachineEvent))
    db.execute(delete(DowntimeEvent))
    db.execute(delete(QualityEvent))
    db.execute(delete(MaintenanceEvent))
    db.execute(delete(Alert))
    db.execute(delete(AgentProposal))
    db.execute(delete(OFConsommationMP))

    # 3. Remise à zéro des machines
    machines = list(db.execute(select(Machine)).scalars())
    for m in machines:
        m.statut = StatutMachine.ARRET
        m.quantite_produite = 0
        m.quantite_bonne = 0
        m.quantite_rejetee = 0
        m.ordre_fabrication_id = None
        m.temps_cycle_actuel_s = m.temps_cycle_cible_s
        m.dernier_evenement_at = None

    # 4. Remise à l'état initial des ordres de fabrication
    ofs = list(db.execute(select(OrdreFabrication)).scalars())
    for of in ofs:
        of.statut = StatutOF.PLANIFIE
        of.quantite_bonne = 0
        of.quantite_rejetee = 0
        of.date_debut_reelle = None
        of.date_fin_reelle = None

    db.commit()

    # 5. Invalider les caches KPI du dashboard
    clear_kpi_caches()

    # 6. Diffuser l'événement de remise à zéro et l'état réinitialisé des machines
    await manager.broadcast({"type": "reset"})
    for m in machines:
        db.refresh(m)
        lecture = machine_read(db, m)
        await manager.broadcast({"type": "machine_update", "machine": lecture.model_dump(mode="json")})

    return {"ok": True, "message": "Atelier et compteurs réinitialisés à 0 avec succès."}

