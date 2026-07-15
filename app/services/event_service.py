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
    detacher_ordre = machine_state_service.appliquer(
        db, machine=machine, type_evenement=type_evenement, payload=payload
    )
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

    # Preserve the completed OF on the event above, then release the machine.
    of_termine_id = machine.ordre_fabrication_id if detacher_ordre else None
    if detacher_ordre:
        machine.ordre_fabrication_id = None

    machine.dernier_evenement_at = datetime.utcnow()
    db.flush()
    db.refresh(evenement)

    # Notify & confirm : quand un OF atteint sa quantité, la ligne se libère.
    # On signale la libération + le prochain OF en file (tri EDD) SANS le lancer —
    # l'opérateur (ou Nova) confirme le démarrage.
    if detacher_ordre:
        _notifier_ligne_liberee(db, machine, of_termine_id)

    return evenement


def _notifier_ligne_liberee(db: Session, machine: Machine, of_termine_id: int | None) -> None:
    """Diffuse `ligne_liberee` avec le prochain OF en attente (sans démarrage auto)."""
    # Imports paresseux : évite le cycle event_service ↔ simulator_service ↔ line_queue_service.
    from app.services import broadcast_service, line_queue_service

    prochain = line_queue_service.prochain_of(db, machine.ligne_production_id)
    broadcast_service.diffuser(
        {
            "type": "ligne_liberee",
            "ligne_production_id": machine.ligne_production_id,
            "machine_id": machine.id,
            "machine_code": machine.code,
            "of_termine_id": of_termine_id,
            "prochain_of": (
                {
                    "id": prochain.id,
                    "numero": prochain.numero,
                    "code_article": prochain.article.code if prochain.article else None,
                    "quantite_planifiee": str(prochain.quantite_planifiee),
                    "date_echeance": (
                        prochain.date_echeance.isoformat() if prochain.date_echeance else None
                    ),
                }
                if prochain is not None
                else None
            ),
        }
    )
