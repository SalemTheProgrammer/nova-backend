from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class DataType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    Unknown: _ClassVar[DataType]
    Int8: _ClassVar[DataType]
    Int16: _ClassVar[DataType]
    Int32: _ClassVar[DataType]
    Int64: _ClassVar[DataType]
    UInt8: _ClassVar[DataType]
    UInt16: _ClassVar[DataType]
    UInt32: _ClassVar[DataType]
    UInt64: _ClassVar[DataType]
    Float: _ClassVar[DataType]
    Double: _ClassVar[DataType]
    Boolean: _ClassVar[DataType]
    String: _ClassVar[DataType]
    DateTime: _ClassVar[DataType]
    Text: _ClassVar[DataType]
    UUID: _ClassVar[DataType]
    DataSet: _ClassVar[DataType]
    Bytes: _ClassVar[DataType]
    File: _ClassVar[DataType]
    Template: _ClassVar[DataType]
Unknown: DataType
Int8: DataType
Int16: DataType
Int32: DataType
Int64: DataType
UInt8: DataType
UInt16: DataType
UInt32: DataType
UInt64: DataType
Float: DataType
Double: DataType
Boolean: DataType
String: DataType
DateTime: DataType
Text: DataType
UUID: DataType
DataSet: DataType
Bytes: DataType
File: DataType
Template: DataType

class Payload(_message.Message):
    __slots__ = ("timestamp", "metrics", "seq", "uuid", "body")
    class Metric(_message.Message):
        __slots__ = ("name", "alias", "timestamp", "datatype", "is_historical", "is_transient", "is_null", "int_value", "long_value", "float_value", "double_value", "boolean_value", "string_value", "bytes_value")
        NAME_FIELD_NUMBER: _ClassVar[int]
        ALIAS_FIELD_NUMBER: _ClassVar[int]
        TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
        DATATYPE_FIELD_NUMBER: _ClassVar[int]
        IS_HISTORICAL_FIELD_NUMBER: _ClassVar[int]
        IS_TRANSIENT_FIELD_NUMBER: _ClassVar[int]
        IS_NULL_FIELD_NUMBER: _ClassVar[int]
        INT_VALUE_FIELD_NUMBER: _ClassVar[int]
        LONG_VALUE_FIELD_NUMBER: _ClassVar[int]
        FLOAT_VALUE_FIELD_NUMBER: _ClassVar[int]
        DOUBLE_VALUE_FIELD_NUMBER: _ClassVar[int]
        BOOLEAN_VALUE_FIELD_NUMBER: _ClassVar[int]
        STRING_VALUE_FIELD_NUMBER: _ClassVar[int]
        BYTES_VALUE_FIELD_NUMBER: _ClassVar[int]
        name: str
        alias: int
        timestamp: int
        datatype: int
        is_historical: bool
        is_transient: bool
        is_null: bool
        int_value: int
        long_value: int
        float_value: float
        double_value: float
        boolean_value: bool
        string_value: str
        bytes_value: bytes
        def __init__(self, name: _Optional[str] = ..., alias: _Optional[int] = ..., timestamp: _Optional[int] = ..., datatype: _Optional[int] = ..., is_historical: _Optional[bool] = ..., is_transient: _Optional[bool] = ..., is_null: _Optional[bool] = ..., int_value: _Optional[int] = ..., long_value: _Optional[int] = ..., float_value: _Optional[float] = ..., double_value: _Optional[float] = ..., boolean_value: _Optional[bool] = ..., string_value: _Optional[str] = ..., bytes_value: _Optional[bytes] = ...) -> None: ...
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    UUID_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    timestamp: int
    metrics: _containers.RepeatedCompositeFieldContainer[Payload.Metric]
    seq: int
    uuid: str
    body: bytes
    def __init__(self, timestamp: _Optional[int] = ..., metrics: _Optional[_Iterable[_Union[Payload.Metric, _Mapping]]] = ..., seq: _Optional[int] = ..., uuid: _Optional[str] = ..., body: _Optional[bytes] = ...) -> None: ...
