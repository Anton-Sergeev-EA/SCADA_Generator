"""Драйвер OPC UA (IEC 62541) — открытый стандарт, который поддерживают ПЛК
и SCADA большинства производителей. Библиотека asyncua (LGPL-3.0).

Конфигурация устройства:
    protocol: opcua
    endpoint: opc.tcp://192.168.1.10:4840
    username / password: ${OPCUA_USER} / ${OPCUA_PASSWORD}   (необязательно)
    security: Basic256Sha256,SignAndEncrypt,client_cert.der,client_key.pem  (необязательно)
Тег: node: "ns=2;s=Tank.Level"
"""

from __future__ import annotations

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
    to_engineering,
    to_raw,
)

logger = logging.getLogger(__name__)


class OpcUaDriver(Driver):
    protocol = "opcua"

    def __init__(self, device: DeviceConfig) -> None:
        super().__init__(device)
        try:
            import asyncua  # noqa: F401
        except ImportError as exc:
            raise DriverUnavailable("OPC UA: установите asyncua (pip install asyncua)") from exc
        self.endpoint = str(device.options["endpoint"])
        self._client: Any = None
        self._nodes: list[Any] = []
        self._types: dict[str, Any] = {}

    def describe(self) -> str:
        return self.endpoint

    async def connect(self) -> bool:
        if self._client is not None and self._connected:
            return True
        from asyncua import Client

        o = self.device.options
        client = Client(url=self.endpoint, timeout=self.device.timeout)
        if o.get("username"):
            client.set_user(str(o["username"]))
            client.set_password(str(o.get("password", "")))
        try:
            if o.get("security"):
                await client.set_security_string(str(o["security"]))
            await client.connect()
            self._nodes = [client.get_node(t.node) for t in self.device.tags]
            self._client = client
            self._connected = True
            self.last_error = None
            logger.info("OPC UA connected: %s", self.endpoint)
        except Exception as exc:  # noqa: BLE001 - сеть, сертификаты, авторизация
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._connected = False
            await self._safe_disconnect(client)
        return self._connected

    async def read(self) -> dict[str, tuple[float | None, str]]:
        tags = self.device.tags
        if not self._connected or self._client is None:
            return {t.name: (None, QUALITY_COMM) for t in tags}
        try:
            # Одно обращение на все теги устройства.
            values = await self._client.read_attributes(self._nodes)
        except Exception as exc:  # noqa: BLE001 - потеря сессии
            self.last_error = f"{type(exc).__name__}: {exc}"
            await self._drop()
            return {t.name: (None, QUALITY_COMM) for t in tags}
        out: dict[str, tuple[float | None, str]] = {}
        for tag, dv in zip(tags, values, strict=True):
            if not dv.StatusCode.is_good():
                out[tag.name] = (None, QUALITY_BAD)
                continue
            try:
                out[tag.name] = (to_engineering(tag, dv.Value.Value), QUALITY_GOOD)
            except (TypeError, ValueError):
                out[tag.name] = (None, QUALITY_BAD)
        return out

    async def write(self, tag: TagConfig, value: float) -> None:
        if not await self.connect():
            raise DriverError(self.last_error or "OPC UA server is unreachable")
        from asyncua import ua

        node = self._client.get_node(tag.node)
        try:
            vtype = self._types.get(tag.name)
            if vtype is None:
                vtype = self._types[tag.name] = await node.read_data_type_as_variant_type()
            raw = to_raw(tag, value)
            if vtype in (ua.VariantType.Float, ua.VariantType.Double):
                raw = float(raw)
            elif vtype != ua.VariantType.Boolean:
                raw = round(raw)
            await node.write_value(ua.DataValue(ua.Variant(raw, vtype)))
        except Exception as exc:
            raise DriverError(f"{type(exc).__name__}: {exc}") from exc

    async def _drop(self) -> None:
        client, self._client = self._client, None
        self._connected = False
        await self._safe_disconnect(client)

    @staticmethod
    async def _safe_disconnect(client: Any) -> None:
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - сессия уже мертва
            pass

    async def disconnect(self) -> None:
        await self._drop()
