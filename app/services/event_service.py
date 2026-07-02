"""Point d'entrée unique d'ingestion des événements machine (simulateur -> DB).

Chaque événement simulateur passe par `enregistrer_evenement` : écriture du log
(`MachineEvent`), application de la transition d'état, évaluation des alertes.
Le TRS n'est jamais stocké : il est recalculé à la lecture depuis les logs
(`trs_service`), donc il n'y a rien à invalider ici.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.services import alert_service, machine_state_service
from app.models import Machine, MachineEvent
from app.models.enums import TypeEvenementMachine


def enregistrer_evenement(
    db: Session,
    *,
    machine: Machine,
    type_evenement: TypeEvenementMachine,
    payload: dict | None = None,
) -> MachineEvent:
    payload = payload or {}

    # Applique la transition d'abord : pour MACHINE_STARTED notamment, c'est ce qui fixe
    # machine.ordre_fabrication_id — le log doit refléter l'état résultant, pas l'état avant.
    machine_state_service.appliquer(db, machine=machine, type_evenement=type_evenement, payload=payload)
    alert_service.evaluer_apres_evenement(
        db, machine=machine, type_evenement=type_evenement, payload=payload
    )

    evenement = MachineEvent(
        machine_id=machine.id,
        ordre_fabrication_id=machine.ordre_fabrication_id,
        type=type_evenement,
        payload=payload,
    )
    db.add(evenement)

    machine.dernier_evenement_at = datetime.utcnow()
    db.flush()
    db.refresh(evenement)
    return evenement
