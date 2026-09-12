"""Commandes machine (SCADA) : Nova → automate en Sparkplug B DCMD, avec accusé.

Principe : le MES n'écrit JAMAIS lui-même l'état d'une machine. Une commande
est publiée vers l'automate (DCMD) ; l'automate l'accepte ou la refuse et
publie son nouvel état (DDATA) avec un accusé corrélé (`Command/LastId`).
C'est l'ingestion (`protocols/sparkplug_b/ingestion.py`) qui transforme cet
état en événements MES ; ce module attend l'accusé (délai
`MACHINE_COMMAND_TIMEOUT_S`) et rend la main avec le résultat.

Contrainte transactionnelle (SQLite n'a qu'un écrivain) : n'appelez JAMAIS ces
fonctions en tenant une transaction d'écriture ouverte — l'ingestion doit
pouvoir écrire l'état confirmé pendant l'attente. Chaque fonction ouvre ses
propres sessions courtes ; l'appelant committe ses écritures AVANT l'appel.

Limite connue : le registre des commandes en attente vit en mémoire process —
comme l'hôte MQTT lui-même, il suppose un seul worker backend.
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError, FabricationError, NotFoundError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models import Machine, OrdreFabrication
from app.models.enums import StatutMachine, StatutOF, TypeMaintenance
from app.models.sparkplug import SparkplugDevice
from app.protocols.sparkplug_b.codec import DataType, Metric
from app.protocols.sparkplug_b.contract import (
    CMD_ACTION,
    CMD_ID,
    CMD_ORDER_NUMBER,
    CMD_TARGET_QUANTITY,
    CommandAction,
)
from app.services.line_queue_service import reste_a_produire

logger = get_logger(__name__)


class MachineCommandError(AppError):
    """La commande a été refusée (par le MES avant envoi, ou par l'automate)."""

    status_code = status.HTTP_409_CONFLICT
    code = "machine_command_refused"


class MachineUnreachableError(MachineCommandError):
    """Automate non rattaché, hors ligne, ou broker MQTT indisponible."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "machine_unreachable"


class MachineCommandTimeout(MachineCommandError):
    """Commande publiée mais jamais confirmée par l'automate."""

    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "machine_command_timeout"


# --------------------------------------------------------------------------- #
# Registre des commandes en attente d'accusé
# --------------------------------------------------------------------------- #
@dataclass
class _CommandeEnAttente:
    id: str
    machine_id: int
    action: CommandAction
    contexte: dict
    terminee: threading.Event = field(default_factory=threading.Event)
    acceptee: bool | None = None
    raison: str | None = None


class CommandRegistry:
    """Commandes publiées, en attente de l'accusé de l'automate (thread-safe)."""

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._en_attente: dict[str, _CommandeEnAttente] = {}

    def enregistrer(
        self, machine_id: int, action: CommandAction, contexte: dict
    ) -> _CommandeEnAttente:
        commande = _CommandeEnAttente(
            id=uuid.uuid4().hex, machine_id=machine_id, action=action, contexte=dict(contexte)
        )
        with self._verrou:
            self._en_attente[commande.id] = commande
        return commande

    def contexte(self, commande_id: str) -> dict:
        """Contexte MES de la commande (OF, maintenance…) pour enrichir les
        événements de l'état qu'elle a provoqué. `{}` si inconnue/expirée."""
        with self._verrou:
            commande = self._en_attente.get(commande_id)
        if commande is None:
            return {}
        return {**commande.contexte, "commande_id": commande.id, "commande": commande.action.value}

    def resoudre(self, commande_id: str, acceptee: bool, raison: str | None) -> bool:
        with self._verrou:
            commande = self._en_attente.get(commande_id)
        if commande is None:
            logger.info("commande_accuse_orphelin", commande_id=commande_id)
            return False
        commande.acceptee = acceptee
        commande.raison = raison
        commande.terminee.set()
        return True

    def abandonner(self, commande_id: str) -> None:
        with self._verrou:
            self._en_attente.pop(commande_id, None)

    def __len__(self) -> int:
        with self._verrou:
            return len(self._en_attente)


registry = CommandRegistry()


# --------------------------------------------------------------------------- #
# Dépendances injectables (tests)
# --------------------------------------------------------------------------- #
class CommandPublisher(Protocol):
    @property
    def connected(self) -> bool: ...

    def publish_device_command(
        self, *, group_id: str, edge_node_id: str, device_id: str, metrics: list[Metric]
    ) -> None: ...


SessionFactory = Callable[[], AbstractContextManager[Session]]

_publisher_force: CommandPublisher | None = None
_session_factory: SessionFactory = session_scope


def configurer(
    *, publisher: CommandPublisher | None = None, session_factory: SessionFactory | None = None
) -> None:
    """Remplace l'hôte MQTT et/ou la fabrique de sessions (tests uniquement)."""
    global _publisher_force, _session_factory
    _publisher_force = publisher
    _session_factory = session_factory or session_scope


def _publisher() -> CommandPublisher | None:
    if _publisher_force is not None:
        return _publisher_force
    from app.protocols.sparkplug_b import runtime

    return runtime.get_host()


# --------------------------------------------------------------------------- #
# Envoi
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Cible:
    machine_id: int
    code: str
    group_id: str
    edge_node_id: str
    device_id: str


def _charger(db: Session, machine_id: int) -> tuple[Machine, _Cible]:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise NotFoundError(f"Machine introuvable (id={machine_id}).")
    device = db.execute(
        select(SparkplugDevice).where(SparkplugDevice.machine_id == machine.id)
    ).scalars().first()
    if device is None:
        raise MachineUnreachableError(
            f"{machine.code} n'est rattachée à aucun automate Sparkplug : impossible de la piloter."
        )
    if not device.online:
        raise MachineUnreachableError(
            f"L'automate de {machine.code} ({device.edge_node_id}/{device.device_id}) est hors ligne."
        )
    cible = _Cible(
        machine_id=machine.id,
        code=machine.code,
        group_id=device.group_id,
        edge_node_id=device.edge_node_id,
        device_id=device.device_id,
    )
    return machine, cible


def _envoyer(
    cible: _Cible,
    action: CommandAction,
    *,
    contexte: dict | None = None,
    parametres: tuple[Metric, ...] = (),
) -> None:
    publisher = _publisher()
    if publisher is None or not publisher.connected:
        raise MachineUnreachableError(
            f"Broker MQTT non connecté : impossible de piloter {cible.code}."
        )
    commande = registry.enregistrer(cible.machine_id, action, contexte or {})
    metrics = [
        Metric(CMD_ID, DataType.STRING, commande.id),
        Metric(CMD_ACTION, DataType.STRING, action.value),
        *parametres,
    ]
    delai = get_settings().machine_command_timeout_s
    logger.info(
        "commande_machine_envoyee", machine=cible.code, action=action.value, commande_id=commande.id
    )
    try:
        try:
            publisher.publish_device_command(
                group_id=cible.group_id,
                edge_node_id=cible.edge_node_id,
                device_id=cible.device_id,
                metrics=metrics,
            )
        except ConnectionError as exc:
            raise MachineUnreachableError(
                f"Commande {action.value} non publiée vers {cible.code} : {exc}."
            ) from exc
        if not commande.terminee.wait(delai):
            raise MachineCommandTimeout(
                f"{cible.code} n'a pas confirmé la commande {action.value} en {delai:g} s "
                "(automate déconnecté ou surchargé ?)."
            )
    finally:
        registry.abandonner(commande.id)

    if not commande.acceptee:
        raise MachineCommandError(
            f"{cible.code} a refusé la commande {action.value} : "
            f"{commande.raison or 'motif non précisé'}."
        )
    logger.info("commande_machine_confirmee", machine=cible.code, action=action.value)


# --------------------------------------------------------------------------- #
# Commandes publiques (préconditions MES vérifiées avant tout envoi)
# --------------------------------------------------------------------------- #
def _verifier_of_lancable(db: Session, machine: Machine, of: OrdreFabrication) -> None:
    if of.statut in (StatutOF.TERMINE, StatutOF.ANNULE):
        raise FabricationError(f"L'OF {of.numero} est {of.statut.value} : il ne peut plus être lancé.")
    if of.ligne_production_id is None:
        raise FabricationError(f"L'OF {of.numero} n'a pas de ligne : affectez-lui une ligne d'abord.")
    if of.ligne_production_id != machine.ligne_production_id:
        raise FabricationError(
            f"L'OF {of.numero} est affecté à une autre ligne que {machine.code}."
        )
    ligne = machine.ligne_production
    if all(article.id != of.article_id for article in ligne.articles):
        raise FabricationError(
            f"L'article {of.article.code} n'est pas homologué sur la ligne {ligne.code}."
        )
    autre = db.execute(
        select(Machine.code).where(
            Machine.ordre_fabrication_id == of.id, Machine.id != machine.id
        )
    ).scalar_one_or_none()
    if autre is not None:
        raise FabricationError(f"L'OF {of.numero} est déjà porté par la machine {autre}.")
    if reste_a_produire(of) <= 0:
        raise FabricationError(f"L'OF {of.numero} est déjà soldé (quantité planifiée atteinte).")


def demarrer(machine_id: int, *, ordre_fabrication_id: int | None = None) -> str:
    """Démarre la machine sur un OF (celui qu'elle porte déjà, à défaut)."""
    with _session_factory() as db:
        machine, cible = _charger(db, machine_id)
        if machine.statut in (StatutMachine.PANNE, StatutMachine.MAINTENANCE):
            raise MachineCommandError(
                f"{machine.code} est en {machine.statut.value.lower()} : résolvez l'arrêt avant de la démarrer."
            )
        of_id = ordre_fabrication_id or machine.ordre_fabrication_id
        if of_id is None:
            raise FabricationError(f"Aucun OF à produire sur {machine.code} : précisez l'OF à lancer.")
        if machine.ordre_fabrication_id not in (None, of_id):
            en_cours = db.get(OrdreFabrication, machine.ordre_fabrication_id)
            raise FabricationError(
                f"{machine.code} porte encore l'OF {en_cours.numero if en_cours else '?'} : "
                "préemptez-le ou remettez-le en file avant d'en lancer un autre."
            )
        of = db.get(OrdreFabrication, of_id)
        if of is None:
            raise NotFoundError(f"OF introuvable (id={of_id}).")
        if machine.statut == StatutMachine.MARCHE:
            return f"{machine.code} est déjà en production sur l'OF {of.numero}."
        _verifier_of_lancable(db, machine, of)
        numero, reste = of.numero, reste_a_produire(of)

    _envoyer(
        cible,
        CommandAction.START,
        contexte={"ordre_fabrication_id": of_id},
        parametres=(
            Metric(CMD_ORDER_NUMBER, DataType.STRING, numero),
            Metric(CMD_TARGET_QUANTITY, DataType.INT64, reste),
        ),
    )
    return f"{cible.code} démarrée sur l'OF {numero} ({reste} unités restantes)."


def arreter(machine_id: int, *, commentaire: str | None = None) -> str:
    with _session_factory() as db:
        machine, cible = _charger(db, machine_id)
        if machine.statut == StatutMachine.ARRET:
            raise MachineCommandError(f"{machine.code} est déjà à l'arrêt.")
        if machine.statut in (StatutMachine.PANNE, StatutMachine.MAINTENANCE):
            raise MachineCommandError(
                f"{machine.code} est en {machine.statut.value.lower()} et ne produit pas : "
                "utilisez la résolution d'arrêt."
            )
    _envoyer(
        cible,
        CommandAction.STOP,
        contexte={"comment": commentaire or "Arrêt commandé depuis Nova"},
    )
    return f"{cible.code} arrêtée."


def mettre_en_pause(machine_id: int) -> str:
    with _session_factory() as db:
        machine, cible = _charger(db, machine_id)
        if machine.statut != StatutMachine.MARCHE:
            raise MachineCommandError(f"{machine.code} n'est pas en marche : pause impossible.")
    _envoyer(cible, CommandAction.PAUSE)
    return f"{cible.code} mise en pause."


def resoudre_arret(machine_id: int, *, commentaire: str | None = None) -> str:
    """Clôt l'arrêt en cours : acquittement de défaut (PANNE) ou fin de
    maintenance (MAINTENANCE) — l'automate reprend l'OF chargé s'il en a un."""
    with _session_factory() as db:
        machine, cible = _charger(db, machine_id)
        statut = machine.statut
    if statut == StatutMachine.PANNE:
        action = CommandAction.RESET_FAULT
    elif statut == StatutMachine.MAINTENANCE:
        action = CommandAction.END_MAINTENANCE
    else:
        raise MachineCommandError(
            f"Aucun arrêt à résoudre sur {cible.code} (statut {statut.value})."
        )
    _envoyer(cible, action, contexte={"comment": commentaire} if commentaire else {})
    with _session_factory() as db:
        nouveau = db.get(Machine, machine_id).statut
    return f"Arrêt résolu sur {cible.code} : statut {nouveau.value}."


def demarrer_maintenance(
    machine_id: int, *, type_maintenance: str, description: str | None = None
) -> str:
    try:
        type_ = TypeMaintenance(type_maintenance)
    except ValueError as exc:
        raise MachineCommandError(
            f"Type de maintenance invalide : {type_maintenance} "
            f"(choix : {', '.join(t.value for t in TypeMaintenance)})."
        ) from exc
    with _session_factory() as db:
        machine, cible = _charger(db, machine_id)
        if machine.statut == StatutMachine.MAINTENANCE:
            raise MachineCommandError(f"{machine.code} est déjà en maintenance.")
    _envoyer(
        cible,
        CommandAction.START_MAINTENANCE,
        contexte={"type": type_.value, "description": description},
    )
    return f"Maintenance {type_.value} démarrée sur {cible.code}."
