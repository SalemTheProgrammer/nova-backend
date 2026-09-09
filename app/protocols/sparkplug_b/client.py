"""Client et bus d'ingestion Sparkplug B MQTT (interne & externe)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.machine import Machine
from app.models.sparkplug import SparkplugDevice, SparkplugTagMapping, TagTransformation, TargetKpi
from app.protocols.sparkplug_b import codec, mapper, topic as sp_topic
from app.services import broadcast_service

logger = get_logger(__name__)


class SparkplugBus:
    """Bus d'événements Sparkplug B centralisé.

    Permet d'ingérer des messages depuis un broker MQTT externe ou directement
    depuis le simulateur d'automates/devices virtuels en mémoire.
    """

    def __init__(self) -> None:
        self._listeners: list[Callable[[str, bytes], None]] = []

    def publish(self, topic_str: str, payload_bytes: bytes) -> None:
        """Publie une trame Sparkplug B sur le bus et la traite immédiatement."""
        self.handle_incoming_message(topic_str, payload_bytes)

    def handle_incoming_message(self, topic_str: str, payload_raw: bytes | str | dict) -> dict[str, Any]:
        """Traite une trame Sparkplug B entrante et synchronise l'état MES."""
        parsed_topic = sp_topic.parse_topic(topic_str)
        if not parsed_topic:
            logger.debug("sparkplug_topic_ignore", topic=topic_str)
            return {"statut": "ignore", "raison": "Topic non conforme spBv1.0"}

        try:
            payload = codec.decode_payload(payload_raw)
        except Exception as e:
            logger.warning("sparkplug_decode_failed", topic=topic_str, error=str(e))
            return {"statut": "erreur", "raison": f"Erreur de décodage payload : {e}"}

        msg_type = parsed_topic.message_type
        group_id = parsed_topic.group_id
        edge_node_id = parsed_topic.edge_node_id
        device_id = parsed_topic.device_id

        if not device_id:
            # Message niveau Edge Node (NBIRTH, NDATA, NDEATH)
            logger.info("sparkplug_node_message", type=msg_type, node=edge_node_id)
            return {"statut": "node_traite", "type": msg_type}

        # Traitement niveau Device (DBIRTH, DDATA, DDEATH)
        with session_scope() as db:
            device = db.execute(
                select(SparkplugDevice).where(SparkplugDevice.device_id == device_id)
            ).scalar_one_or_none()

            if not device:
                # Enregistrement automatique du device à la première trame
                machine = db.execute(select(Machine).limit(1)).scalar_one_or_none()
                device = SparkplugDevice(
                    name=f"Device {device_id}",
                    group_id=group_id,
                    edge_node_id=edge_node_id,
                    device_id=device_id,
                    machine_id=machine.id if machine else None,
                    broker_url="internal",
                    online=(msg_type != "DDEATH"),
                )
                db.add(device)
                db.flush()

            if msg_type == "DBIRTH":
                device.online = True
                device.last_birth_at = datetime.utcnow()
                device.last_data_at = datetime.utcnow()

                # Sauvegarde du catalogue des métriques découvertes sur l'automate
                metric_catalog = {
                    m.name: {"dataType": m.dataType, "valeur_initiale": m.value}
                    for m in payload.metrics
                }
                device.available_metrics = metric_catalog

                # Création automatique des règles de base si aucune n'existe encore
                if not device.mappings:
                    self._creer_mappings_par_defaut(db, device, payload)

                db.flush()
                broadcast_service.diffuser(
                    {
                        "type": "sparkplug_device_birth",
                        "device_id": device.device_id,
                        "metrics_count": len(payload.metrics),
                    }
                )
                logger.info("sparkplug_dbirth_traite", device=device_id, metrics=len(payload.metrics))
                return {"statut": "dbirth_traite", "device": device_id, "metrics": len(payload.metrics)}

            elif msg_type == "DDATA":
                device.online = True
                device.last_data_at = datetime.utcnow()
                res = mapper.appliquer_payload_au_mes(db, device, payload)
                db.flush()
                return {"statut": "ddata_traite", "device": device_id, "kpis": res}

            elif msg_type == "DDEATH":
                device.online = False
                db.flush()
                broadcast_service.diffuser(
                    {"type": "sparkplug_device_death", "device_id": device.device_id}
                )
                logger.info("sparkplug_ddeath_traite", device=device_id)
                return {"statut": "ddeath_traite", "device": device_id}

        return {"statut": "inconnu", "type": msg_type}

    def _creer_mappings_par_defaut(
        self,
        db: Session,
        device: SparkplugDevice,
        payload: codec.SparkplugPayload,
    ) -> None:
        """Génère automatiquement les règles de base selon les noms standards des métriques."""
        metric_names = {m.name for m in payload.metrics}

        mappings_suggeres = [
            ("Counters/GoodParts", TargetKpi.BONNES_PIECES, TagTransformation.DIRECT),
            ("Counters/Rejects", TargetKpi.REJETS, TagTransformation.DIRECT),
            ("Cadence/UnitsPerMin", TargetKpi.CADENCE, TagTransformation.DIRECT),
            ("Machine/State", TargetKpi.STATUT_MACHINE, TagTransformation.DIRECT),
            ("Sensors/Temperature", TargetKpi.TEMPERATURE, TagTransformation.DIRECT),
            ("DigitalIn/OperatorCard", TargetKpi.OPERATOR_CARD, TagTransformation.OPERATOR_CARD),
        ]

        for tag, kpi, transf in mappings_suggeres:
            if tag in metric_names:
                mapping = SparkplugTagMapping(
                    device_id=device.id,
                    tag_name=tag,
                    target_kpi=kpi,
                    transformation=transf,
                )
                db.add(mapping)


sparkplug_bus = SparkplugBus()
