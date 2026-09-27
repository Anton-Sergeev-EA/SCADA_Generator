"""Совместимость эмулятора со сторонним синхронным клиентом (pyModbusTCP)."""

import asyncio
import threading

from pyModbusTCP.client import ModbusClient

from scada_core.sim.datastore import DataStore
from scada_core.sim.modbus_server import ModbusTCPServer


def test_third_party_sync_client(port: int) -> None:
    store = DataStore()
    store.holding[0:3] = [100, 200, 300]
    store.coils[0:3] = [1, 0, 1]
    ready = threading.Event()
    loop = asyncio.new_event_loop()

    server = ModbusTCPServer(store, "127.0.0.1", port)

    def serve() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(5)
    client = ModbusClient(host="127.0.0.1", port=port, unit_id=1, timeout=2.0)
    try:
        assert client.open()
        assert client.read_holding_registers(0, 3) == [100, 200, 300]
        assert client.read_coils(0, 3) == [True, False, True]
        assert client.read_holding_registers(999, 5) is None  # исключение Modbus, не обрыв
        assert client.is_open  # соединение осталось живым
        assert client.write_single_register(1, 7)
        assert store.holding[1] == 7
    finally:
        client.close()
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
