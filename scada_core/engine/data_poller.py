"""
Modbus device polling scheduler.

Каждое устройство опрашивается собственной задачей со своим периодом
(poll_interval_ms), теги читаются блоками. Поллер только собирает
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

from scada_core.config.loader import AppConfig, DeviceConfig, TagConfig, load_app_config
from scada_core.engine.codec import decode, plan_blocks
from scada_core.engine.modbus_client import AsyncModbusManager

logger = logging.getLogger(__name__)

QUALITY_GOOD = "GOOD"
QUALITY_BAD = "BAD"
QUALITY_COMM = "COMM_FAIL"


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
    """Modbus device polling scheduler."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or load_app_config()
        self._devices: dict[str, AsyncModbusManager] = {}
        self._tasks: list[asyncio.Task] = []
        self._data_callbacks: list[DataCallback] = []
        self._status_callbacks: list[StatusCallback] = []
        self._online: dict[str, bool] = {}
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

    def client(self, device_id: str) -> AsyncModbusManager | None:
        return self._devices.get(device_id)

    def device_online(self, device_id: str) -> bool:
        return self._online.get(device_id, False)

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
            client = AsyncModbusManager(
                host=dev.host,
                port=dev.port,
                slave_id=dev.slave_id,
                timeout=dev.timeout,
                max_retries=dev.retries,
            )
            self._devices[dev.id] = client
            self._tasks.append(asyncio.create_task(self._device_loop(dev, client)))
            logger.info("Device added: %s (%s:%s)", dev.name.get("ru", dev.id), dev.host, dev.port)
        logger.info("DataPoller started")

    async def stop(self) -> None:
        if not self._is_running:
            return
        self._is_running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for client in self._devices.values():
            await client.disconnect()
        logger.info("DataPoller stopped")

    async def _set_online(self, device_id: str, online: bool, error: str | None) -> None:
        if self._online.get(device_id) == online:
            return
        self._online[device_id] = online
        for cb in self._status_callbacks:
            try:
                await _maybe_await(cb(device_id, online, error))
            except Exception:
                logger.exception("status callback failed")

    async def _device_loop(self, dev: DeviceConfig, client: AsyncModbusManager) -> None:
        period = dev.poll_interval_ms / 1000.0
        blocks = plan_blocks(list(dev.tags))
        next_t = time.monotonic()
        while self._is_running:
            try:
                values = await self.poll_device(dev, client, blocks)
                self.cycles += 1
                for cb in self._data_callbacks:
                    try:
                        await _maybe_await(cb(values))
                    except Exception:
                        logger.exception("data callback failed")
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

    async def poll_device(
        self, dev: DeviceConfig, client: AsyncModbusManager, blocks: list[dict] | None = None
    ) -> list[TagValue]:
        """Один цикл опроса устройства. Возвращает значения всех тегов;
        при отсутствии связи — с качеством COMM_FAIL, чтобы оператор
        видел потерю связи, а не «замёрзшие» цифры."""
        blocks = blocks if blocks is not None else plan_blocks(list(dev.tags))
        ts = time.time()
        if not await client.connect():
            await self._set_online(dev.id, False, client.last_error)
            return [TagValue(dev.id, t.name, None, QUALITY_COMM, ts) for t in dev.tags]

        values: list[TagValue] = []
        for block in blocks:
            count = block["end"] - block["start"]
            raw = await client.read(block["function"], block["start"], count)
            tag: TagConfig
            for tag in block["tags"]:
                if raw is None:
                    quality = QUALITY_BAD if client.is_connected else QUALITY_COMM
                    values.append(TagValue(dev.id, tag.name, None, quality, ts))
                    continue
                off = tag.address - block["start"]
                try:
                    value = decode(tag, raw[off : off + tag.register_count])
                    values.append(TagValue(dev.id, tag.name, value, QUALITY_GOOD, ts))
                except (IndexError, ValueError) as exc:
                    logger.error("Decode error %s/%s: %s", dev.id, tag.name, exc)
                    values.append(TagValue(dev.id, tag.name, None, QUALITY_BAD, ts))
        await self._set_online(dev.id, client.is_connected, client.last_error)
        return values
