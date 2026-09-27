"""`python run.py --check`: отчёт о связи с каждым устройством."""

import pytest
from conftest import free_port

from scada_core.commissioning import FAIL, OK, WARN, check_config, exit_code, format_report
from scada_core.config.loader import parse_config
from scada_core.sim.datastore import DataStore
from scada_core.sim.modbus_server import ModbusTCPServer


async def test_check_reports_every_device(port: int, monkeypatch) -> None:
    pytest.importorskip("aiomqtt")
    from scada_core.sim.mqtt_broker import MiniMqttBroker

    server = ModbusTCPServer(DataStore(), "127.0.0.1", port)
    await server.start()
    mqtt_port = free_port()
    broker = MiniMqttBroker("127.0.0.1", mqtt_port)
    await broker.start()
    broker.publish("site/temp", "21.5", retain=True)
    cfg = parse_config(
        {
            "devices": [
                {
                    "id": "plc",
                    "host": "127.0.0.1",
                    "port": port,
                    "timeout": 1.0,
                    "tags": [{"name": "level", "address": 0}],
                },
                {
                    "id": "sensors",
                    "protocol": "mqtt",
                    "host": "127.0.0.1",
                    "port": mqtt_port,
                    "timeout": 1.0,
                    "tags": [{"name": "temp", "topic": "site/temp"}, {"name": "hum", "topic": "site/hum"}],
                },
                {
                    "id": "gone",
                    "host": "127.0.0.1",
                    "port": free_port(),
                    "timeout": 0.5,
                    "retries": 1,
                    "tags": [{"name": "x", "address": 0}],
                },
                {"id": "sub", "protocol": "iec104", "host": "127.0.0.1", "tags": [{"name": "u", "ioa": 1}]},
            ]
        }
    )
    import scada_core.drivers.iec104 as iec

    def missing(*_a, **_k):
        raise iec.DriverUnavailable("МЭК 104: установите c104")

    monkeypatch.setattr(iec.Iec104Driver, "__init__", missing)
    try:
        reports = {r.device_id: r for r in await check_config(cfg, wait_s=1.0)}
    finally:
        await server.stop()
        await broker.stop()

    assert reports["plc"].status == OK and reports["plc"].good_tags == 1
    assert reports["sensors"].status == WARN and "hum" in reports["sensors"].message
    assert reports["sensors"].good_tags == 1
    assert reports["gone"].status == FAIL
    assert reports["sub"].status == FAIL and "c104" in reports["sub"].message
    assert exit_code(list(reports.values())) == 1
    text = format_report(list(reports.values()))
    assert "[OK  ] plc (modbus_tcp 127.0.0.1:" in text and "[FAIL] sub" in text


async def test_check_all_ok_and_cli(port: int, capsys) -> None:
    import run

    server = ModbusTCPServer(DataStore(), "127.0.0.1", port)
    await server.start()
    try:
        cfg = parse_config(
            {
                "devices": [
                    {
                        "id": "plc",
                        "host": "127.0.0.1",
                        "port": port,
                        "tags": [{"name": "level", "address": 0}],
                    }
                ]
            }
        )
        assert exit_code(await check_config(cfg)) == 0
    finally:
        await server.stop()
    assert exit_code([]) == 1  # пустая конфигурация — не «всё хорошо»
    assert run.parse_args(["--check"]).check is True


def test_cli_exit_code_without_connection(tmp_path, capsys) -> None:
    import run

    cfg = tmp_path / "plant.yaml"
    cfg.write_text(
        f"devices: [{{id: plc, host: 127.0.0.1, port: {free_port()}, timeout: 0.5, retries: 1,"
        " tags: [{name: level, address: 0}]}]",
        encoding="utf-8",
    )
    assert run.main(["--check", "--config", str(cfg)]) == 1
    assert "[FAIL] plc" in capsys.readouterr().out
