"""Драйверы промышленных протоколов. Каждый — необязательный модуль:
библиотека протокола нужна, только если такое устройство есть в конфигурации.

| protocol    | Оборудование                                  | Библиотека        |
|-------------|-----------------------------------------------|-------------------|
| modbus_tcp  | ПЛК, шлюзы, частотники по Ethernet            | pyModbusTCP (MIT) |
| modbus_rtu  | RS-485/RS-232, шлюзы «RTU поверх TCP»          | pyserial (BSD)    |
| opcua       | ПЛК и серверы OPC UA любых производителей     | asyncua (LGPL-3)  |
| mqtt        | IIoT-шлюзы, телеметрия, брокеры MQTT          | aiomqtt (BSD)     |
| iec104      | телемеханика, энергетика (МЭК 60870-5-104)    | c104 (GPL-3)      |
"""

from __future__ import annotations

from importlib import import_module

from scada_core.config.loader import DeviceConfig
from scada_core.drivers.base import (
    QUALITY_BAD,
    QUALITY_COMM,
    QUALITY_GOOD,
    QUALITY_STALE,
    Driver,
    DriverError,
    DriverUnavailable,
)

_REGISTRY = {
    "modbus_tcp": ("scada_core.drivers.modbus", "ModbusTcpDriver"),
    "modbus_rtu": ("scada_core.drivers.modbus", "ModbusRtuDriver"),
    "opcua": ("scada_core.drivers.opcua", "OpcUaDriver"),
    "mqtt": ("scada_core.drivers.mqtt", "MqttDriver"),
    "iec104": ("scada_core.drivers.iec104", "Iec104Driver"),
}


def create_driver(device: DeviceConfig) -> Driver:
    module, cls = _REGISTRY[device.protocol]
    return getattr(import_module(module), cls)(device)


def available_protocols() -> dict[str, bool]:
    """Какие протоколы можно использовать в этой установке."""
    probes = {
        "modbus_tcp": "pyModbusTCP",
        "modbus_rtu": "serial",
        "opcua": "asyncua",
        "mqtt": "aiomqtt",
        "iec104": "c104",
    }
    out = {}
    for proto, mod in probes.items():
        try:
            import_module(mod)
            out[proto] = True
        except ImportError:
            out[proto] = False
    return out


__all__ = [
    "QUALITY_BAD",
    "QUALITY_COMM",
    "QUALITY_GOOD",
    "QUALITY_STALE",
    "Driver",
    "DriverError",
    "DriverUnavailable",
    "available_protocols",
    "create_driver",
]
