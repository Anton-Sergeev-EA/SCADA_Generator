#!/usr/bin/env python3
"""
Modbus TCP эмулятор насосной станции (отдельный процесс).

Запускает физическую модель установки (scada_core/sim/process.py) и
Modbus TCP сервер. Карта регистров соответствует configs/config.yaml.

    python3 modbus_emulator_new.py [--port 5020] [--fault bearing_wear]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging

from scada_core.sim import FAULTS, Simulator


async def main(host: str, port: int, faults: list[str]) -> None:
    sim = Simulator(host=host, port=port)
    for fault in faults:
        sim.model.inject(fault)
    await sim.start()
    logging.info("Modbus TCP эмулятор запущен на %s:%s. Неисправности: %s", host, port, faults or "нет")
    try:
        await asyncio.Event().wait()
    finally:
        await sim.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--fault", action="append", default=[], choices=sorted(FAULTS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(args.host, args.port, args.fault))
