"""Проверка Modbus-клиента против эмулятора (раньше — ручной скрипт).

Эмулятор поднимается внутри теста, поэтому отдельно запускать
modbus_emulator_new.py не нужно.
"""

from scada_core.engine.modbus_client import AsyncModbusManager
from scada_core.sim import Simulator


async def test_client_against_emulator(port: int) -> None:
    sim = Simulator(port=port)
    await sim.start()
    client = AsyncModbusManager("127.0.0.1", port, 1, timeout=1.0, max_retries=1)
    try:
        assert await client.connect()
        regs = await client.read_holding_registers(0, 8)
        assert regs is not None and len(regs) == 8
        assert 500 <= regs[0] <= 700  # уровень 50..70 % (×10)
        coils = await client.read_coils(0, 2)
        assert coils == [True, True]  # насос в работе, задвижка открыта
        assert await client.write_single_register(10, 550)  # уставка 55 %
        assert (await client.read_holding_registers(10, 1)) == [550]
    finally:
        await client.disconnect()
        await sim.stop()
