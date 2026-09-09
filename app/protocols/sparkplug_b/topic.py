"""Parser et formateur de topics Sparkplug B (spBv1.0).

Structure standard:
spBv1.0/<group_id>/<message_type>/<edge_node_id>/[<device_id>]
"""
from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class SparkplugTopic:
    group_id: str
    message_type: str
    edge_node_id: str
    device_id: str | None = None
    namespace: str = "spBv1.0"

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
    """Parse un topic MQTT Sparkplug B. Retourne None si le topic n'est pas conforme."""
    parts = topic.strip("/").split("/")
    if len(parts) < 4 or len(parts) > 5:
        return None

    namespace, group_id, message_type, edge_node_id = parts[:4]
    if namespace != "spBv1.0" or message_type not in MESSAGE_TYPES:
        return None

    device_id = parts[4] if len(parts) == 5 else None
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
    """Construit un topic MQTT Sparkplug B standard."""
    if message_type not in MESSAGE_TYPES:
        raise ValueError(f"Type de message Sparkplug B invalide : {message_type}")

    if device_id:
        return f"spBv1.0/{group_id}/{message_type}/{edge_node_id}/{device_id}"
    return f"spBv1.0/{group_id}/{message_type}/{edge_node_id}"
