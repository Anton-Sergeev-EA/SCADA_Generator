"""Драйвер МЭК 60870-5-104 — телемеханика, энергетика, подстанции.
Библиотека c104 (GPL-3.0): ставится отдельно (pip install c104). Если вы
распространяете сборку с этим драйвером, на неё распространяется GPL-3.0.

Устройство: protocol: iec104, host, port (2404), common_address (1)
Тег: ioa: 1001, type: float (M_ME_NC_1) | scaled (M_ME_NB_1) | bool (M_SP_NA_1)
     command_ioa: 5001 — адрес команды для записи (уставка C_SE_NC_1/C_SE_NB_1
     или команда C_SC_NA_1); по умолчанию запись запрещена.
При подключении выполняется общий опрос (general interrogation), дальше
значения приходят спорадически.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from scada_core.config.loader import DeviceConfig, TagConfig
from scada_core.drivers.base import (
    QUALITY_BAD,
    QUALITY_COMM,
    QUALITY_GOOD,
    Driver,
    DriverError,
    DriverUnavailable,
    error_text,
    to_engineering,
    to_raw,
)

logger = logging.getLogger(__name__)


class Iec104Driver(Driver):
    protocol = "iec104"

    def __init__(self, device: DeviceConfig) -> None:
        super().__init__(device)
        try:
            import c104
        except ImportError as exc:
            raise DriverUnavailable("МЭК 104: установите c104 (pip install c104, GPL-3.0)") from exc
        self._c104 = c104
        self._client: Any = None
        self._conn: Any = None
        self._points: dict[str, Any] = {}
        self._created: dict[str, Any] = {}  # processed_at точки при создании
        self._commands: dict[str, Any] = {}

    def _types(self, tag: TagConfig) -> tuple[Any, Any]:
        t = self._c104.Type
        if tag.is_bit:
            return t.M_SP_NA_1, t.C_SC_NA_1
        if tag.type in ("int16", "uint16", "scaled"):
            return t.M_ME_NB_1, t.C_SE_NB_1
        return t.M_ME_NC_1, t.C_SE_NC_1

    def _start(self) -> None:
        c104 = self._c104
        client = c104.Client(tick_rate_ms=100, command_timeout_ms=int(self.device.timeout * 1000))
        conn = client.add_connection(ip=self.device.host, port=self.device.port, init=c104.Init.ALL)
        station = conn.add_station(common_address=int(self.device.options.get("common_address", 1)))
        for tag in self.device.tags:
            mtype, ctype = self._types(tag)
            point = station.add_point(io_address=tag.ioa, type=mtype)
            self._points[tag.name] = point
            self._created[tag.name] = point.processed_at
            if tag.writable and tag.command_ioa is not None:
                self._commands[tag.name] = station.add_point(io_address=tag.command_ioa, type=ctype)
        client.start()  # собственный поток библиотеки, переподключается сам
        self._client, self._conn = client, conn

    async def connect(self) -> bool:
        if self._client is None:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self._start)
            except Exception as exc:  # noqa: BLE001
                self.last_error = error_text(exc)
                return False
        for _ in range(int(self.device.timeout * 10)):
            if self._conn.is_connected:
                break
            await asyncio.sleep(0.1)
        if self._conn.is_connected:
            await self._ensure_active()
        self._connected = bool(self._conn.is_connected)
        self.last_error = None if self._connected else f"no link to {self.describe()}"
        return self._connected

    async def _ensure_active(self) -> None:
        """Соединение может остаться «немым» (OPEN_MUTED): TCP открыт, но
        STARTDT не подтверждён и данные не идут — например, после
        перезапуска КП. Снимаем немоту и повторяем общий опрос."""
        conn = self._conn
        if conn is None or not conn.is_muted:
            return
        ca = int(self.device.options.get("common_address", 1))
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, conn.unmute)
            await loop.run_in_executor(None, lambda: conn.interrogation(common_address=ca))
            logger.info("IEC 104 %s: STARTDT + general interrogation", self.describe())
        except Exception as exc:  # noqa: BLE001
            self.last_error = error_text(exc)

    async def read(self) -> dict[str, tuple[float | None, str]]:
        if self._conn is None or not self._conn.is_connected:
            self._connected = False
            return {t.name: (None, QUALITY_COMM) for t in self.device.tags}
        await self._ensure_active()
        out: dict[str, tuple[float | None, str]] = {}
        for tag in self.device.tags:
            point = self._points[tag.name]
            if point.processed_at == self._created[tag.name]:  # значение ещё не приходило
                out[tag.name] = (None, QUALITY_BAD)
                continue
            raw = point.value
            raw = bool(raw) if tag.is_bit else float(int(raw) if not isinstance(raw, float) else raw)
            quality = QUALITY_GOOD if point.quality.is_good else QUALITY_BAD
            out[tag.name] = (to_engineering(tag, raw), quality)
        return out

    async def write(self, tag: TagConfig, value: float) -> None:
        point = self._commands.get(tag.name)
        if point is None:
            raise PermissionError("для записи в МЭК 104 задайте command_ioa у тега")
        c104 = self._c104
        raw = to_raw(tag, value)
        if tag.is_bit:
            point.value = bool(raw)
        elif point.type == c104.Type.C_SE_NB_1:
            point.value = c104.Int16(round(raw))
        else:
            point.value = float(raw)
        ok = await asyncio.get_running_loop().run_in_executor(
            None, lambda: point.transmit(cause=c104.Cot.ACTIVATION)
        )
        if not ok:
            raise DriverError("команда отклонена или не подтверждена")

    async def disconnect(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await asyncio.get_running_loop().run_in_executor(None, client.stop)
        self._conn = None
        self._connected = False
