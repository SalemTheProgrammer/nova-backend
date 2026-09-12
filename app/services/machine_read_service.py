"""Lecture de l'état temps réel d'une machine : état MES, TRS, arrêt, automate.

Service partagé par les routes (`/machines`) et la diffusion WebSocket
(`broadcast_service`) — il vit dans la couche services pour qu'aucun service
n'ait à importer la couche routes.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DowntimeEvent, Machine
from app.models.sparkplug import SparkplugDevice
from app.protocols.sparkplug_b.contract import METRIC_OPERATOR_ID, METRIC_OPERATOR_NAME
from app.schemas.machine_schema import DowntimeActifRead, MachineRead, OperateurRead
from app.services import trs_service


def downtime_actif(db: Session, machine_id: int) -> DowntimeActifRead | None:
    arret = db.execute(
        select(DowntimeEvent)
        .where(DowntimeEvent.machine_id == machine_id, DowntimeEvent.end_time.is_(None))
        .order_by(DowntimeEvent.start_time.desc())
    ).scalars().first()
    if arret is None:
        return None
    return DowntimeActifRead(
        id=arret.id,
        cause=arret.cause,
        operator_comment=arret.operator_comment,
        start_time=arret.start_time,
    )


def _device(db: Session, machine_id: int) -> SparkplugDevice | None:
    return db.execute(
        select(SparkplugDevice).where(SparkplugDevice.machine_id == machine_id)
    ).scalars().first()


def automate_connecte(db: Session, machine_id: int) -> bool | None:
    """True/False selon l'automate Sparkplug rattaché ; None si aucun automate."""
    device = _device(db, machine_id)
    return None if device is None else bool(device.online)


def operateur_depuis_valeurs(valeurs: dict[str, Any] | None) -> OperateurRead | None:
    """Employé au poste, d'après les dernières valeurs publiées par l'automate."""
    valeurs = valeurs or {}
    matricule = str(valeurs.get(METRIC_OPERATOR_ID) or "").strip()
    if not matricule:
        return None
    nom = str(valeurs.get(METRIC_OPERATOR_NAME) or "").strip()
    return OperateurRead(matricule=matricule, nom=nom or None)


def operateur_au_poste(db: Session, machine_id: int) -> OperateurRead | None:
    """Employé badgé sur la machine ; None si personne, ou si l'automate est hors
    ligne (sa dernière valeur ne dit plus qui est au poste)."""
    device = _device(db, machine_id)
    if device is None or not device.online:
        return None
    return operateur_depuis_valeurs(device.last_values)


def machine_read(db: Session, machine: Machine) -> MachineRead:
    trs = trs_service.calculer_trs_machine(db, machine) if machine.temps_cycle_cible_s else None
    device = _device(db, machine.id)
    return MachineRead(
        id=machine.id,
        code=machine.code,
        nom=machine.nom,
        ligne_production_id=machine.ligne_production_id,
        temps_cycle_cible_s=machine.temps_cycle_cible_s,
        statut=machine.statut,
        ordre_fabrication_id=machine.ordre_fabrication_id,
        numero_of_actif=machine.ordre_fabrication.numero if machine.ordre_fabrication else None,
        temps_cycle_actuel_s=machine.temps_cycle_actuel_s,
        quantite_produite=machine.quantite_produite,
        quantite_bonne=machine.quantite_bonne,
        quantite_rejetee=machine.quantite_rejetee,
        dernier_evenement_at=machine.dernier_evenement_at,
        downtime_actif=downtime_actif(db, machine.id),
        automate_connecte=None if device is None else bool(device.online),
        # Un automate hors ligne ne dit plus qui est au poste : on ne l'affirme pas.
        operateur=operateur_depuis_valeurs(device.last_values) if device is not None and device.online else None,
        trs=trs.trs if trs else None,
        tq=trs.tq if trs else None,
        tp=trs.tp if trs else None,
        do=trs.do if trs else None,
    )
