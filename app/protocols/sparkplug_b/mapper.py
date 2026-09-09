"""Moteur d'évaluation des règles de mappage des tags Sparkplug B vers les KPIs MES."""
from __future__ import annotations

from typing import Any
from decimal import Decimal
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.enums import CauseArret, StatutMachine, TypeEvenementMachine
from app.models.sparkplug import SparkplugDevice, SparkplugTagMapping, TagTransformation, TargetKpi
from app.models.machine import Machine
from app.protocols.sparkplug_b.codec import SparkplugPayload
from app.services import broadcast_service, event_service

logger = get_logger(__name__)

CARTE_CAUSE_MAP: dict[str, CauseArret] = {
    "CARTE_REGLAGE": CauseArret.REGLAGE_MACHINE,
    "CARTE_PANNE_MECA": CauseArret.PANNE_MECANIQUE,
    "CARTE_PANNE_ELEC": CauseArret.PANNE_ELECTRIQUE,
    "CARTE_NETTOYAGE": CauseArret.NETTOYAGE,
    "CARTE_CHANGEMENT_SERIE": CauseArret.CHANGEMENT_SERIE,
    "CARTE_ATTENTE_MATIERE": CauseArret.ATTENTE_MATIERE,
    "CARTE_QUALITE": CauseArret.QUALITE_BLOQUANTE,
}


def transformer_valeur(val: Any, rule: SparkplugTagMapping) -> Any:
    """Applique la transformation configurée par l'utilisateur."""
    if rule.transformation == TagTransformation.DIRECT:
        return val

    if rule.transformation == TagTransformation.SCALE_FACTOR:
        try:
            factor = float(rule.formula_param or "1.0")
            return float(val) * factor
        except (ValueError, TypeError):
            return val

    if rule.transformation == TagTransformation.THRESHOLD_STATE:
        try:
            # Ex: param = "0.5" -> val > 0.5 = MARCHE, sinon ARRET
            seuil = float(rule.formula_param or "0.0")
            return StatutMachine.MARCHE if float(val) > seuil else StatutMachine.ARRET
        except (ValueError, TypeError):
            return StatutMachine.MARCHE if bool(val) else StatutMachine.ARRET

    if rule.transformation == TagTransformation.OPERATOR_CARD:
        return str(val).strip().upper()

    return val


def appliquer_payload_au_mes(
    db: Session,
    device: SparkplugDevice,
    payload: SparkplugPayload,
) -> dict[str, Any]:
    """Applique les métriques reçues d'un message DDATA/DBIRTH à la machine MES associée."""
    if not device.machine_id:
        return {"statut": "ignore", "message": "Aucune machine associée à ce device"}

    machine = db.get(Machine, device.machine_id)
    if not machine:
        return {"statut": "erreur", "message": f"Machine #{device.machine_id} introuvable"}

    resultats: dict[str, Any] = {}
    active_mappings = [m for m in device.mappings if m.actif]

    for rule in active_mappings:
        val = payload.get_value(rule.tag_name)
        if val is None:
            continue

        val_transformee = transformer_valeur(val, rule)
        target = rule.target_kpi

        if target == TargetKpi.BONNES_PIECES:
            diff = int(val_transformee) - machine.quantite_bonne
            if diff > 0:
                event_service.enregistrer_evenement(
                    db,
                    machine=machine,
                    type_evenement=TypeEvenementMachine.GOOD_UNIT_PRODUCED,
                    payload={"quantite": diff, "source": "SparkplugB", "tag": rule.tag_name},
                )
            resultats["bonnes_pieces"] = int(val_transformee)

        elif target == TargetKpi.REJETS:
            diff = int(val_transformee) - machine.quantite_rejetee
            if diff > 0:
                event_service.enregistrer_evenement(
                    db,
                    machine=machine,
                    type_evenement=TypeEvenementMachine.SCRAP_UNIT_PRODUCED,
                    payload={"quantite": diff, "cause": "AUTRE", "source": "SparkplugB", "tag": rule.tag_name},
                )
            resultats["rejets"] = int(val_transformee)

        elif target == TargetKpi.CADENCE:
            cadence_val = float(val_transformee)
            if cadence_val > 0:
                cycle_s = round(60.0 / cadence_val, 2)
                machine.temps_cycle_actuel_s = Decimal(str(cycle_s))
                event_service.enregistrer_evenement(
                    db,
                    machine=machine,
                    type_evenement=TypeEvenementMachine.CYCLE_TIME_CHANGED,
                    payload={"temps_cycle_s": cycle_s, "cadence_cpm": cadence_val, "source": "SparkplugB"},
                )
            resultats["cadence"] = cadence_val

        elif target == TargetKpi.STATUT_MACHINE:
            nouveau_statut = val_transformee
            if isinstance(nouveau_statut, str):
                nouveau_statut = StatutMachine(nouveau_statut) if nouveau_statut in StatutMachine.__members__ else machine.statut
            if machine.statut != nouveau_statut:
                type_ev = TypeEvenementMachine.MACHINE_STARTED if nouveau_statut == StatutMachine.MARCHE else TypeEvenementMachine.MACHINE_STOPPED
                event_service.enregistrer_evenement(
                    db,
                    machine=machine,
                    type_evenement=type_ev,
                    payload={"nouveau_statut": nouveau_statut.value, "source": "SparkplugB"},
                )
            resultats["statut"] = str(nouveau_statut)

        elif target == TargetKpi.TEMPERATURE:
            temp = float(val_transformee)
            event_service.enregistrer_evenement(
                db,
                machine=machine,
                type_evenement=TypeEvenementMachine.SENSOR_TAG_UPDATED,
                payload={"tag": "temperature", "valeur": temp, "source": "SparkplugB"},
            )
            resultats["temperature"] = temp

        elif target == TargetKpi.OPERATOR_CARD:
            carte_code = str(val_transformee)
            if carte_code == "CARTE_REPRISE" or carte_code == "REPRISE":
                # Fin de l'arrêt
                if machine.statut == StatutMachine.ARRET or machine.statut == StatutMachine.PANNE:
                    from app.services import simulator_service
                    simulator_service.demarrer(db, machine, ordre_fabrication_id=machine.ordre_fabrication_id)
                resultats["carte_action"] = "reprise_production"
            elif carte_code in CARTE_CAUSE_MAP:
                cause = CARTE_CAUSE_MAP[carte_code]
                from app.services.downtime_service import qualifier_ou_creer_arret
                qualifier_ou_creer_arret(db, machine=machine, cause=cause, commentaire=f"Badgeage carte {carte_code}")
                resultats["carte_action"] = f"arret_qualifie_{cause.value}"

    db.flush()
    broadcast_service.diffuser_machine(db, machine)
    return resultats
