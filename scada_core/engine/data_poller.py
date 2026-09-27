"""
Modbus device polling scheduler.

Каждое устройство опрашивается собственной задачей со своим периодом
(poll_interval_ms) через драйвер своего протокола (scada_core/drivers):
Modbus TCP/RTU, OPC UA, MQTT, МЭК 104. Поллер только собирает
данные; алармы и аналитика обрабатываются подписчиками (см. runtime.py).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from scada_core.config.loader import AppConfig, DeviceConfig, load_app_config
from scada_core.drivers import (
    QUALITY_BAD,
    QUALITY_COMM,
    QUALITY_GOOD,
    QUALITY_STALE,
    Driver,
    DriverUnavailable,
    create_driver,
)

logger = logging.getLogger(__name__)

__all__ = ["QUALITY_BAD", "QUALITY_COMM", "QUALITY_GOOD", "QUALITY_STALE", "DataPoller", "TagValue"]


@dataclass
class TagValue:
    """Tag value with metadata. ts — секунды UNIX (UTC)."""

    device_id: str
    tag_name: str
    value: float | None
    quality: str = QUALITY_GOOD
    ts: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.device_id}/{self.tag_name}"

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc)


DataCallback = Callable[[list[TagValue]], Awaitable[None] | None]
StatusCallback = Callable[[str, bool, str | None], Awaitable[None] | None]


async def _maybe_await(result: Any) -> None:
    if inspect.isawaitable(result):
        await result


class DataPoller:
    """Опрос устройств любых поддерживаемых протоколов."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or load_app_config()
        self._drivers: dict[str, Driver] = {}
        self._tasks: list[asyncio.Task] = []
        self._data_callbacks: list[DataCallback] = []
        self._status_callbacks: list[StatusCallback] = []
        self._online: dict[str, bool] = {}
        self._errors: dict[str, str] = {}
        self._is_running = False
        self.cycles = 0

    def add_callback(self, callback_type: str, callback: Callable) -> None:
        """Register a callback: "data" (list[TagValue]) or "status" (device_id, online, error)."""
        if callback_type == "data":
            self._data_callbacks.append(callback)
        elif callback_type == "status":
            self._status_callbacks.append(callback)
        else:
            raise ValueError(f"unknown callback type: {callback_type!r}")

    def driver(self, device_id: str) -> Driver | None:
        return self._drivers.get(device_id)

    def device_online(self, device_id: str) -> bool:
        return self._online.get(device_id, False)

    def device_error(self, device_id: str) -> str | None:
        return self._errors.get(device_id)

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        if self._is_running:
            return
        devices = self._config.active_devices
        if not devices:
            logger.warning("No active devices configured")
            return
        self._is_running = True
        for dev in devices:
            try:
                driver = create_driver(dev)
            except DriverUnavailable as exc:
                # Устройство без установленной библиотеки не должно ронять
                # всю систему: остальные устройства продолжают работать.
                logger.error("%s: %s", dev.id, exc)
                self._errors[dev.id] = str(exc)
                self._tasks.append(asyncio.create_task(self._unavailable_loop(dev, str(exc))))
                continue
            self._drivers[dev.id] = driver
            self._tasks.append(asyncio.create_task(self._device_loop(dev, driver)))
            logger.info(
                "Device added: %s [%s %s]", dev.name.get("ru", dev.id), dev.protocol, driver.describe()
            )
        logger.info("DataPoller started")

    async def stop(self) -> None:
        if not self._is_running:
            return
        self._is_running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for driver in self._drivers.values():
            try:
                await driver.disconnect()
            except Exception:
                logger.exception("disconnect failed")
        logger.info("DataPoller stopped")

    async def _set_online(self, device_id: str, online: bool, error: str | None) -> None:
        if error:
            self._errors[device_id] = error
        if self._online.get(device_id) == online:
            return
        self._online[device_id] = online
        for cb in self._status_callbacks:
            try:
                await _maybe_await(cb(device_id, online, error))
            except Exception:
                logger.exception("status callback failed")

    async def _emit(self, values: list[TagValue]) -> None:
        self.cycles += 1
        for cb in self._data_callbacks:
            try:
                await _maybe_await(cb(values))
            except Exception:
                logger.exception("data callback failed")

    async def _unavailable_loop(self, dev: DeviceConfig, error: str) -> None:
        period = dev.poll_interval_ms / 1000.0
        while self._is_running:
            await self._set_online(dev.id, False, error)
            await self._emit([TagValue(dev.id, t.name, None, QUALITY_COMM) for t in dev.tags])
            await asyncio.sleep(period)

    async def _device_loop(self, dev: DeviceConfig, driver: Driver) -> None:
        period = dev.poll_interval_ms / 1000.0
        next_t = time.monotonic()
        while self._is_running:
            try:
                await self._emit(await self.poll_device(dev, driver))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Polling error on %s", dev.id)
            next_t += period
            delay = next_t - time.monotonic()
            if delay < 0:  # не успели — не пытаемся «догонять» пачкой запросов
                next_t = time.monotonic()
                delay = 0
            await asyncio.sleep(delay)

    async def poll_device(self, dev: DeviceConfig, driver: Driver) -> list[TagValue]:
        """Один цикл опроса устройства. Возвращает значения всех тегов;
        при отсутствии связи — с качеством COMM_FAIL, чтобы оператор
        видел потерю связи, а не «замёрзшие» цифры."""
        ts = time.time()
        if not await driver.connect():
            await self._set_online(dev.id, False, driver.last_error)
            return [TagValue(dev.id, t.name, None, QUALITY_COMM, ts) for t in dev.tags]
        values = await driver.read()
        await self._set_online(dev.id, driver.is_connected, driver.last_error)
        return [TagValue(dev.id, t.name, *values.get(t.name, (None, QUALITY_BAD)), ts) for t in dev.tags]
