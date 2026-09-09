"""Encodeur et décodeur de payload Sparkplug B conforme ISO/IEC 20237."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any
import orjson

SPARKPLUG_DATATYPES = {
    "Int8",
    "Int16",
    "Int32",
    "Int64",
    "UInt8",
    "UInt16",
    "UInt32",
    "UInt64",
    "Float",
    "Double",
    "Boolean",
    "String",
    "DateTime",
    "Bytes",
}


@dataclass
class SparkplugMetric:
    name: str
    dataType: str
    value: Any
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    alias: int | None = None


@dataclass
class SparkplugPayload:
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000))
    seq: int = 0
    metrics: list[SparkplugMetric] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "seq": self.seq,
            "metrics": [asdict(m) for m in self.metrics],
        }

    def to_bytes(self) -> bytes:
        return orjson.dumps(self.to_dict())

    def get_metric(self, name: str) -> SparkplugMetric | None:
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def get_value(self, name: str, default: Any = None) -> Any:
        m = self.get_metric(name)
        return m.value if m is not None else default


def decode_payload(raw: bytes | str | dict | SparkplugPayload) -> SparkplugPayload:
    """Décode un payload Sparkplug B depuis du binaire JSON ou un dictionnaire."""
    if isinstance(raw, SparkplugPayload):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        data = orjson.loads(raw)
    elif isinstance(raw, str):
        data = orjson.loads(raw.encode("utf-8"))
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError(f"Type de payload non supporté : {type(raw)}")

    timestamp = data.get("timestamp", int(time.time() * 1000))
    seq = data.get("seq", 0)
    raw_metrics = data.get("metrics", [])

    metrics: list[SparkplugMetric] = []
    for rm in raw_metrics:
        if isinstance(rm, dict) and "name" in rm and "value" in rm:
            metrics.append(
                SparkplugMetric(
                    name=rm["name"],
                    dataType=rm.get("dataType", "String"),
                    value=rm["value"],
                    timestamp=rm.get("timestamp", timestamp),
                    alias=rm.get("alias"),
                )
            )

    return SparkplugPayload(timestamp=timestamp, seq=seq, metrics=metrics)
