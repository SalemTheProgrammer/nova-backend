"""Transitions d'état machine appliquées pour chaque type d'événement simulateur.

Chaque fonction mute la `Machine` (et au besoin l'`OrdreFabrication` actif) et écrit les
enregistrements associés (arrêt, qualité, maintenance). N'effectue aucun commit : l'appelant
(`event_service`) contrôle la transaction.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DowntimeEvent, MaintenanceEvent, Machine, OrdreFabrication, QualityEvent
from app.models.enums import (
    CauseArret,
    CauseRebut,
    StatutMachine,
    StatutOF,
    TypeEvenementMachine,
    TypeEvenementQualite,
    TypeMaintenance,
)


def _downtime_ouvert(db: Session, machine_id: int) -> DowntimeEvent | None:
    return db.execute(
        select(DowntimeEvent)
        .where(DowntimeEvent.machine_id == machine_id, DowntimeEvent.end_time.is_(None))
        .order_by(DowntimeEvent.start_time.desc())
    ).scalars().first()


def _maintenance_ouverte(db: Session, machine_id: int) -> MaintenanceEvent | None:
    return db.execute(
        select(MaintenanceEvent)
        .where(MaintenanceEvent.machine_id == machine_id, MaintenanceEvent.end_time.is_(None))
        .order_by(MaintenanceEvent.start_time.desc())
    ).scalars().first()


def _ouvrir_arret(
    db: Session, machine: Machine, *, cause: CauseArret, comment: str | None = None
) -> DowntimeEvent:
    existant = _downtime_ouvert(db, machine.id)
    if existant is not None:
        return existant
    downtime = DowntimeEvent(
        machine_id=machine.id,
        ordre_fabrication_id=machine.ordre_fabrication_id,
        cause=cause,
        operator_comment=comment,
        start_time=datetime.utcnow(),
    )
    db.add(downtime)
    db.flush()
    return downtime


def _fermer_arret(db: Session, machine: Machine, *, comment: str | None = None) -> None:
    downtime = _downtime_ouvert(db, machine.id)
    if downtime is None:
        return
    downtime.end_time = datetime.utcnow()
    if comment:
        downtime.operator_comment = comment


def _reprendre_statut_actif(machine: Machine) -> StatutMachine:
    return StatutMachine.MARCHE if machine.ordre_fabrication_id else StatutMachine.ARRET


def appliquer(
    db: Session,
    *,
    machine: Machine,
    type_evenement: TypeEvenementMachine,
    payload: dict,
) -> None:
    """Applique la transition d'état correspondant à `type_evenement`."""

    if type_evenement == TypeEvenementMachine.MACHINE_STARTED:
        ordre_id = payload.get("ordre_fabrication_id")
        if ordre_id is not None:
            machine.ordre_fabrication_id = ordre_id
            of = db.get(OrdreFabrication, ordre_id)
            if of is not None:
                if of.date_debut_reelle is None:
                    of.date_debut_reelle = datetime.utcnow()
                if of.statut in (StatutOF.BROUILLON, StatutOF.PLANIFIE):
                    of.statut = StatutOF.EN_COURS
        _fermer_arret(db, machine)
        machine.statut = StatutMachine.MARCHE

    elif type_evenement == TypeEvenementMachine.MACHINE_STOPPED:
        machine.statut = StatutMachine.ARRET
        _ouvrir_arret(db, machine, cause=CauseArret.AUTRE, comment="Arrêt manuel machine")

    elif type_evenement == TypeEvenementMachine.MACHINE_IDLE:
        pass  # informational only — pas de changement d'état/downtime en v1

    elif type_evenement == TypeEvenementMachine.MACHINE_ALARM:
        machine.statut = StatutMachine.PANNE
        cause = CauseArret(payload.get("cause", CauseArret.PANNE_MECANIQUE.value))
        _ouvrir_arret(db, machine, cause=cause, comment=payload.get("message"))

    elif type_evenement == TypeEvenementMachine.MACHINE_MAINTENANCE:
        machine.statut = StatutMachine.MAINTENANCE
        _ouvrir_arret(db, machine, cause=CauseArret.MAINTENANCE_PLANIFIEE)

    elif type_evenement == TypeEvenementMachine.CYCLE_TIME_CHANGED:
        machine.temps_cycle_actuel_s = Decimal(str(payload["temps_cycle_s"]))

    elif type_evenement in (
        TypeEvenementMachine.GOOD_UNIT_PRODUCED,
        TypeEvenementMachine.SCRAP_UNIT_PRODUCED,
        TypeEvenementMachine.QUALITY_EVENT_CREATED,
    ):
        quantite = int(payload.get("quantite", 1))
        machine.quantite_produite += quantite
        of = (
            db.get(OrdreFabrication, machine.ordre_fabrication_id)
            if machine.ordre_fabrication_id
            else None
        )
        if type_evenement == TypeEvenementMachine.GOOD_UNIT_PRODUCED:
            machine.quantite_bonne += quantite
            db.add(
                QualityEvent(
                    machine_id=machine.id,
                    ordre_fabrication_id=machine.ordre_fabrication_id,
                    type=TypeEvenementQualite.BONNE,
                    quantite=quantite,
                )
            )
            if of is not None:
                of.quantite_bonne = of.quantite_bonne + Decimal(quantite)
        else:
            machine.quantite_rejetee += quantite
            cause = CauseRebut(payload.get("cause", CauseRebut.AUTRE.value))
            db.add(
                QualityEvent(
                    machine_id=machine.id,
                    ordre_fabrication_id=machine.ordre_fabrication_id,
                    type=TypeEvenementQualite.REBUT,
                    quantite=quantite,
                    cause=cause,
                )
            )
            if of is not None:
                of.quantite_rejetee = of.quantite_rejetee + Decimal(quantite)

    elif type_evenement == TypeEvenementMachine.DOWNTIME_STARTED:
        cause = CauseArret(payload.get("cause", CauseArret.AUTRE.value))
        machine.statut = StatutMachine.PANNE
        _ouvrir_arret(db, machine, cause=cause, comment=payload.get("comment"))

    elif type_evenement == TypeEvenementMachine.DOWNTIME_RESOLVED:
        _fermer_arret(db, machine, comment=payload.get("comment"))
        machine.statut = _reprendre_statut_actif(machine)

    elif type_evenement == TypeEvenementMachine.MAINTENANCE_STARTED:
        db.add(
            MaintenanceEvent(
                machine_id=machine.id,
                type=TypeMaintenance(payload.get("type", TypeMaintenance.PREVENTIVE.value)),
                description=payload.get("description"),
                start_time=datetime.utcnow(),
            )
        )
        machine.statut = StatutMachine.MAINTENANCE
        _ouvrir_arret(db, machine, cause=CauseArret.MAINTENANCE_PLANIFIEE)

    elif type_evenement == TypeEvenementMachine.MAINTENANCE_ENDED:
        maintenance = _maintenance_ouverte(db, machine.id)
        if maintenance is not None:
            maintenance.end_time = datetime.utcnow()
            prochaine = payload.get("prochaine_maintenance")
            if prochaine:
                from datetime import date

                maintenance.prochaine_maintenance = date.fromisoformat(prochaine)
        _fermer_arret(db, machine)
        machine.statut = _reprendre_statut_actif(machine)

    elif type_evenement == TypeEvenementMachine.SENSOR_TAG_UPDATED:
        pass  # stocké uniquement dans MachineEvent.payload, pas d'état dédié en v1

    elif type_evenement == TypeEvenementMachine.PRODUCTION_COUNT_UPDATED:
        pass  # réservé pour une future synchronisation de totalisateur capteur
