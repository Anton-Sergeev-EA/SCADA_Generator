"""
Asynchronous Modbus TCP client with automatic reconnection.

pyModbusTCP — синхронная библиотека, поэтому каждый вызов уходит в
пул потоков (run_in_executor) и не блокирует event loop. Доступ к
одному соединению сериализуется asyncio.Lock: сокет не потокобезопасен.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pyModbusTCP.client import ModbusClient

logger = logging.getLogger(__name__)


class ModbusConnectionError(Exception):
    """Modbus device connection error"""


class AsyncModbusManager:
    """Asynchronous Modbus TCP connection manager with reconnection support."""

    def __init__(
        self,
        host: str,
        port: int = 502,
        slave_id: int = 1,
        timeout: float = 3.0,
        max_retries: int = 3,
        reconnect_delay: float = 1.0,
    ) -> None:
        self.host = host
        self.port = port
        self.slave_id = slave_id
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.reconnect_delay = reconnect_delay
        self.client = ModbusClient(host=host, port=port, unit_id=slave_id, timeout=timeout, auto_open=False)
        self._is_connected = False
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    async def _call(self, func: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    async def connect(self) -> bool:
        """Establish connection to the device. Returns True on success."""
        async with self._lock:
            return await self._connect_locked()

    async def _connect_locked(self) -> bool:
        if self._is_connected and self.client.is_open:
            return True
        self._is_connected = False
        for attempt in range(self.max_retries):
            try:
                if await self._call(self.client.open):
                    self._is_connected = True
                    self.last_error = None
                    logger.info("Connected to %s:%s", self.host, self.port)
                    return True
                self.last_error = f"connection refused ({self.host}:{self.port})"
            except Exception as exc:  # noqa: BLE001 - сетевые ошибки любого рода
                self.last_error = str(exc)
            logger.debug(
                "Connect %s:%s failed (attempt %d/%d): %s",
                self.host,
                self.port,
                attempt + 1,
                self.max_retries,
                self.last_error,
            )
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.reconnect_delay * (2**attempt))
        logger.warning("Device %s:%s unreachable: %s", self.host, self.port, self.last_error)
        return False

    async def disconnect(self) -> None:
        async with self._lock:
            if self.client.is_open:
                await self._call(self.client.close)
            self._is_connected = False

    async def _request(self, func: Callable[..., Any], *args: Any) -> Any:
        """Выполняет запрос; при сбое помечает соединение разорванным.

        pyModbusTCP при обрыве связи не бросает исключение, а возвращает
        None и закрывает сокет — поэтому проверяем is_open после вызова.
        Раньше флаг _is_connected оставался True, и после первой же
        потери связи поллер больше никогда не переподключался.
        """
        async with self._lock:
            if not await self._connect_locked():
                return None
            try:
                result = await self._call(func, *args)
            except Exception as exc:  # noqa: BLE001
                result = None
                self.last_error = str(exc)
            if result is None:
                err = self.client.last_error_as_txt
                exc_txt = self.client.last_except_as_txt
                self.last_error = f"{err}; {exc_txt}" if self.client.last_except else err
                if not self.client.is_open:
                    self._is_connected = False
                    logger.warning("Lost connection to %s:%s", self.host, self.port)
            return result

    async def read_holding_registers(self, address: int, count: int = 1) -> list[int] | None:
        """Read Holding Registers (0x03)."""
        return await self._request(self.client.read_holding_registers, address, count)

    async def read_input_registers(self, address: int, count: int = 1) -> list[int] | None:
        """Read Input Registers (0x04)."""
        return await self._request(self.client.read_input_registers, address, count)

    async def read_coils(self, address: int, count: int = 1) -> list[bool] | None:
        """Read Coils (0x01)."""
        return await self._request(self.client.read_coils, address, count)

    async def read_discrete_inputs(self, address: int, count: int = 1) -> list[bool] | None:
        """Read Discrete Inputs (0x02)."""
        return await self._request(self.client.read_discrete_inputs, address, count)

    async def write_single_register(self, address: int, value: int) -> bool:
        """Write Single Register (0x06)."""
        return bool(await self._request(self.client.write_single_register, address, value))

    async def write_multiple_registers(self, address: int, values: list[int]) -> bool:
        """Write Multiple Registers (0x10)."""
        return bool(await self._request(self.client.write_multiple_registers, address, values))

    async def write_single_coil(self, address: int, value: bool) -> bool:
        """Write Single Coil (0x05)."""
        return bool(await self._request(self.client.write_single_coil, address, bool(value)))

    async def read(self, function: str, address: int, count: int) -> list[Any] | None:
        readers = {
            "holding_register": self.read_holding_registers,
            "input_register": self.read_input_registers,
            "coil": self.read_coils,
            "discrete_input": self.read_discrete_inputs,
        }
        return await readers[function](address, count)

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def __repr__(self) -> str:
        return f"AsyncModbusManager(host={self.host}, port={self.port}, slave_id={self.slave_id})"
