"""Simulateur d'automates industriels Sparkplug B (Edge Node & Devices).

Permet de simuler des trames réelles DBIRTH, DDATA, DDEATH, des micro-arrêts
automatiques non planifiés et le badgeage de cartes opérateurs RFID.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.enums import CauseArret, StatutMachine
from app.models.machine import Machine
from app.models.sparkplug import SparkplugDevice
from app.protocols.sparkplug_b.client import sparkplug_bus
from app.protocols.sparkplug_b.codec import SparkplugMetric, SparkplugPayload
from app.protocols.sparkplug_b.topic import SparkplugTopic
from app.services.downtime_service import detecter_arret_non_planifie

logger = get_logger(__name__)


class VirtualSparkplugSimulator:
    """Simulateur d'automate industriel Sparkplug B."""

    def __init__(
        self,
        group_id: str = "Tunisie_Industrie",
        edge_node_id: str = "Edge_Ligne_01",
        device_id: str = "Automate_Blister_01",
    ) -> None:
        self.group_id = group_id
        self.edge_node_id = edge_node_id
        self.device_id = device_id
        self.sequence = 0
        self._loop_task: asyncio.Task[None] | None = None
        self._running = False

    def _next_seq(self) -> int:
        seq = self.sequence
        self.sequence = (self.sequence + 1) % 256
        return seq

    def publish_dbirth(self) -> dict[str, Any]:
        """Publie une trame de naissance DBIRTH avec le catalogue complet des tags."""
        topic = SparkplugTopic(
            group_id=self.group_id,
            message_type="DBIRTH",
            edge_node_id=self.edge_node_id,
            device_id=self.device_id,
        )

        with session_scope() as db:
            machine = db.execute(select(Machine).limit(1)).scalar_one_or_none()
            good_parts = machine.quantite_bonne if machine else 1250
            rejects = machine.quantite_rejetee if machine else 42
            cadence = (
                float(60.0 / float(machine.temps_cycle_actuel_s))
                if machine and machine.temps_cycle_actuel_s and machine.temps_cycle_actuel_s > 0
                else 45.0
            )

        payload = SparkplugPayload(
            timestamp=int(time.time() * 1000),
            seq=self._next_seq(),
            metrics=[
                SparkplugMetric("Counters/GoodParts", "Int32", good_parts),
                SparkplugMetric("Counters/Rejects", "Int32", rejects),
                SparkplugMetric("Cadence/UnitsPerMin", "Float", round(cadence, 1)),
                SparkplugMetric("Machine/State", "String", "MARCHE"),
                SparkplugMetric("Sensors/Temperature", "Float", 23.5),
                SparkplugMetric("Sensors/Vibration", "Float", 0.85),
                SparkplugMetric("Sensors/MotorCurrent", "Float", 5.2),
                SparkplugMetric("DigitalIn/OperatorCard", "String", ""),
                SparkplugMetric("Protocol/Standard", "String", "ISO/IEC 20237"),
            ],
        )

        res = sparkplug_bus.handle_incoming_message(topic.to_topic_string(), payload)
        logger.info("sparkplug_sim_dbirth_sent", device=self.device_id, metrics=len(payload.metrics))
        return {
            "topic": topic.to_topic_string(),
            "metrics_count": len(payload.metrics),
            "result": res,
        }

    def publish_ddata(
        self,
        good_increment: int = 2,
        reject_increment: int = 0,
        cadence_cpm: float = 48.0,
        temperature: float = 24.2,
        operator_card: str = "",
    ) -> dict[str, Any]:
        """Publie une trame de télémétrie DDATA."""
        topic = SparkplugTopic(
            group_id=self.group_id,
            message_type="DDATA",
            edge_node_id=self.edge_node_id,
            device_id=self.device_id,
        )

        with session_scope() as db:
            machine = db.execute(select(Machine).limit(1)).scalar_one_or_none()
            current_good = (machine.quantite_bonne if machine else 1000) + good_increment
            current_rejects = (machine.quantite_rejetee if machine else 20) + reject_increment

        metrics: list[SparkplugMetric] = [
            SparkplugMetric("Counters/GoodParts", "Int32", current_good),
            SparkplugMetric("Counters/Rejects", "Int32", current_rejects),
            SparkplugMetric("Cadence/UnitsPerMin", "Float", cadence_cpm),
            SparkplugMetric("Machine/State", "String", "MARCHE" if cadence_cpm > 0 else "ARRET"),
            SparkplugMetric("Sensors/Temperature", "Float", temperature),
        ]

        if operator_card:
            metrics.append(SparkplugMetric("DigitalIn/OperatorCard", "String", operator_card))

        payload = SparkplugPayload(
            timestamp=int(time.time() * 1000),
            seq=self._next_seq(),
            metrics=metrics,
        )

        res = sparkplug_bus.handle_incoming_message(topic.to_topic_string(), payload)
        return {
            "topic": topic.to_topic_string(),
            "payload": payload.to_dict(),
            "result": res,
        }

    def trigger_unplanned_stop(self) -> dict[str, Any]:
        """Simule un arrêt non planifié : cadence à zéro alors que la machine était active."""
        ddata_res = self.publish_ddata(good_increment=0, reject_increment=0, cadence_cpm=0.0)

        with session_scope() as db:
            machine = db.execute(select(Machine).limit(1)).scalar_one_or_none()
            if machine:
                arret = detecter_arret_non_planifie(db, machine)
                return {
                    "statut": "arret_non_planifie_declenche",
                    "machine": machine.nom,
                    "arret_id": arret.id if arret else None,
                    "cause": arret.cause.value if arret else "MICRO_ARRET",
                    "sparkplug_event": ddata_res,
                }

        return {"statut": "erreur", "message": "Aucune machine trouvée"}

    def swipe_operator_card(self, card_code: str) -> dict[str, Any]:
        """Simule le passage d'une carte RFID opérateur pour qualifier ou clore un arrêt."""
        topic = SparkplugTopic(
            group_id=self.group_id,
            message_type="DDATA",
            edge_node_id=self.edge_node_id,
            device_id=self.device_id,
        )

        payload = SparkplugPayload(
            timestamp=int(time.time() * 1000),
            seq=self._next_seq(),
            metrics=[
                SparkplugMetric("DigitalIn/OperatorCard", "String", card_code),
            ],
        )

        res = sparkplug_bus.handle_incoming_message(topic.to_topic_string(), payload)
        logger.info("sparkplug_card_swiped", card=card_code, result=res)
        return {
            "topic": topic.to_topic_string(),
            "carte": card_code,
            "resultat": res,
        }

    async def _loop_worker(self) -> None:
        """Boucle de télémétrie continue en arrière-plan."""
        logger.info("sparkplug_sim_loop_started")
        tick_count = 0
        while self._running:
            try:
                tick_count += 1
                temp = round(23.5 + (tick_count % 5) * 0.3, 1)
                reject = 1 if tick_count % 12 == 0 else 0
                self.publish_ddata(good_increment=3, reject_increment=reject, cadence_cpm=48.5, temperature=temp)
            except Exception as e:
                logger.error("sparkplug_sim_loop_error", error=str(e))
            await asyncio.sleep(2.0)

    def start_live_stream(self) -> dict[str, Any]:
        """Démarre l'émission en continu toutes les 2 secondes."""
        if self._running:
            return {"statut": "deja_actif", "message": "Simulateur Sparkplug B déjà en cours"}

        self._running = True
        self.publish_dbirth()
        try:
            loop = asyncio.get_running_loop()
            self._loop_task = loop.create_task(self._loop_worker())
        except RuntimeError:
            pass
        return {"statut": "demarre", "device": self.device_id, "intervalle_s": 2.0}

    def stop_live_stream(self) -> dict[str, Any]:
        """Arrête l'émission en continu."""
        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            self._loop_task = None
        return {"statut": "arrete", "device": self.device_id}

    def is_running(self) -> bool:
        return self._running


sparkplug_simulator = VirtualSparkplugSimulator()
