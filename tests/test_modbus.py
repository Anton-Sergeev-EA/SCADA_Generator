"""Протокол эмулятора и асинхронный клиент, включая переподключение."""

import asyncio
import struct

import pytest

from scada_core.engine.modbus_client import AsyncModbusManager
from scada_core.sim.datastore import DataStore
from scada_core.sim.modbus_server import ModbusTCPServer, process_adu, process_pdu


def test_pdu_read_holding() -> None:
    st = DataStore()
    st.holding[3:5] = [0x1234, 0xABCD]
    assert process_pdu(st, bytes([3, 0, 3, 0, 2])) == bytes([3, 4, 0x12, 0x34, 0xAB, 0xCD])


def test_pdu_coils_bit_packing() -> None:
    st = DataStore()
    st.coils[0:10] = [1, 0, 1, 1, 0, 0, 0, 0, 1, 0]
    assert process_pdu(st, bytes([1, 0, 0, 0, 10])) == bytes([1, 2, 0b00001101, 0b00000001])


def test_exception_responses_instead_of_disconnect() -> None:
    st = DataStore()
    illegal_fn = process_adu(st, struct.pack(">HHHB", 7, 0, 2, 1) + bytes([0x2B]))
    assert illegal_fn[-2:] == bytes([0xAB, 0x01])
    illegal_addr = process_adu(st, struct.pack(">HHHB", 8, 0, 6, 1) + bytes([3, 0x03, 0xE7, 0, 5]))
    assert illegal_addr[-2:] == bytes([0x83, 0x02])
    bad_qty = process_adu(st, struct.pack(">HHHB", 9, 0, 6, 1) + bytes([3, 0, 0, 0, 200]))
    assert bad_qty[-2:] == bytes([0x83, 0x03])


def test_write_multiple() -> None:
    st = DataStore()
    process_pdu(st, bytes([0x10, 0, 10, 0, 2, 4, 0, 1, 0, 2]))
    assert st.holding[10:12] == [1, 2]
    process_pdu(st, bytes([0x0F, 0, 0, 0, 3, 1, 0b101]))
    assert st.coils[0:3] == [1, 0, 1]


async def test_client_read_write(port: int) -> None:
    store = DataStore()
    server = ModbusTCPServer(store, "127.0.0.1", port)
    await server.start()
    client = AsyncModbusManager("127.0.0.1", port, timeout=1.0, max_retries=1)
    try:
        assert await client.write_single_register(5, 999)
        assert await client.read_holding_registers(5, 1) == [999]
        assert await client.write_single_coil(2, True)
        assert (await client.read_coils(0, 3))[2] is True
        assert await client.write_multiple_registers(20, [1, 2, 3])
        assert await client.read("holding_register", 20, 3) == [1, 2, 3]
    finally:
        await client.disconnect()
        await server.stop()


async def test_reconnects_after_device_restart(port: int) -> None:
    """Регрессия: после обрыва связи клиент больше никогда не переподключался."""
    store = DataStore()
    store.holding[0] = 42
    server = ModbusTCPServer(store, "127.0.0.1", port)
    await server.start()
    client = AsyncModbusManager("127.0.0.1", port, timeout=0.5, max_retries=1, reconnect_delay=0.1)
    assert await client.read_holding_registers(0, 1) == [42]

    await server.stop()
    await asyncio.sleep(0.1)
    assert await client.read_holding_registers(0, 1) is None
    assert not client.is_connected

    server = ModbusTCPServer(store, "127.0.0.1", port)
    await server.start()
    try:
        assert await client.read_holding_registers(0, 1) == [42]
        assert client.is_connected
    finally:
        await client.disconnect()
        await server.stop()


@pytest.mark.parametrize("count", [1, 125])
async def test_block_limits(port: int, count: int) -> None:
    server = ModbusTCPServer(DataStore(), "127.0.0.1", port)
    await server.start()
    client = AsyncModbusManager("127.0.0.1", port, timeout=1.0, max_retries=1)
    try:
        assert len(await client.read_holding_registers(0, count)) == count
    finally:
        await client.disconnect()
        await server.stop()
