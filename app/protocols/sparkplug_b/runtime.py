"""Accès à l'hôte Sparkplug démarré par le lifespan FastAPI (voir `main.py`).

`None` quand MQTT est désactivé (`MQTT_ENABLED=false`, tests) : les appelants
doivent traiter ce cas comme « automates injoignables ».
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.protocols.sparkplug_b.host import SparkplugHost

_host: SparkplugHost | None = None


def set_host(host: SparkplugHost | None) -> None:
    global _host
    _host = host


def get_host() -> SparkplugHost | None:
    return _host
