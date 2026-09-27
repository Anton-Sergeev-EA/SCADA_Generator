"""Драйвер MQTT — IIoT-шлюзы, телеметрия, любые брокеры (Mosquitto, EMQX…).
Библиотека aiomqtt (BSD) поверх paho-mqtt (EPL-2.0 / BSD-3-Clause).

Устройство: protocol: mqtt, host, port (1883), username, password, tls: true,
            stale_s — через сколько секунд без сообщений значение устаревает.
Тег: topic: plant/tank/level
     json_path: data.value        (если сообщение — JSON)
     command_topic: plant/tank/setpoint/set   (для записи; по умолчанию <topic>/set)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from scada_core.config.loader import DeviceConfig, TagConfig
from scada_core.drivers.base import (
    QUALITY_BAD,
    QUALITY_COMM,
    Driver,
    DriverError,
    DriverUnavailable,
    PushCache,
    error_text,
    parse_payload,
    to_engineering,
    to_raw,
)

logger = logging.getLogger(__name__)


def topic_matches(pattern: str, topic: str) -> bool:
    """Сопоставление с шаблоном MQTT (+ — один уровень, # — остаток)."""
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


class MqttDriver(Driver):
    protocol = "mqtt"

    def __init__(self, device: DeviceConfig) -> None:
        super().__init__(device)
        try:
            import aiomqtt  # noqa: F401
        except ImportError as exc:
            raise DriverUnavailable("MQTT: установите aiomqtt (pip install aiomqtt)") from exc
        o = device.options
        self.cache = PushCache(float(o.get("stale_s", max(30.0, 5 * device.poll_interval_ms / 1000))))
        self._client: Any = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def connect(self) -> bool:
        if self._task is None or self._task.done():
            self._ready.clear()
            self._task = asyncio.create_task(self._run())
        if not self._connected:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._ready.wait(), timeout=self.device.timeout)
        return self._connected

    async def _run(self) -> None:
        """Держит подписку и переподключается с нарастающей паузой."""
        import aiomqtt

        o = self.device.options
        delay = 1.0
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self.device.host,
                    port=self.device.port,
                    username=o.get("username"),
                    password=o.get("password"),
                    identifier=o.get("client_id"),
                    tls_params=aiomqtt.TLSParameters() if o.get("tls") else None,
                    timeout=self.device.timeout,
                ) as client:
                    for topic in {t.topic for t in self.device.tags}:
                        await client.subscribe(topic)
                    self._client = client
                    self._connected = True
                    self.last_error = None
                    self._ready.set()
                    delay = 1.0
                    logger.info("MQTT connected: %s", self.describe())
                    async for message in client.messages:
                        self._on_message(str(message.topic), message.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - брокер недоступен, обрыв сети
                self.last_error = error_text(exc)
            self._client = None
            self._connected = False
            self._ready.set()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    def _on_message(self, topic: str, payload: Any) -> None:
        for tag in self.device.tags:
            if not topic_matches(tag.topic, topic):
                continue
            try:
                value = to_engineering(tag, parse_payload(payload, tag.json_path))
                self.cache.put(tag.name, value)
            except (ValueError, KeyError, IndexError, TypeError):
                self.cache.put(tag.name, None, QUALITY_BAD)

    async def read(self) -> dict[str, tuple[float | None, str]]:
        if not self._connected:
            return {t.name: (None, QUALITY_COMM) for t in self.device.tags}
        return {t.name: self.cache.get(t.name) for t in self.device.tags}

    async def write(self, tag: TagConfig, value: float) -> None:
        if self._client is None:
            raise DriverError(self.last_error or "MQTT broker is unreachable")
        raw = to_raw(tag, value)
        payload = ("1" if raw else "0") if isinstance(raw, bool) else repr(float(raw))
        try:
            await self._client.publish(tag.command_topic or f"{tag.topic}/set", payload, qos=1)
        except Exception as exc:
            raise DriverError(error_text(exc)) from exc

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._connected = False
