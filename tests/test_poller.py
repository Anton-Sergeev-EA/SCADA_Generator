"""DataPoller против эмулятора: значения, качество, потеря связи."""

import asyncio

from scada_core.config.loader import AppConfig
from scada_core.engine.data_poller import QUALITY_COMM, QUALITY_GOOD, DataPoller, TagValue
from scada_core.sim import Simulator


async def wait_for(predicate, timeout: float = 8.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("timeout")
        await asyncio.sleep(0.05)


async def test_poller_reads_all_tags_and_reports_comm_loss(config: AppConfig) -> None:
    sim = Simulator(port=config.devices[0].port)
    await sim.start()
    batches: list[list[TagValue]] = []
    status: list[tuple[str, bool]] = []
    poller = DataPoller(config)
    poller.add_callback("data", batches.append)
    poller.add_callback("status", lambda dev, online, err: status.append((dev, online)))
    await poller.start()
    try:
        await wait_for(lambda: len(batches) >= 2)
        last = {tv.tag_name: tv for tv in batches[-1]}
        assert set(last) == {t.name for t in config.devices[0].tags}
        assert all(tv.quality == QUALITY_GOOD for tv in last.values())
        assert 0 <= last["tank_level"].value <= 100
        assert last["pump_running"].value == 1.0
        assert status == [("plc_main", True)]

        await sim.stop()  # «обрыв кабеля»
        await wait_for(lambda: batches[-1][0].quality == QUALITY_COMM)
        assert ("plc_main", False) in status
    finally:
        await poller.stop()


async def test_poller_period_is_respected(config: AppConfig) -> None:
    sim = Simulator(port=config.devices[0].port)
    await sim.start()
    poller = DataPoller(config)
    stamps: list[float] = []
    poller.add_callback("data", lambda values: stamps.append(values[0].ts))
    await poller.start()
    try:
        await wait_for(lambda: len(stamps) >= 6)
    finally:
        await poller.stop()
        await sim.stop()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(0.1 < g < 0.5 for g in gaps), gaps  # poll_interval_ms = 200
