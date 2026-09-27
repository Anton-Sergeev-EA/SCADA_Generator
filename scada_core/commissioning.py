"""Проверка связи перед пуском: `python run.py --check`.

Для каждого устройства из конфигурации: установлена ли библиотека протокола,
удаётся ли подключиться и сколько тегов читается с хорошим качеством.
Ничего не пишет в устройства и в базу данных.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from scada_core.config.loader import AppConfig, DeviceConfig
from scada_core.drivers import QUALITY_GOOD, DriverUnavailable, create_driver
from scada_core.drivers.base import error_text

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class DeviceReport:
    device_id: str
    protocol: str
    target: str
    status: str
    message: str
    good_tags: int = 0
    total_tags: int = 0


async def check_device(dev: DeviceConfig, wait_s: float | None = None) -> DeviceReport:
    total = len(dev.tags)
    try:
        driver = create_driver(dev)
    except DriverUnavailable as exc:
        return DeviceReport(dev.id, dev.protocol, "", FAIL, str(exc), 0, total)
    target = driver.describe()
    try:
        if not await driver.connect():
            msg = driver.last_error or f"нет ответа за {dev.timeout:g} с"
            return DeviceReport(dev.id, dev.protocol, target, FAIL, msg, 0, total)
        values = await driver.read()
        # Протоколы с передачей по событию (MQTT, МЭК 104) присылают данные
        # не сразу после подключения — даём им время до таймаута устройства.
        deadline = asyncio.get_running_loop().time() + (dev.timeout if wait_s is None else wait_s)
        while _good(values) < total and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.2)
            values = await driver.read()
    except Exception as exc:  # noqa: BLE001 - отчёт, а не падение
        return DeviceReport(dev.id, dev.protocol, target, FAIL, error_text(exc), 0, total)
    finally:
        try:
            await driver.disconnect()
        except Exception:  # noqa: BLE001
            pass
    good = _good(values)
    if good == total:
        return DeviceReport(dev.id, dev.protocol, target, OK, "связь есть", good, total)
    bad = [name for name, (_, q) in values.items() if q != QUALITY_GOOD]
    return DeviceReport(dev.id, dev.protocol, target, WARN, "нет данных: " + ", ".join(bad), good, total)


def _good(values: dict[str, tuple[float | None, str]]) -> int:
    return sum(1 for _, q in values.values() if q == QUALITY_GOOD)


async def check_config(config: AppConfig, wait_s: float | None = None) -> list[DeviceReport]:
    devices = config.active_devices
    return list(await asyncio.gather(*(check_device(d, wait_s) for d in devices)))


def format_report(reports: list[DeviceReport]) -> str:
    marks = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}
    lines = []
    for r in reports:
        target = f" {r.target}" if r.target else ""
        lines.append(
            f"[{marks[r.status]}] {r.device_id} ({r.protocol}{target}): "
            f"{r.good_tags}/{r.total_tags} тегов — {r.message}"
        )
    if not reports:
        lines.append("В конфигурации нет активных устройств")
    return "\n".join(lines)


def exit_code(reports: list[DeviceReport]) -> int:
    """0 — всё в порядке, 1 — есть устройства без связи или без данных."""
    return 0 if reports and all(r.status == OK for r in reports) else 1
