"""Hôte Sparkplug B de Nova (« Primary Host Application ») sur un broker MQTT.

Responsabilités :
- tenir la session MQTT (reconnexion automatique, identifiants/TLS optionnels) ;
- publier l'état de l'hôte `spBv1.0/STATE/<host_id>` (retenu, testament
  « offline ») : les edge nodes attendent cet état pour publier leurs
  naissances et re-naissent quand l'hôte revient (Sparkplug 3.0) ;
- s'abonner aux messages NBIRTH/NDATA/NDEATH/DBIRTH/DDATA/DDEATH du groupe ;
- publier les commandes DCMD (pilotage machine) et NCMD (re-naissance).

Le callback réseau de paho ne fait AUCUN travail : les messages sont posés
dans une file consommée par un unique thread d'ingestion. Le réseau reste
réactif (keepalive) et les écritures SQLite sont sérialisées dans l'ordre
d'arrivée.
"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from app.core.config import Settings
from app.core.logging import get_logger
from app.protocols.sparkplug_b import codec
from app.protocols.sparkplug_b import topic as sp_topic
from app.protocols.sparkplug_b.contract import NODE_REBIRTH
from app.protocols.sparkplug_b.ingestion import SparkplugIngestor

logger = get_logger(__name__)

TAILLE_FILE_MAX = 10_000
QOS_ETAT = 1  # STATE : QoS 1 + retenu (spécification)
QOS_COMMANDE = 0  # NCMD / DCMD : QoS 0 (spécification)
QOS_ABONNEMENT = 1


@dataclass(frozen=True, slots=True)
class _Message:
    topic: str
    payload: bytes


class SparkplugHost:
    def __init__(self, settings: Settings, ingestor: SparkplugIngestor | None = None) -> None:
        self._settings = settings
        self._ingestor = ingestor or SparkplugIngestor(broker_url=self.broker_url(settings))
        self._ingestor.set_rebirth_requester(self.request_rebirth)
        self._file: queue.Queue[_Message | None] = queue.Queue(maxsize=TAILLE_FILE_MAX)
        self._worker: threading.Thread | None = None
        self._connecte = threading.Event()
        self._derniere_erreur: str | None = None
        self._etat_topic = sp_topic.state_topic(settings.sparkplug_host_id)
        # Sparkplug 3.0 : le testament et la naissance STATE portent le même horodatage.
        self._etat_horodatage = codec.now_ms()
        self._client = self._construire_client()

    # ------------------------------------------------------------------ #
    # État
    # ------------------------------------------------------------------ #
    @staticmethod
    def broker_url(settings: Settings) -> str:
        schema = "mqtts" if settings.mqtt_tls else "mqtt"
        return f"{schema}://{settings.mqtt_host}:{settings.mqtt_port}"

    @property
    def connected(self) -> bool:
        return self._connecte.is_set()

    def status(self) -> dict:
        return {
            "enabled": True,
            "connected": self.connected,
            "broker": self.broker_url(self._settings),
            "group_id": self._settings.sparkplug_group_id,
            "host_id": self._settings.sparkplug_host_id,
            "last_error": self._derniere_erreur,
            "queue_depth": self._file.qsize(),
            "edge_nodes": len(self._ingestor.edge_nodes_connus()),
        }

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._worker = threading.Thread(
            target=self._consommer, name="sparkplug-ingestion", daemon=True
        )
        self._worker.start()
        self._client.connect_async(
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            keepalive=self._settings.mqtt_keepalive_s,
        )
        self._client.loop_start()
        logger.info(
            "sparkplug_hote_demarre",
            broker=self.broker_url(self._settings),
            groupe=self._settings.sparkplug_group_id,
            host_id=self._settings.sparkplug_host_id,
        )

    def stop(self) -> None:
        if self.connected:
            # Déconnexion propre : le testament n'est pas publié par le broker,
            # on annonce donc nous-mêmes l'état « offline ».
            info = self._client.publish(
                self._etat_topic, self._etat(online=False), qos=QOS_ETAT, retain=True
            )
            info.wait_for_publish(timeout=2)
        self._client.disconnect()
        self._client.loop_stop()
        self._file.put(None)
        if self._worker is not None:
            self._worker.join(timeout=5)
        logger.info("sparkplug_hote_arrete")

    # ------------------------------------------------------------------ #
    # Publication (commandes)
    # ------------------------------------------------------------------ #
    def publish_device_command(
        self, *, group_id: str, edge_node_id: str, device_id: str, metrics: list[codec.Metric]
    ) -> None:
        topic = sp_topic.build_topic(
            group_id=group_id, message_type="DCMD", edge_node_id=edge_node_id, device_id=device_id
        )
        self._publier(topic, codec.Payload(metrics=metrics))

    def request_rebirth(self, group_id: str, edge_node_id: str) -> None:
        topic = sp_topic.build_topic(group_id=group_id, message_type="NCMD", edge_node_id=edge_node_id)
        self._publier(
            topic,
            codec.Payload(metrics=[codec.Metric(NODE_REBIRTH, codec.DataType.BOOLEAN, True)]),
        )
        logger.info("sparkplug_rebirth_demandee", groupe=group_id, edge_node=edge_node_id)

    def request_rebirth_all(self) -> int:
        """Demande une re-naissance à chaque edge node connu. Renvoie leur nombre."""
        noeuds = self._ingestor.edge_nodes_connus()
        for group_id, edge_node_id in noeuds:
            self.request_rebirth(group_id, edge_node_id)
        return len(noeuds)

    def _publier(self, topic: str, payload: codec.Payload) -> None:
        if not self.connected:
            raise ConnectionError(f"broker MQTT non connecté ({self.broker_url(self._settings)})")
        info = self._client.publish(topic, codec.encode(payload), qos=QOS_COMMANDE)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"publication MQTT refusée ({mqtt.error_string(info.rc)})")

    # ------------------------------------------------------------------ #
    # Client paho
    # ------------------------------------------------------------------ #
    def _construire_client(self) -> mqtt.Client:
        s = self._settings
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=s.mqtt_client_id,
            clean_session=True,  # exigé par Sparkplug 3.0 en MQTT 3.1.1
            protocol=mqtt.MQTTv311,
        )
        if s.mqtt_username:
            client.username_pw_set(s.mqtt_username, s.mqtt_password or None)
        if s.mqtt_tls:
            client.tls_set(ca_certs=s.mqtt_tls_ca_file or None)
        client.will_set(self._etat_topic, self._etat(online=False), qos=QOS_ETAT, retain=True)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _etat(self, *, online: bool) -> bytes:
        return json.dumps({"online": online, "timestamp": self._etat_horodatage}).encode()

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:  # noqa: ANN001
        if reason_code.is_failure:
            self._derniere_erreur = f"connexion refusée : {reason_code}"
            logger.error("sparkplug_connexion_refusee", raison=str(reason_code))
            return
        filtres = sp_topic.subscription_filters(self._settings.sparkplug_group_id)
        client.subscribe([(filtre, QOS_ABONNEMENT) for filtre in filtres])
        # Sparkplug 3.0 : STATE « online » APRÈS les abonnements, pour ne rater
        # aucune naissance déclenchée par cette annonce.
        client.publish(self._etat_topic, self._etat(online=True), qos=QOS_ETAT, retain=True)
        self._derniere_erreur = None
        self._connecte.set()
        logger.info("sparkplug_connecte", broker=self.broker_url(self._settings), filtres=filtres)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:  # noqa: ANN001
        self._connecte.clear()
        if reason_code.is_failure:
            self._derniere_erreur = f"déconnecté : {reason_code}"
            logger.warning("sparkplug_deconnecte", raison=str(reason_code))

    def _on_message(self, _client, _userdata, message) -> None:  # noqa: ANN001
        try:
            self._file.put_nowait(_Message(message.topic, bytes(message.payload)))
        except queue.Full:
            logger.error("sparkplug_file_pleine", topic=message.topic)

    def _consommer(self) -> None:
        while True:
            item = self._file.get()
            if item is None:
                return
            try:
                self._ingestor.handle(item.topic, item.payload)
            except Exception:  # noqa: BLE001 — un message fautif ne doit pas tuer l'ingestion
                logger.exception("sparkplug_ingestion_echec", topic=item.topic)
