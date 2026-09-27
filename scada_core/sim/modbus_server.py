"""Асинхронный Modbus TCP сервер (slave) поверх DataStore.

По сравнению с прежним эмулятором:
  * корректно собирает кадры MBAP из потока TCP (несколько запросов
    в одном recv или один запрос в нескольких);
  * на ошибки отвечает кодами исключений Modbus (0x01/0x02/0x03),
    а не разрывом соединения;
  * поддерживает функции 0x01-0x06, 0x0F, 0x10.
"""

from __future__ import annotations

import asyncio
import logging
import struct

from scada_core.sim.datastore import DataStore

logger = logging.getLogger(__name__)

ILLEGAL_FUNCTION = 0x01
ILLEGAL_ADDRESS = 0x02
ILLEGAL_VALUE = 0x03


class ModbusError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _pack_bits(bits: list[int]) -> bytes:
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def process_pdu(store: DataStore, pdu: bytes) -> bytes:
    """Обрабатывает PDU (код функции + данные) и возвращает PDU ответа."""
    if not pdu:
        raise ModbusError(ILLEGAL_FUNCTION)
    fc = pdu[0]
    try:
        if fc in (0x01, 0x02, 0x03, 0x04):
            if len(pdu) != 5:
                raise ModbusError(ILLEGAL_VALUE)
            start, qty = struct.unpack(">HH", pdu[1:5])
            bit = fc in (0x01, 0x02)
            max_qty = 2000 if bit else 125
            if not 1 <= qty <= max_qty:
                raise ModbusError(ILLEGAL_VALUE)
            if start + qty > store.size:
                raise ModbusError(ILLEGAL_ADDRESS)
            table = {0x01: store.coils, 0x02: store.discrete, 0x03: store.holding, 0x04: store.input}[fc]
            values = table[start : start + qty]
            if bit:
                data = _pack_bits(values)
            else:
                data = struct.pack(f">{qty}H", *(v & 0xFFFF for v in values))
            return bytes([fc, len(data)]) + data
        if fc == 0x05:
            addr, value = struct.unpack(">HH", pdu[1:5])
            if value not in (0x0000, 0xFF00):
                raise ModbusError(ILLEGAL_VALUE)
            if addr >= store.size:
                raise ModbusError(ILLEGAL_ADDRESS)
            store.coils[addr] = 1 if value == 0xFF00 else 0
            return pdu[:5]
        if fc == 0x06:
            addr, value = struct.unpack(">HH", pdu[1:5])
            if addr >= store.size:
                raise ModbusError(ILLEGAL_ADDRESS)
            store.holding[addr] = value
            return pdu[:5]
        if fc == 0x0F:
            start, qty, nbytes = struct.unpack(">HHB", pdu[1:6])
            if not 1 <= qty <= 1968 or nbytes != (qty + 7) // 8 or len(pdu) != 6 + nbytes:
                raise ModbusError(ILLEGAL_VALUE)
            if start + qty > store.size:
                raise ModbusError(ILLEGAL_ADDRESS)
            payload = pdu[6:]
            for i in range(qty):
                store.coils[start + i] = (payload[i // 8] >> (i % 8)) & 1
            return pdu[:5]
        if fc == 0x10:
            start, qty, nbytes = struct.unpack(">HHB", pdu[1:6])
            if not 1 <= qty <= 123 or nbytes != qty * 2 or len(pdu) != 6 + nbytes:
                raise ModbusError(ILLEGAL_VALUE)
            if start + qty > store.size:
                raise ModbusError(ILLEGAL_ADDRESS)
            store.holding[start : start + qty] = list(struct.unpack(f">{qty}H", pdu[6:]))
            return pdu[:5]
    except struct.error as exc:
        raise ModbusError(ILLEGAL_VALUE) from exc
    raise ModbusError(ILLEGAL_FUNCTION)


def process_adu(store: DataStore, adu: bytes) -> bytes:
    """Полный кадр Modbus TCP (MBAP + PDU) -> кадр ответа."""
    tid, pid, _length, unit = struct.unpack(">HHHB", adu[:7])
    pdu = adu[7:]
    try:
        reply = process_pdu(store, pdu)
    except ModbusError as err:
        reply = bytes([(pdu[0] if pdu else 0) | 0x80, err.code])
    return struct.pack(">HHHB", tid, pid, len(reply) + 1, unit) + reply


class ModbusTCPServer:
    def __init__(self, store: DataStore, host: str = "0.0.0.0", port: int = 5020) -> None:
        self.store = store
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._handlers: set[asyncio.Task] = set()
        self.requests = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        logger.info("Modbus TCP emulator listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        """Останавливает сервер и рвёт активные соединения — как при
        выключении реального ПЛК (иначе клиенты «не заметили» бы останов)."""
        if self._server:
            self._server.close()
            for writer in list(self._clients):
                writer.close()
            for task in list(self._handlers):
                task.cancel()
            await asyncio.gather(*self._handlers, return_exceptions=True)
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.debug("client connected: %s", peer)
        self._clients.add(writer)
        task = asyncio.current_task()
        if task is not None:
            self._handlers.add(task)
        try:
            while True:
                header = await reader.readexactly(7)
                length = struct.unpack(">H", header[4:6])[0]
                if length < 2 or length > 254:
                    break
                body = await reader.readexactly(length - 1)
                self.requests += 1
                writer.write(process_adu(self.store, header + body))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(writer)
            if task is not None:
                self._handlers.discard(task)
            writer.close()
            logger.debug("client disconnected: %s", peer)


class ModbusRTUOverTCPServer(ModbusTCPServer):
    """Slave Modbus RTU поверх TCP — так работают шлюзы RS-485/Ethernet
    в «прозрачном» режиме. Кадры: адрес + PDU + CRC-16, без заголовка MBAP.
    Используется в тестах драйвера RTU (serial_port: socket://хост:порт)."""

    def __init__(self, store: DataStore, host: str = "0.0.0.0", port: int = 4001, unit: int = 1) -> None:
        super().__init__(store, host, port)
        self.unit = unit

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        from scada_core.drivers.modbus_pdu import rtu_check, rtu_frame

        self._clients.add(writer)
        task = asyncio.current_task()
        if task is not None:
            self._handlers.add(task)
        try:
            while True:
                head = await reader.readexactly(2)  # адрес, функция
                fc = head[1]
                if fc in (0x0F, 0x10):
                    fixed = await reader.readexactly(5)
                    rest = await reader.readexactly(fixed[4] + 2)
                    frame = head + fixed + rest
                else:
                    frame = head + await reader.readexactly(6)
                try:
                    body = rtu_check(frame)
                except ValueError:
                    continue  # битый кадр: slave молчит, как настоящий
                if body[0] != self.unit:
                    continue
                self.requests += 1
                try:
                    reply = process_pdu(self.store, body[1:])
                except ModbusError as err:
                    reply = bytes([fc | 0x80, err.code])
                writer.write(rtu_frame(self.unit, reply))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(writer)
            if task is not None:
                self._handlers.discard(task)
            writer.close()
