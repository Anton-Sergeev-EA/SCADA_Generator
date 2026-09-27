"""Общий интерфейс драйвера промышленного протокола.

Всё, что выше драйвера (алармы, ML, мнемосхема, архив), работает с
инженерными значениями и не знает, откуда они пришли: из Modbus-регистра,
узла OPC UA, MQTT-топика или объекта МЭК 104. Поэтому система не привязана
ни к одному производителю оборудования.
"""

from __future__ import annotations

import json
import math
import time
from abc import ABC, abstractmethod
from typing import Any

from scada_core.config.loader import DeviceConfig, TagConfig

QUALITY_GOOD = "GOOD"
QUALITY_BAD = "BAD"
QUALITY_COMM = "COMM_FAIL"
QUALITY_STALE = "STALE"  # значение давно не обновлялось (MQTT, МЭК 104)


class DriverError(RuntimeError):
    """Ошибка обмена с устройством (записи, чтения, подключения)."""


class DriverUnavailable(DriverError):
    """Не установлена библиотека протокола."""


def to_engineering(tag: TagConfig, raw: Any) -> float:
    """Значение из протокола -> инженерное (raw * scale + offset)."""
    if isinstance(raw, bool) or tag.is_bit:
        return 1.0 if _truthy(raw) else 0.0
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite value")
    return value * tag.scale + tag.offset


def to_raw(tag: TagConfig, value: float) -> float | bool:
    """Инженерное значение -> значение для записи в протокол."""
    if tag.is_bit:
        return bool(value)
    return (value - tag.offset) / tag.scale if tag.scale else value


_TRUE = ("1", "true", "on", "yes", "open", "running")
_FALSE = ("0", "false", "off", "no", "closed", "stopped")


def _truthy(raw: Any) -> bool:
    if isinstance(raw, str):
        word = raw.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        # Неизвестное слово — это ошибка данных, а не «выключено».
        raise ValueError(f"not a boolean: {raw!r}")
    return bool(raw)


def parse_payload(payload: bytes | str, json_path: str | None) -> Any:
    """Текстовое/JSON-сообщение -> значение. json_path: «data.value» или «items.0.v»."""
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    if json_path is None:
        text = text.strip()
        try:
            return float(text)
        except ValueError:
            return text  # слово: допустимо только для дискретного тега
    node: Any = json.loads(text)
    for part in json_path.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


class Driver(ABC):
    """Драйвер одного устройства. Реализации: scada_core/drivers/*.py."""

    protocol = "abstract"

    def __init__(self, device: DeviceConfig) -> None:
        self.device = device
        self.last_error: str | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def describe(self) -> str:
        return f"{self.device.host}:{self.device.port}"

    @abstractmethod
    async def connect(self) -> bool:
        """Подключиться (или убедиться, что подключены). True — связь есть."""

    @abstractmethod
    async def read(self) -> dict[str, tuple[float | None, str]]:
        """Все теги устройства: имя -> (инженерное значение, качество)."""

    @abstractmethod
    async def write(self, tag: TagConfig, value: float) -> None:
        """Записать инженерное значение. При неудаче — DriverError."""

    @abstractmethod
    async def disconnect(self) -> None: ...


class PushCache:
    """Кеш значений для протоколов с подпиской (MQTT, МЭК 104):
    данные приходят сами, а поллер забирает последние значения."""

    def __init__(self, stale_s: float) -> None:
        self.stale_s = stale_s
        self._values: dict[str, tuple[float | None, str, float]] = {}

    def put(self, name: str, value: float | None, quality: str = QUALITY_GOOD) -> None:
        self._values[name] = (value, quality, time.monotonic())

    def get(self, name: str) -> tuple[float | None, str]:
        item = self._values.get(name)
        if item is None:
            return None, QUALITY_STALE
        value, quality, at = item
        if time.monotonic() - at > self.stale_s:
            return value, QUALITY_STALE
        return value, quality
