"""Topics Sparkplug B (spBv1.0) : analyse, construction, abonnements.

Messages des edge nodes : spBv1.0/<group_id>/<message_type>/<edge_node_id>[/<device_id>]
État de l'hôte (Sparkplug 3.0) : spBv1.0/STATE/<host_id>
"""
from __future__ import annotations

from dataclasses import dataclass

NAMESPACE = "spBv1.0"

MESSAGE_TYPES = {
    "NBIRTH",
    "NDATA",
    "NDEATH",
    "NCMD",
    "DBIRTH",
    "DDATA",
    "DDEATH",
    "DCMD",
}

_CARACTERES_INTERDITS = frozenset("/+#")


def valider_identifiant(valeur: str, *, nom: str = "identifiant") -> str:
    """Un identifiant Sparkplug (groupe, edge node, device, hôte) ne peut être
    vide ni contenir les caractères réservés MQTT `/`, `+`, `#`."""
    if not valeur or any(c in _CARACTERES_INTERDITS for c in valeur):
        raise ValueError(f"{nom} Sparkplug invalide : {valeur!r} (vide ou contient / + #).")
    return valeur


@dataclass(frozen=True)
class SparkplugTopic:
    group_id: str
    message_type: str
    edge_node_id: str
    device_id: str | None = None
    namespace: str = NAMESPACE

    @property
    def is_device_message(self) -> bool:
        return self.message_type.startswith("D") and self.device_id is not None

    @property
    def is_node_message(self) -> bool:
        return self.message_type.startswith("N")

    def to_topic_string(self) -> str:
        return build_topic(
            group_id=self.group_id,
            message_type=self.message_type,
            edge_node_id=self.edge_node_id,
            device_id=self.device_id,
        )


def parse_topic(topic: str) -> SparkplugTopic | None:
    """Analyse un topic Sparkplug B. Renvoie None si le topic n'est pas conforme."""
    parts = topic.strip("/").split("/")
    if len(parts) < 4 or len(parts) > 5:
        return None

    namespace, group_id, message_type, edge_node_id = parts[:4]
    if namespace != NAMESPACE or message_type not in MESSAGE_TYPES:
        return None

    device_id = parts[4] if len(parts) == 5 else None
    if message_type.startswith("D") and not device_id:
        return None
    return SparkplugTopic(
        namespace=namespace,
        group_id=group_id,
        message_type=message_type,
        edge_node_id=edge_node_id,
        device_id=device_id,
    )


def build_topic(
    group_id: str,
    message_type: str,
    edge_node_id: str,
    device_id: str | None = None,
) -> str:
    """Construit un topic Sparkplug B standard."""
    if message_type not in MESSAGE_TYPES:
        raise ValueError(f"Type de message Sparkplug B invalide : {message_type}")
    valider_identifiant(group_id, nom="group_id")
    valider_identifiant(edge_node_id, nom="edge_node_id")
    if device_id:
        valider_identifiant(device_id, nom="device_id")
        return f"{NAMESPACE}/{group_id}/{message_type}/{edge_node_id}/{device_id}"
    return f"{NAMESPACE}/{group_id}/{message_type}/{edge_node_id}"


def state_topic(host_id: str) -> str:
    return f"{NAMESPACE}/STATE/{valider_identifiant(host_id, nom='host_id')}"


def is_state_topic(topic: str) -> bool:
    return topic.startswith(f"{NAMESPACE}/STATE/")


def subscription_filters(group_id: str) -> list[str]:
    """Filtres d'abonnement de l'hôte : tout ce que publient les edge nodes du
    groupe, sauf les commandes (NCMD/DCMD, émises par l'hôte lui-même).
    `group_id="+"` écoute tous les groupes."""
    if group_id != "+":
        valider_identifiant(group_id, nom="group_id")
    noeud = [f"{NAMESPACE}/{group_id}/{t}/+" for t in ("NBIRTH", "NDEATH", "NDATA")]
    device = [f"{NAMESPACE}/{group_id}/{t}/+/+" for t in ("DBIRTH", "DDEATH", "DDATA")]
    return noeud + device
