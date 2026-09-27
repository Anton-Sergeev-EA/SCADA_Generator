"""Драйверы Modbus TCP и Modbus RTU.

Логика одинакова — блочное чтение, декодирование типов, запись; отличается
только транспорт: TCP-сокет или последовательный порт (RS-485/RS-232).
RTU работает и через шлюзы «RTU поверх TCP» (serial_port: socket://шлюз:4001)
и RFC 2217 (rfc2217://шлюз:порт) — это умеет pyserial.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from typing import Any

from scada_core.config.loader import DeviceConfig, TagConfig
from scada_core.drivers import modbus_pdu as pdu
from scada_core.drivers.base import (
    QUALITY_BAD,
    QUALITY_COMM,
    QUALITY_GOOD,
    Driver,
    DriverError,
    DriverUnavailable,
)
from scada_core.engine.codec import decode, encode, plan_blocks
from scada_core.engine.modbus_client import AsyncModbusManager

logger = logging.getLogger(__name__)


class _ModbusDriver(Driver):
    def __init__(self, device: DeviceConfig) -> None:
        super().__init__(device)
        self._blocks = plan_blocks(list(device.tags))

    @abstractmethod
    async def _read_block(self, function: str, start: int, count: int) -> list[Any] | None: ...

    @abstractmethod
    async def _write_words(self, tag: TagConfig, words: list[int]) -> bool: ...

    async def read(self) -> dict[str, tuple[float | None, str]]:
        out: dict[str, tuple[float | None, str]] = {}
        for block in self._blocks:
            raw = await self._read_block(block["function"], block["start"], block["end"] - block["start"])
            for tag in block["tags"]:
                if raw is None:
                    out[tag.name] = (None, QUALITY_BAD if self.is_connected else QUALITY_COMM)
                    continue
                off = tag.address - block["start"]
                try:
                    out[tag.name] = (decode(tag, raw[off : off + tag.register_count]), QUALITY_GOOD)
                except (IndexError, ValueError) as exc:
                    logger.error("Decode error %s/%s: %s", self.device.id, tag.name, exc)
                    out[tag.name] = (None, QUALITY_BAD)
        return out

    async def write(self, tag: TagConfig, value: float) -> None:
        if tag.function not in ("coil", "holding_register"):
            raise PermissionError(f"{tag.function} is read-only by the Modbus protocol")
        if not await self._write_words(tag, encode(tag, value)):
            raise DriverError(self.last_error or "write failed")


class ModbusTcpDriver(_ModbusDriver):
    protocol = "modbus_tcp"

    def __init__(self, device: DeviceConfig) -> None:
        super().__init__(device)
        self.client = AsyncModbusManager(
            host=device.host,
            port=device.port,
            slave_id=device.slave_id,
            timeout=device.timeout,
            max_retries=device.retries,
        )

    @property
    def is_connected(self) -> bool:
        return self.client.is_connected

    async def connect(self) -> bool:
        ok = await self.client.connect()
        self.last_error = self.client.last_error
        return ok

    async def _read_block(self, function: str, start: int, count: int) -> list[Any] | None:
        raw = await self.client.read(function, start, count)
        self.last_error = self.client.last_error
        return raw

    async def _write_words(self, tag: TagConfig, words: list[int]) -> bool:
        c = self.client
        if tag.function == "coil":
            ok = await c.write_single_coil(tag.address, bool(words[0]))
        elif len(words) == 1:
            ok = await c.write_single_register(tag.address, words[0])
        else:
            ok = await c.write_multiple_registers(tag.address, words)
        self.last_error = c.last_error
        return ok

    async def disconnect(self) -> None:
        await self.client.disconnect()


class ModbusRtuDriver(_ModbusDriver):
    protocol = "modbus_rtu"

    def __init__(self, device: DeviceConfig) -> None:
        super().__init__(device)
        try:
            import serial  # noqa: F401
        except ImportError as exc:
            raise DriverUnavailable("Modbus RTU: установите pyserial (pip install pyserial)") from exc
        o = device.options
        self.url = str(o["serial_port"])
        self.params = {
            "baudrate": int(o.get("baudrate", 9600)),
            "parity": str(o.get("parity", "N")).upper()[0],
            "stopbits": float(o.get("stopbits", 1)),
            "bytesize": int(o.get("bytesize", 8)),
        }
        self._port: Any = None
        self._lock = asyncio.Lock()
        # Пауза между кадрами RTU — 3,5 символа (не меньше 1,75 мс, как в стандарте).
        char_s = 11.0 / self.params["baudrate"]
        self._gap = max(3.5 * char_s, 0.00175)

    def describe(self) -> str:
        p = self.params
        return f"{self.url} {p['baudrate']} {p['bytesize']}{p['parity']}{p['stopbits']:g}"

    async def _run(self, func: Any, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)

    def _open(self) -> Any:
        import serial

        stop = {1.0: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2.0: serial.STOPBITS_TWO}
        return serial.serial_for_url(
            self.url,
            baudrate=self.params["baudrate"],
            parity=self.params["parity"],
            stopbits=stop.get(self.params["stopbits"], serial.STOPBITS_ONE),
            bytesize=self.params["bytesize"],
            timeout=self.device.timeout,
        )

    async def connect(self) -> bool:
        async with self._lock:
            if self._port is not None:
                return True
            try:
                self._port = await self._run(self._open)
                self._connected = True
                self.last_error = None
                logger.info("Modbus RTU opened: %s", self.describe())
            except Exception as exc:  # noqa: BLE001 - порт занят, шлюз недоступен и т.п.
                self._port = None
                self._connected = False
                self.last_error = str(exc)
            return self._connected

    def _transact_sync(self, request: bytes) -> bytes:
        port = self._port
        port.reset_input_buffer()
        port.write(pdu.rtu_frame(self.device.slave_id, request))
        head = port.read(3)  # unit, function, (bytecount | exception code | addr hi)
        if len(head) < 3:
            raise TimeoutError("no response from slave")
        rest = pdu.expected_length(head[1:3]) + 1 + 2 - 3  # unit + PDU + CRC
        frame = head + port.read(rest)
        body = pdu.rtu_check(frame)
        if body[0] != self.device.slave_id:
            raise ValueError("response from another slave")
        return body[1:]

    async def _transact(self, request: bytes) -> bytes | None:
        if not await self.connect():
            return None
        async with self._lock:
            try:
                reply = await self._run(self._transact_sync, request)
                await asyncio.sleep(self._gap)
                self._connected = True  # на линии RS-485 «связь» — это ответ slave
                return reply
            except TimeoutError as exc:
                self.last_error = str(exc)  # порт открыт, но устройство молчит
                self._connected = False
                return None
            except (ValueError, pdu.ModbusException) as exc:
                self.last_error = str(exc)  # искажённый кадр или отказ slave
                return None
            except Exception as exc:  # noqa: BLE001 - порт/шлюз пропал
                self.last_error = str(exc)
                await self._close_locked()
                return None

    async def _read_block(self, function: str, start: int, count: int) -> list[Any] | None:
        request = pdu.read_request(function, start, count)
        reply = await self._transact(request)
        if reply is None:
            return None
        try:
            return pdu.parse_read(function, count, reply)
        except (ValueError, pdu.ModbusException) as exc:
            self.last_error = str(exc)
            return None

    async def _write_words(self, tag: TagConfig, words: list[int]) -> bool:
        if tag.function == "coil":
            request = pdu.write_coil_request(tag.address, bool(words[0]))
        elif len(words) == 1:
            request = pdu.write_register_request(tag.address, words[0])
        else:
            request = pdu.write_registers_request(tag.address, words)
        reply = await self._transact(request)
        if reply is None:
            return False
        try:
            pdu.check_write(request, reply)
        except (ValueError, pdu.ModbusException) as exc:
            self.last_error = str(exc)
            return False
        return True

    async def _close_locked(self) -> None:
        if self._port is not None:
            port, self._port = self._port, None
            await self._run(port.close)
        self._connected = False

    async def disconnect(self) -> None:
        async with self._lock:
            await self._close_locked()
