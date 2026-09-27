"""Драйверы протоколов против настоящих серверов/эмуляторов:
чтение, запись, ошибки устройства, потеря связи и восстановление."""

import asyncio
import time

import pytest

from scada_core.config.loader import ConfigError, parse_config
from scada_core.drivers import QUALITY_BAD, QUALITY_COMM, QUALITY_GOOD, QUALITY_STALE, create_driver
from scada_core.drivers.base import parse_payload
from scada_core.drivers.modbus_pdu import crc16, rtu_check, rtu_frame
from scada_core.drivers.mqtt import topic_matches


def device(protocol: str, tags: list[dict], **extra):
    cfg = parse_config(
        {"devices": [{"id": "dev", "protocol": protocol, "timeout": 1.0, **extra, "tags": tags}]}
    )
    return cfg.devices[0]


async def eventually(check, timeout: float = 8.0):
    end = time.monotonic() + timeout
    while True:
        result = await check()
        if result:
            return result
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------- конфигурация
def test_each_protocol_requires_its_address_field() -> None:
    cases = {
        "opcua": ("node", {"endpoint": "opc.tcp://h:4840"}),
        "mqtt": ("topic", {"host": "broker"}),
        "iec104": ("ioa", {"host": "rtu"}),
        "modbus_rtu": ("address", {"serial_port": "/dev/ttyUSB0"}),
    }
    for proto, (field, extra) in cases.items():
        with pytest.raises(ConfigError) as exc:
            device(proto, [{"name": "x"}], **extra)
        assert exc.value.errors == [
            ("devices[0].tags[0]." + field, f"обязательное поле для протокола {proto}")
        ]


def test_device_level_requirements_and_defaults() -> None:
    with pytest.raises(ConfigError) as exc:
        device("opcua", [{"name": "x", "node": "ns=2;s=X"}])
    assert exc.value.errors[0][0] == "devices[0].endpoint"
    with pytest.raises(ConfigError):
        device("profinet", [])
    assert device("mqtt", [], host="b").port == 1883
    assert device("iec104", [], host="r").port == 2404


# ---------------------------------------------------------------- Modbus RTU
def test_crc16_reference_frame() -> None:
    # Классический пример из спецификации Modbus: 01 03 00 00 00 0A C5 CD
    assert rtu_frame(1, bytes.fromhex("030000000A")).hex() == "01030000000ac5cd"
    assert crc16(b"") == 0xFFFF
    with pytest.raises(ValueError):
        rtu_check(bytes.fromhex("01030000000ac5ce"))


async def test_modbus_rtu_over_tcp(port: int) -> None:
    pytest.importorskip("serial")
    from scada_core.sim.datastore import DataStore
    from scada_core.sim.modbus_server import ModbusRTUOverTCPServer

    store = DataStore()
    store.holding[0:3] = [1234, 0xFFFF, 5]
    store.coils[4] = 1
    server = ModbusRTUOverTCPServer(store, "127.0.0.1", port, unit=7)
    await server.start()
    dev = device(
        "modbus_rtu",
        [
            {"name": "level", "address": 0, "scale": 0.1},
            {"name": "signed", "address": 1, "type": "int16"},
            {"name": "sp", "address": 2, "writable": True},
            {"name": "pump", "address": 4, "function": "coil", "writable": True},
            {"name": "missing", "address": 5000},
        ],
        serial_port=f"socket://127.0.0.1:{port}",
        slave_id=7,
        baudrate=115200,
    )
    drv = create_driver(dev)
    try:
        assert await drv.connect()
        values = await drv.read()
        assert values["level"] == (pytest.approx(123.4), QUALITY_GOOD)
        assert values["signed"] == (-1.0, QUALITY_GOOD)
        assert values["pump"] == (1.0, QUALITY_GOOD)
        assert values["missing"] == (None, QUALITY_BAD)  # исключение 0x02, связь жива
        await drv.write(dev.tags[2], 42)
        await drv.write(dev.tags[3], 0)
        assert store.holding[2] == 42 and store.coils[4] == 0

        await server.stop()  # шлюз пропал
        assert (await drv.read())["level"][1] == QUALITY_COMM
        assert not drv.is_connected
        server = ModbusRTUOverTCPServer(store, "127.0.0.1", port, unit=7)
        await server.start()
        values = await eventually(lambda: _read_good(drv, "level"))
        assert values["level"][0] == pytest.approx(123.4)
    finally:
        await drv.disconnect()
        await server.stop()


async def _read_good(drv, name):
    await drv.connect()
    values = await drv.read()
    return values if values[name][1] == QUALITY_GOOD else None


# ---------------------------------------------------------------- OPC UA
async def test_opcua(port: int) -> None:
    pytest.importorskip("asyncua")
    from asyncua import Server, ua

    async def make_server():
        server = Server()
        await server.init()
        server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/scada/")
        idx = await server.register_namespace("urn:scada:test")
        obj = await server.nodes.objects.add_object(idx, "Plant")
        nodes = {
            "level": await obj.add_variable(ua.NodeId("Tank.Level", idx), "Tank.Level", 55.5),
            "pump": await obj.add_variable(ua.NodeId("Pump.Run", idx), "Pump.Run", True),
            "count": await obj.add_variable(
                ua.NodeId("Pump.Starts", idx), "Pump.Starts", ua.Variant(7, ua.VariantType.Int16)
            ),
        }
        for n in nodes.values():
            await n.set_writable()
        await server.start()
        return server, nodes, idx

    server, nodes, idx = await make_server()
    dev = device(
        "opcua",
        [
            {"name": "level", "node": f"ns={idx};s=Tank.Level", "writable": True},
            {"name": "pump", "node": f"ns={idx};s=Pump.Run", "type": "bool", "writable": True},
            {"name": "starts", "node": f"ns={idx};s=Pump.Starts", "writable": True},
            {"name": "ghost", "node": f"ns={idx};s=No.Such.Node"},
        ],
        endpoint=f"opc.tcp://127.0.0.1:{port}/scada/",
    )
    drv = create_driver(dev)
    try:
        assert await drv.connect()
        values = await drv.read()
        assert values["level"] == (55.5, QUALITY_GOOD)
        assert values["pump"] == (1.0, QUALITY_GOOD)
        assert values["starts"] == (7.0, QUALITY_GOOD)
        assert values["ghost"] == (None, QUALITY_BAD)  # BadNodeIdUnknown, остальные теги живы
        await drv.write(dev.tags[0], 61.25)
        await drv.write(dev.tags[1], 0)
        await drv.write(dev.tags[2], 9)
        assert await nodes["level"].read_value() == 61.25
        assert await nodes["pump"].read_value() is False
        assert await nodes["count"].read_value() == 9  # тип Int16 сохранён

        await server.stop()
        values = await drv.read()
        assert values["level"][1] == QUALITY_COMM and not drv.is_connected
        server, nodes, idx = await make_server()
        assert await eventually(drv.connect)
        assert (await drv.read())["level"] == (55.5, QUALITY_GOOD)
    finally:
        await drv.disconnect()
        await server.stop()


# ---------------------------------------------------------------- MQTT
def test_mqtt_topic_matching_and_payloads() -> None:
    assert topic_matches("plant/+/level", "plant/tank1/level")
    assert topic_matches("plant/#", "plant/a/b/c")
    assert not topic_matches("plant/+/level", "plant/tank1/temp")
    assert parse_payload(b" 12.5 ", None) == 12.5
    assert parse_payload(b"ON", None) == "ON"
    from scada_core.config.loader import TagConfig
    from scada_core.drivers.base import to_engineering

    bit, num = TagConfig(name="b", type="bool"), TagConfig(name="n")
    assert to_engineering(bit, "ON") == 1.0 and to_engineering(bit, "off") == 0.0
    for bad_tag, bad in ((bit, "maybe"), (num, "not a number")):
        with pytest.raises(ValueError):
            to_engineering(bad_tag, bad)  # ошибка данных -> качество BAD, не «ноль»
    assert parse_payload(b'{"data": {"items": [{"v": 3}]}}', "data.items.0.v") == 3


async def test_mqtt(port: int) -> None:
    pytest.importorskip("aiomqtt")
    from scada_core.sim.mqtt_broker import MiniMqttBroker

    broker = MiniMqttBroker("127.0.0.1", port)
    await broker.start()
    broker.publish("plant/tank/level", "48.2", retain=True)
    dev = device(
        "mqtt",
        [
            {"name": "level", "topic": "plant/tank/level"},
            {"name": "temp", "topic": "plant/+/telemetry", "json_path": "temp", "scale": 0.1},
            {"name": "sp", "topic": "plant/tank/sp", "writable": True, "command_topic": "plant/tank/sp/cmd"},
            {"name": "pump", "topic": "plant/pump/state", "type": "bool", "writable": True},
        ],
        host="127.0.0.1",
        port=port,
        stale_s=1.5,
    )
    drv = create_driver(dev)
    try:
        assert await drv.connect()
        values = await eventually(lambda: _mqtt_value(drv, "level"))
        assert values["level"] == (48.2, QUALITY_GOOD)  # retained-сообщение
        assert values["temp"][1] == QUALITY_STALE  # ещё не приходило
        broker.publish("plant/boiler/telemetry", '{"temp": 655}')
        broker.publish("plant/pump/state", "on")
        values = await eventually(lambda: _mqtt_value(drv, "temp"))
        assert values["temp"] == (pytest.approx(65.5), QUALITY_GOOD)
        assert values["pump"] == (1.0, QUALITY_GOOD)
        broker.publish("plant/tank/level", "not a number")
        await eventually(lambda: _mqtt_quality(drv, "level", QUALITY_BAD))

        await drv.write(dev.tags[2], 55)
        await drv.write(dev.tags[3], 0)
        await eventually(lambda: _published(broker, "plant/pump/state/set"))
        sent = dict(broker.published)
        assert float(sent["plant/tank/sp/cmd"]) == 55.0 and sent["plant/pump/state/set"] == b"0"

        await asyncio.sleep(1.6)
        assert (await drv.read())["temp"][1] == QUALITY_STALE  # давно не обновлялось

        await broker.stop()
        await eventually(lambda: _mqtt_quality(drv, "level", QUALITY_COMM))
    finally:
        await drv.disconnect()
        await broker.stop()


async def _mqtt_value(drv, name):
    values = await drv.read()
    return values if values[name][1] == QUALITY_GOOD else None


async def _mqtt_quality(drv, name, quality):
    return (await drv.read())[name][1] == quality


async def _published(broker, topic):
    return any(t == topic for t, _ in broker.published)


# ---------------------------------------------------------------- МЭК 60870-5-104
async def test_iec104(port: int) -> None:
    c104 = pytest.importorskip("c104")

    def make_server():
        srv = c104.Server(ip="127.0.0.1", port=port)
        st = srv.add_station(common_address=5)
        pts = {
            "level": st.add_point(io_address=1001, type=c104.Type.M_ME_NC_1),
            "breaker": st.add_point(io_address=2001, type=c104.Type.M_SP_NA_1),
            "count": st.add_point(io_address=3001, type=c104.Type.M_ME_NB_1),
        }
        pts["level"].value = 12.5
        pts["breaker"].value = True
        pts["count"].value = c104.Int16(42)
        received: list[float] = []
        cmd = st.add_point(io_address=5001, type=c104.Type.C_SE_NC_1)

        def on_cmd(
            point: c104.Point, previous_info: c104.Information, message: c104.IncomingMessage
        ) -> c104.ResponseState:
            received.append(point.value)
            return c104.ResponseState.SUCCESS

        cmd.on_receive(callable=on_cmd)
        srv.start()
        return srv, pts, received

    srv, pts, received = make_server()
    dev = device(
        "iec104",
        [
            {"name": "level", "ioa": 1001, "type": "float", "writable": True, "command_ioa": 5001},
            {"name": "breaker", "ioa": 2001, "type": "bool"},
            {"name": "count", "ioa": 3001, "type": "int16", "scale": 0.5},
            {"name": "unknown", "ioa": 9999},
        ],
        host="127.0.0.1",
        port=port,
        common_address=5,
        timeout=3.0,
    )
    drv = create_driver(dev)
    try:
        assert await drv.connect()
        values = await eventually(lambda: _read_good(drv, "level"))  # после общего опроса
        assert values["level"] == (12.5, QUALITY_GOOD)
        assert values["breaker"] == (1.0, QUALITY_GOOD)
        assert values["count"] == (21.0, QUALITY_GOOD)
        assert values["unknown"] == (None, QUALITY_BAD)
        pts["level"].value = 20.25
        pts["level"].transmit(cause=c104.Cot.SPONTANEOUS)
        await eventually(lambda: _iec_value(drv, "level", 20.25))
        await drv.write(dev.tags[0], 77.5)
        assert received == [77.5]

        # «Немое» соединение (TCP открыт, STARTDT снят) драйвер восстанавливает сам.
        await asyncio.get_running_loop().run_in_executor(None, drv._conn.mute)
        assert drv._conn.is_muted
        pts["level"].value = 30.5  # пока соединение «немое», это значение не дойдёт

        async def recovered():
            values = await drv.read()  # драйвер снимает немоту и делает общий опрос
            return values["level"][0] == 30.5 and not drv._conn.is_muted

        await eventually(recovered)
        with pytest.raises(PermissionError):
            await drv.write(dev.tags[1], 1)  # нет command_ioa — запись запрещена

        srv.stop()
        await eventually(lambda: _mqtt_quality(drv, "level", QUALITY_COMM))
    finally:
        await drv.disconnect()


async def _iec_value(drv, name, value):
    return (await drv.read())[name][0] == value


# ---------------------------------------------------------------- система целиком
async def test_poller_mixes_protocols_and_survives_missing_library(port: int, monkeypatch) -> None:
    """Устройство с неустановленной библиотекой не мешает остальным."""
    import scada_core.drivers.opcua as opcua_mod
    from scada_core.drivers import base
    from scada_core.engine.data_poller import DataPoller
    from scada_core.sim import Simulator

    def unavailable(self, device):
        raise base.DriverUnavailable("OPC UA: установите asyncua")

    monkeypatch.setattr(opcua_mod.OpcUaDriver, "__init__", unavailable)
    cfg = parse_config(
        {
            "devices": [
                {
                    "id": "plc",
                    "host": "127.0.0.1",
                    "port": port,
                    "poll_interval_ms": 200,
                    "tags": [{"name": "level", "address": 0, "scale": 0.1}],
                },
                {
                    "id": "ua",
                    "protocol": "opcua",
                    "endpoint": "opc.tcp://127.0.0.1:1/",
                    "poll_interval_ms": 200,
                    "tags": [{"name": "x", "node": "ns=2;s=X"}],
                },
            ]
        }
    )
    sim = Simulator(port=port)
    await sim.start()
    seen: dict[str, str] = {}
    poller = DataPoller(cfg)
    poller.add_callback("data", lambda values: seen.update({v.key: v.quality for v in values}))
    await poller.start()
    try:

        async def both():
            return seen.get("plc/level") == QUALITY_GOOD and seen.get("ua/x") == QUALITY_COMM

        await eventually(both)
        assert poller.device_online("plc") and not poller.device_online("ua")
        assert "asyncua" in poller.device_error("ua")
    finally:
        await poller.stop()
        await sim.stop()
