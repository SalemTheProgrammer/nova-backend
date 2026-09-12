"""Ingestion Sparkplug B : registre des devices et traduction en événements MES.

Appelé par l'unique thread d'ingestion de `SparkplugHost` (jamais en parallèle
avec lui-même). Pour chaque message :

1. validation du topic et décodage protobuf ;
2. suivi de session par edge node — `bdSeq` (naissance / mort) et `seq`
   (0-255) : un trou de séquence ou une donnée reçue sans naissance déclenche
   une demande de re-naissance (NCMD `Node Control/Rebirth`), limitée en
   fréquence ;
3. résolution des alias déclarés à la naissance ;
4. mise à jour du registre `SparkplugDevice` (naissance, rattachement machine,
   règles de mapping par défaut, en / hors ligne) ;
5. traduction des métriques en événements MES (`mapper`) dans UNE transaction ;
6. APRÈS commit seulement : accusé de commande (libère l'appelant qui
   attendait) et diffusion WebSocket — tout lecteur réveillé lit donc l'état
   validé.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import Machine
from app.models.sparkplug import SparkplugDevice
from app.protocols.sparkplug_b import codec, mapper
from app.protocols.sparkplug_b import topic as sp_topic
from app.protocols.sparkplug_b.contract import BD_SEQ
from app.services import broadcast_service
from app.services.machine_command_service import CommandRegistry
from app.services.machine_command_service import registry as registre_commandes

logger = get_logger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]
RebirthRequester = Callable[[str, str], None]

_TYPES_COMMANDE = {"NCMD", "DCMD"}


@dataclass
class _SessionNoeud:
    """Ce que l'hôte sait d'un edge node depuis sa dernière naissance."""

    ne: bool = False  # NBIRTH reçue depuis le démarrage de l'hôte
    bd_seq: int | None = None
    dernier_seq: int | None = None
    alias: dict[str | None, dict[int, str]] = field(default_factory=dict)
    derniere_demande_rebirth: float = float("-inf")


def _en_entier(valeur: object) -> int | None:
    try:
        return int(valeur)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class SparkplugIngestor:
    def __init__(
        self,
        *,
        broker_url: str | None = None,
        session_factory: SessionFactory = session_scope,
        commandes: CommandRegistry = registre_commandes,
        rebirth_requester: RebirthRequester | None = None,
        on_machines_modifiees: Callable[[list[int]], None] | None = None,
        on_evenement: Callable[[dict], None] | None = None,
        intervalle_rebirth_s: float | None = None,
        horloge: Callable[[], float] = time.monotonic,
    ) -> None:
        self._broker_url = broker_url
        self._session_factory = session_factory
        self._commandes = commandes
        self._rebirth_requester = rebirth_requester
        self._on_machines_modifiees = on_machines_modifiees or broadcast_service.diffuser_machines_par_id
        self._on_evenement = on_evenement or broadcast_service.diffuser
        self._intervalle_rebirth_s = (
            intervalle_rebirth_s
            if intervalle_rebirth_s is not None
            else get_settings().sparkplug_rebirth_min_interval_s
        )
        self._horloge = horloge
        self._noeuds: dict[tuple[str, str], _SessionNoeud] = {}

    def set_rebirth_requester(self, requester: RebirthRequester | None) -> None:
        self._rebirth_requester = requester

    def edge_nodes_connus(self) -> list[tuple[str, str]]:
        return list(self._noeuds)

    # ------------------------------------------------------------------ #
    # Point d'entrée
    # ------------------------------------------------------------------ #
    def handle(self, topic: str, brut: bytes) -> None:
        if sp_topic.is_state_topic(topic):
            return
        parsed = sp_topic.parse_topic(topic)
        if parsed is None:
            logger.debug("sparkplug_topic_ignore", topic=topic)
            return
        if parsed.message_type in _TYPES_COMMANDE:
            return
        try:
            payload = codec.decode(brut)
        except codec.PayloadDecodeError as exc:
            logger.warning("sparkplug_payload_illisible", topic=topic, erreur=str(exc))
            return

        noeud = self._noeuds.setdefault((parsed.group_id, parsed.edge_node_id), _SessionNoeud())
        type_ = parsed.message_type

        if type_ == "NBIRTH":
            self._naissance_noeud(parsed, noeud, payload)
            return
        if type_ == "NDEATH":
            self._mort_noeud(parsed, noeud, payload)
            return

        self._suivre_sequence(parsed, noeud, payload)
        if type_ == "NDATA":
            return  # métriques de niveau nœud : non exploitées par le MES
        if type_ == "DDEATH":
            self._mort_device(parsed)
            return
        if type_ == "DBIRTH":
            noeud.alias[parsed.device_id] = {
                m.alias: m.name for m in payload.metrics if m.alias is not None and m.name
            }
        payload = self._resoudre_alias(noeud, parsed.device_id, payload)
        self._message_device(parsed, payload, naissance=type_ == "DBIRTH")

    # ------------------------------------------------------------------ #
    # Session des edge nodes
    # ------------------------------------------------------------------ #
    def _naissance_noeud(
        self, parsed: sp_topic.SparkplugTopic, noeud: _SessionNoeud, payload: codec.Payload
    ) -> None:
        noeud.ne = True
        noeud.bd_seq = _en_entier(payload.value(BD_SEQ))
        noeud.dernier_seq = payload.seq
        noeud.alias = {
            None: {m.alias: m.name for m in payload.metrics if m.alias is not None and m.name}
        }
        logger.info(
            "sparkplug_nbirth",
            groupe=parsed.group_id,
            edge_node=parsed.edge_node_id,
            bd_seq=noeud.bd_seq,
        )

    def _mort_noeud(
        self, parsed: sp_topic.SparkplugTopic, noeud: _SessionNoeud, payload: codec.Payload
    ) -> None:
        bd_seq = _en_entier(payload.value(BD_SEQ))
        if noeud.bd_seq is not None and bd_seq is not None and bd_seq != noeud.bd_seq:
            # Testament d'une session antérieure, livré après la re-connexion.
            logger.info("sparkplug_ndeath_obsolete", edge_node=parsed.edge_node_id, bd_seq=bd_seq)
            return
        noeud.ne = False
        noeud.dernier_seq = None
        noeud.alias = {}
        with self._session_factory() as db:
            devices = db.execute(
                select(SparkplugDevice).where(
                    SparkplugDevice.group_id == parsed.group_id,
                    SparkplugDevice.edge_node_id == parsed.edge_node_id,
                )
            ).scalars().all()
            for device in devices:
                device.online = False
            machines = [d.machine_id for d in devices if d.machine_id is not None]
            ids_devices = [d.device_id for d in devices]
        logger.warning("sparkplug_ndeath", edge_node=parsed.edge_node_id, devices=ids_devices)
        for device_id in ids_devices:
            self._on_evenement({"type": "sparkplug_device", "device_id": device_id, "online": False})
        if machines:
            self._on_machines_modifiees(machines)

    def _suivre_sequence(
        self, parsed: sp_topic.SparkplugTopic, noeud: _SessionNoeud, payload: codec.Payload
    ) -> None:
        if not noeud.ne:
            self._demander_rebirth(parsed, noeud, "donnée reçue sans NBIRTH")
            return
        if payload.seq is None:
            self._demander_rebirth(parsed, noeud, "message sans numéro de séquence")
            return
        attendu = (noeud.dernier_seq + 1) % 256 if noeud.dernier_seq is not None else payload.seq
        noeud.dernier_seq = payload.seq
        if payload.seq != attendu:
            logger.warning(
                "sparkplug_rupture_sequence",
                edge_node=parsed.edge_node_id,
                attendu=attendu,
                recu=payload.seq,
            )
            self._demander_rebirth(parsed, noeud, "rupture de séquence")

    def _demander_rebirth(
        self, parsed: sp_topic.SparkplugTopic, noeud: _SessionNoeud, raison: str
    ) -> None:
        if self._rebirth_requester is None:
            return
        maintenant = self._horloge()
        if maintenant - noeud.derniere_demande_rebirth < self._intervalle_rebirth_s:
            return
        noeud.derniere_demande_rebirth = maintenant
        logger.info("sparkplug_rebirth_necessaire", edge_node=parsed.edge_node_id, raison=raison)
        try:
            self._rebirth_requester(parsed.group_id, parsed.edge_node_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sparkplug_rebirth_echec", edge_node=parsed.edge_node_id, erreur=str(exc))

    @staticmethod
    def _resoudre_alias(
        noeud: _SessionNoeud, device_id: str | None, payload: codec.Payload
    ) -> codec.Payload:
        table = noeud.alias.get(device_id) or {}
        if not table or all(m.name is not None for m in payload.metrics):
            return payload
        metrics = [
            codec.Metric(
                name=m.name if m.name is not None else table.get(m.alias or -1),
                datatype=m.datatype,
                value=m.value,
                alias=m.alias,
                timestamp=m.timestamp,
            )
            for m in payload.metrics
        ]
        return codec.Payload(metrics=metrics, timestamp=payload.timestamp, seq=payload.seq)

    # ------------------------------------------------------------------ #
    # Devices
    # ------------------------------------------------------------------ #
    def _message_device(
        self, parsed: sp_topic.SparkplugTopic, payload: codec.Payload, *, naissance: bool
    ) -> None:
        ack = mapper.lire_ack(payload)
        try:
            with self._session_factory() as db:
                device, nouveau_en_ligne = self._device(db, parsed, payload, naissance=naissance)
                resultat = mapper.appliquer(
                    db, device, payload, naissance=naissance, commandes=self._commandes
                )
                device_id, machine_id = device.device_id, resultat.machine_id
        except Exception:
            # L'état n'a pas pu être enregistré : l'appelant ne doit pas croire
            # la commande réussie.
            if ack is not None:
                self._commandes.resoudre(ack[0], False, "erreur d'enregistrement côté MES")
            raise

        if resultat.ack is not None:
            self._commandes.resoudre(*resultat.ack)
        if machine_id is not None and (resultat.evenements or naissance or nouveau_en_ligne):
            self._on_machines_modifiees([machine_id])
        if naissance or nouveau_en_ligne:
            self._on_evenement({"type": "sparkplug_device", "device_id": device_id, "online": True})

    def _device(
        self, db: Session, parsed: sp_topic.SparkplugTopic, payload: codec.Payload, *, naissance: bool
    ) -> tuple[SparkplugDevice, bool]:
        """Device du registre (créé si inconnu). Renvoie (device, vient_de_passer_en_ligne)."""
        maintenant = datetime.utcnow()
        device = db.execute(
            select(SparkplugDevice).where(SparkplugDevice.device_id == parsed.device_id)
        ).scalar_one_or_none()
        if device is None:
            device = SparkplugDevice(
                name=parsed.device_id,
                group_id=parsed.group_id,
                edge_node_id=parsed.edge_node_id,
                device_id=parsed.device_id,
                broker_url=self._broker_url,
                online=False,
            )
            db.add(device)
            db.flush()
            logger.info("sparkplug_device_decouvert", device=parsed.device_id)

        etait_en_ligne = device.online
        device.group_id = parsed.group_id
        device.edge_node_id = parsed.edge_node_id
        if self._broker_url:
            device.broker_url = self._broker_url
        device.online = True
        device.last_data_at = maintenant

        if naissance:
            device.last_birth_at = maintenant
            device.available_metrics = {
                m.name: {"datatype": m.datatype.name, "valeur": mapper.valeur_json(m.value)}
                for m in payload.metrics
                if m.name
            }
            self._rattacher_machine(db, device)
            if not device.mappings:
                crees = mapper.creer_mappings_par_defaut(db, device, payload)
                logger.info("sparkplug_mappings_par_defaut", device=device.device_id, regles=crees)
        return device, not etait_en_ligne

    @staticmethod
    def _rattacher_machine(db: Session, device: SparkplugDevice) -> None:
        """Convention : `device_id` = code machine MES. Rattachement automatique si
        la machine existe et n'est pas déjà pilotée par un autre device."""
        if device.machine_id is not None:
            return
        machine = db.execute(
            select(Machine).where(Machine.code == device.device_id, Machine.actif.is_(True))
        ).scalar_one_or_none()
        if machine is None:
            logger.info("sparkplug_device_sans_machine", device=device.device_id)
            return
        deja = db.execute(
            select(SparkplugDevice.id).where(
                SparkplugDevice.machine_id == machine.id, SparkplugDevice.id != device.id
            )
        ).first()
        if deja is not None:
            logger.warning("sparkplug_machine_deja_rattachee", machine=machine.code)
            return
        device.machine_id = machine.id
        logger.info("sparkplug_device_rattache", device=device.device_id, machine=machine.code)

    def _mort_device(self, parsed: sp_topic.SparkplugTopic) -> None:
        with self._session_factory() as db:
            device = db.execute(
                select(SparkplugDevice).where(SparkplugDevice.device_id == parsed.device_id)
            ).scalar_one_or_none()
            if device is None:
                return
            device.online = False
            machine_id = device.machine_id
        logger.warning("sparkplug_ddeath", device=parsed.device_id)
        self._on_evenement({"type": "sparkplug_device", "device_id": parsed.device_id, "online": False})
        if machine_id is not None:
            self._on_machines_modifiees([machine_id])
