"""Encodage / décodage des payloads Sparkplug B (protobuf).

Le schéma est `sparkplug_b.proto` (sous-ensemble compatible Eclipse Tahu). Ce
module isole le code généré : le reste de l'application ne manipule que les
dataclasses `Payload` / `Metric` ci-dessous.

Fichier dupliqué à l'identique dans `simulator/nova_sim/sparkplug/` (import
relatif, aucune dépendance au reste de l'application) — voir
`tests/test_contract_sync.py`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum

from google.protobuf.message import DecodeError

from . import sparkplug_b_pb2 as pb

MetricValue = int | float | bool | str | bytes | None


class DataType(IntEnum):
    """Types de données Sparkplug B (valeurs fixées par la spécification)."""

    UNKNOWN = 0
    INT8 = 1
    INT16 = 2
    INT32 = 3
    INT64 = 4
    UINT8 = 5
    UINT16 = 6
    UINT32 = 7
    UINT64 = 8
    FLOAT = 9
    DOUBLE = 10
    BOOLEAN = 11
    STRING = 12
    DATETIME = 13
    TEXT = 14
    UUID = 15
    BYTES = 17


_INT32_SIGNES = frozenset({DataType.INT8, DataType.INT16, DataType.INT32})
_INT32_NON_SIGNES = frozenset({DataType.UINT8, DataType.UINT16, DataType.UINT32})
_TEXTES = frozenset({DataType.STRING, DataType.TEXT, DataType.UUID})


class PayloadDecodeError(ValueError):
    """Trame reçue illisible (pas un payload Sparkplug B protobuf)."""


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class Metric:
    name: str | None
    datatype: DataType
    value: MetricValue
    alias: int | None = None
    timestamp: int | None = None


@dataclass(slots=True)
class Payload:
    metrics: list[Metric] = field(default_factory=list)
    timestamp: int = field(default_factory=now_ms)
    seq: int | None = None

    def metric(self, name: str) -> Metric | None:
        for metric in self.metrics:
            if metric.name == name:
                return metric
        return None

    def value(self, name: str, default: MetricValue = None) -> MetricValue:
        metric = self.metric(name)
        return metric.value if metric is not None else default

    def as_dict(self) -> dict[str, MetricValue]:
        """{nom: valeur} des métriques nommées (la dernière occurrence gagne)."""
        return {m.name: m.value for m in self.metrics if m.name is not None}


def encode(payload: Payload) -> bytes:
    message = pb.Payload()
    message.timestamp = payload.timestamp
    if payload.seq is not None:
        message.seq = payload.seq
    for metric in payload.metrics:
        pm = message.metrics.add()
        if metric.name is not None:
            pm.name = metric.name
        if metric.alias is not None:
            pm.alias = metric.alias
        pm.timestamp = metric.timestamp if metric.timestamp is not None else payload.timestamp
        pm.datatype = int(metric.datatype)
        _ecrire_valeur(pm, metric.datatype, metric.value)
    return message.SerializeToString()


def decode(data: bytes) -> Payload:
    message = pb.Payload()
    try:
        message.ParseFromString(data)
    except DecodeError as exc:
        raise PayloadDecodeError(str(exc)) from exc
    metrics = [
        Metric(
            name=pm.name if pm.HasField("name") else None,
            datatype=_datatype(pm.datatype),
            value=_lire_valeur(pm),
            alias=pm.alias if pm.HasField("alias") else None,
            timestamp=pm.timestamp if pm.HasField("timestamp") else None,
        )
        for pm in message.metrics
    ]
    return Payload(
        metrics=metrics,
        timestamp=message.timestamp if message.HasField("timestamp") else now_ms(),
        seq=message.seq if message.HasField("seq") else None,
    )


def _datatype(valeur: int) -> DataType:
    try:
        return DataType(valeur)
    except ValueError:
        return DataType.UNKNOWN


def _ecrire_valeur(pm: pb.Payload.Metric, datatype: DataType, value: MetricValue) -> None:
    if value is None:
        pm.is_null = True
        return
    if datatype in _INT32_SIGNES:
        pm.int_value = int(value) & 0xFFFFFFFF  # complément à deux sur 32 bits (spécification)
    elif datatype in _INT32_NON_SIGNES:
        pm.int_value = int(value)
    elif datatype == DataType.INT64:
        pm.long_value = int(value) & 0xFFFFFFFFFFFFFFFF
    elif datatype in (DataType.UINT64, DataType.DATETIME):
        pm.long_value = int(value)
    elif datatype == DataType.FLOAT:
        pm.float_value = float(value)
    elif datatype == DataType.DOUBLE:
        pm.double_value = float(value)
    elif datatype == DataType.BOOLEAN:
        pm.boolean_value = bool(value)
    elif datatype in _TEXTES:
        pm.string_value = str(value)
    elif datatype == DataType.BYTES:
        pm.bytes_value = bytes(value)  # type: ignore[arg-type]
    else:
        raise ValueError(f"Type Sparkplug non pris en charge : {datatype!r}")


def _lire_valeur(pm: pb.Payload.Metric) -> MetricValue:
    if pm.is_null:
        return None
    champ = pm.WhichOneof("value")
    if champ is None:
        return None
    brut = getattr(pm, champ)
    if champ == "int_value" and pm.datatype in _INT32_SIGNES:
        return brut - (1 << 32) if brut >= (1 << 31) else brut
    if champ == "long_value" and pm.datatype == DataType.INT64:
        return brut - (1 << 64) if brut >= (1 << 63) else brut
    if champ == "float_value":
        # Float = IEEE 754 simple précision : on rend la valeur décimale utile
        # (62.3 et non 62.29999923706055).
        return float(f"{brut:.7g}")
    return brut
