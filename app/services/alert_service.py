"""Création/résolution d'alertes à partir des événements machine."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, Machine, QualityEvent
from app.models.enums import SeveriteAlerte, TypeEvenementMachine, TypeEvenementQualite

SEUIL_TAUX_REBUT = Decimal("0.15")
FENETRE_REBUT = 20


def _alerte_ouverte(db: Session, machine_id: int, type_: str) -> Alert | None:
    return db.execute(
        select(Alert).where(
            Alert.machine_id == machine_id, Alert.type == type_, Alert.resolved.is_(False)
        )
    ).scalars().first()


def _resoudre(db: Session, machine_id: int, type_: str) -> None:
    from datetime import datetime

    alerte = _alerte_ouverte(db, machine_id, type_)
    if alerte is not None:
        alerte.resolved = True
        alerte.resolved_at = datetime.utcnow()


def _taux_rebut_recent(db: Session, machine_id: int) -> Decimal | None:
    events = db.execute(
        select(QualityEvent)
        .where(QualityEvent.machine_id == machine_id)
        .order_by(QualityEvent.created_at.desc())
        .limit(FENETRE_REBUT)
    ).scalars().all()
    if not events:
        return None
    total = sum(e.quantite for e in events)
    rebuts = sum(e.quantite for e in events if e.type == TypeEvenementQualite.REBUT)
    if total == 0:
        return None
    return Decimal(rebuts) / Decimal(total)


def evaluer_apres_evenement(
    db: Session, *, machine: Machine, type_evenement: TypeEvenementMachine, payload: dict
) -> None:
    if type_evenement in (
        TypeEvenementMachine.DOWNTIME_STARTED,
        TypeEvenementMachine.MACHINE_STOPPED,
        TypeEvenementMachine.MACHINE_ALARM,
    ):
        if _alerte_ouverte(db, machine.id, "arret") is None:
            severite = (
                SeveriteAlerte.CRITICAL
                if type_evenement == TypeEvenementMachine.MACHINE_ALARM
                else SeveriteAlerte.WARNING
            )
            db.add(
                Alert(
                    machine_id=machine.id,
                    ordre_fabrication_id=machine.ordre_fabrication_id,
                    severity=severite,
                    type="arret",
                    message=f"Arrêt en cours sur {machine.code}"
                    + (f" : {payload['message']}" if payload.get("message") else ""),
                )
            )

    if type_evenement in (
        TypeEvenementMachine.DOWNTIME_RESOLVED,
        TypeEvenementMachine.MAINTENANCE_ENDED,
    ):
        _resoudre(db, machine.id, "arret")

    if type_evenement == TypeEvenementMachine.SCRAP_UNIT_PRODUCED:
        taux = _taux_rebut_recent(db, machine.id)
        if taux is not None and taux > SEUIL_TAUX_REBUT:
            if _alerte_ouverte(db, machine.id, "qualite") is None:
                db.add(
                    Alert(
                        machine_id=machine.id,
                        ordre_fabrication_id=machine.ordre_fabrication_id,
                        severity=SeveriteAlerte.WARNING,
                        type="qualite",
                        message=(
                            f"Taux de rebut élevé sur {machine.code} "
                            f"({taux * 100:.0f}% des {FENETRE_REBUT} dernières unités)"
                        ),
                    )
                )

    if type_evenement == TypeEvenementMachine.GOOD_UNIT_PRODUCED:
        taux = _taux_rebut_recent(db, machine.id)
        if taux is not None and taux <= SEUIL_TAUX_REBUT:
            _resoudre(db, machine.id, "qualite")
