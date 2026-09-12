"""Codec protobuf Sparkplug B et topics."""
from __future__ import annotations

import pytest

from app.protocols.sparkplug_b import codec, topic
from app.protocols.sparkplug_b.codec import DataType, Metric, Payload


def _aller_retour(*metrics: Metric) -> Payload:
    return codec.decode(codec.encode(Payload(metrics=list(metrics), seq=42)))


def test_aller_retour_de_tous_les_types() -> None:
    decode = _aller_retour(
        Metric("i8", DataType.INT8, -5),
        Metric("i32", DataType.INT32, -123456),
        Metric("u32", DataType.UINT32, 4_000_000_000),
        Metric("i64", DataType.INT64, -9_000_000_000),
        Metric("u64", DataType.UINT64, 18_000_000_000_000_000_000),
        Metric("f", DataType.FLOAT, 62.3),
        Metric("d", DataType.DOUBLE, 1.0 / 3.0),
        Metric("b", DataType.BOOLEAN, True),
        Metric("s", DataType.STRING, "MARCHE"),
        Metric("bytes", DataType.BYTES, b"\x00\xff"),
        Metric("nul", DataType.STRING, None),
    )
    assert decode.seq == 42
    assert decode.as_dict() == {
        "i8": -5,
        "i32": -123456,
        "u32": 4_000_000_000,
        "i64": -9_000_000_000,
        "u64": 18_000_000_000_000_000_000,
        "f": 62.3,  # float32 ramené à sa valeur décimale utile
        "d": 1.0 / 3.0,
        "b": True,
        "s": "MARCHE",
        "bytes": b"\x00\xff",
        "nul": None,
    }
    assert decode.metric("i64").datatype == DataType.INT64


def test_alias_sans_nom_preserve() -> None:
    decode = _aller_retour(Metric(None, DataType.INT64, 7, alias=3))
    assert decode.metrics[0].name is None
    assert decode.metrics[0].alias == 3


def test_trame_illisible() -> None:
    with pytest.raises(codec.PayloadDecodeError):
        codec.decode(b"\xff\xff\xff\xff not protobuf")


def test_topics() -> None:
    t = topic.parse_topic("spBv1.0/Plant/DDATA/Edge-1/M-01")
    assert t is not None and t.device_id == "M-01" and t.is_device_message
    assert topic.parse_topic("spBv1.0/Plant/DDATA/Edge-1") is None  # DDATA sans device
    assert topic.parse_topic("spBv1.0/STATE/nova-mes") is None
    assert topic.is_state_topic("spBv1.0/STATE/nova-mes")
    assert topic.build_topic("Plant", "NCMD", "Edge-1") == "spBv1.0/Plant/NCMD/Edge-1"
    with pytest.raises(ValueError):
        topic.build_topic("Pl/ant", "DCMD", "Edge-1", "M-01")
    assert "spBv1.0/Plant/DDATA/+/+" in topic.subscription_filters("Plant")
