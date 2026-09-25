"""Эмулятор установки: модель процесса + Modbus TCP сервер."""

from __future__ import annotations

import asyncio
import contextlib

from scada_core.sim.datastore import DataStore
from scada_core.sim.modbus_server import ModbusTCPServer
from scada_core.sim.process import FAULTS, ProcessModel


class Simulator:
    """Запускает модель процесса и Modbus-сервер в текущем event loop."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5020, seed: int | None = 42) -> None:
        self.store = DataStore()
        self.model = ProcessModel(self.store, seed=seed)
        self.server = ModbusTCPServer(self.store, host, port)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.server.start()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            self.model.step(1.0)
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.server.stop()


__all__ = ["FAULTS", "DataStore", "ModbusTCPServer", "ProcessModel", "Simulator"]
