"""REST API в демо-режиме: runtime + эмулятор в одном event loop."""

import asyncio
import json

import httpx
import pytest

from scada_core.api.server import create_app
from scada_core.config.loader import AppConfig
from scada_core.runtime import ScadaRuntime


@pytest.fixture
async def client(config: AppConfig, monkeypatch):
    monkeypatch.delenv("SCADA_API_TOKEN", raising=False)
    rt = ScadaRuntime(config, demo=True)
    await rt.start()
    app = create_app(rt)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.runtime = rt  # type: ignore[attr-defined]
        for _ in range(100):
            if rt.store.latest.get("plc_main/tank_level") and rt.poller.cycles > 1:
                break
            await asyncio.sleep(0.05)
        yield c
    await rt.stop()


async def test_meta_and_hmi(client) -> None:
    meta = (await client.get("/api/meta")).json()
    assert meta["demo"] is True and meta["ml_backend"] in ("cpp", "python")
    assert "bearing_wear" in meta["faults"]
    hmi = (await client.get("/api/hmi")).json()
    assert hmi["stats"]["areas"] == 4


async def test_snapshot_and_history(client) -> None:
    snap = (await client.get("/api/snapshot")).json()
    tag = snap["tags"]["plc_main/tank_level"]
    assert tag["quality"] == "GOOD" and tag["ml"]["ready"]  # ML прогрет заранее
    hist = (await client.get("/api/history/plc_main/tank_level?seconds=900")).json()
    assert len(hist["ts"]) >= 600  # история прогрева попадает в тренды
    assert hist["limits"]["LL"] == 10
    assert (await client.get("/api/history/plc_main/nope")).status_code == 404


async def test_generator_preview(client) -> None:
    ok = await client.post(
        "/api/hmi/preview", json={"yaml": "devices: [{id: a, host: h, tags: [{name: x, address: 0}]}]"}
    )
    assert ok.status_code == 200 and ok.json()["hmi"]["stats"]["tags"] == 1
    bad = await client.post(
        "/api/hmi/preview", json={"yaml": "devices: [{id: a, host: h, tags: [{name: x}]}]"}
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["path"] == "devices[0].tags[0].address"


async def test_write_validation_and_effect(client) -> None:
    ro = await client.post("/api/write", json={"device": "plc_main", "tag": "tank_level", "value": 1})
    assert ro.status_code == 403
    out = await client.post("/api/write", json={"device": "plc_main", "tag": "level_setpoint", "value": 99})
    assert out.status_code == 422
    ok = await client.post("/api/write", json={"device": "plc_main", "tag": "level_setpoint", "value": 55})
    assert ok.status_code == 200
    assert client.runtime.simulator.store.holding[10] == 550
    pump = await client.post("/api/write", json={"device": "plc_main", "tag": "pump_running", "value": 0})
    assert pump.status_code == 200 and client.runtime.simulator.store.coils[0] == 0


async def test_token_protects_control(config, monkeypatch) -> None:
    monkeypatch.setenv("SCADA_API_TOKEN", "s3cret")
    rt = ScadaRuntime(config, demo=False, use_db=False, model_dir=None)
    app = create_app(rt)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/api/alarms/ack_all")).status_code == 401
        assert (await c.post("/api/alarms/ack_all", headers={"X-API-Token": "s3cret"})).status_code == 200
        assert (await c.get("/api/alarms")).status_code == 200  # чтение открыто
        assert (await c.get("/api/meta")).json()["auth_required"] is True


async def test_fault_injection_and_alarm_ack(client) -> None:
    r = await client.post("/api/sim/fault", json={"fault": "pump_trip"})
    assert r.json()["active"] == ["pump_trip"]
    rt = client.runtime
    for _ in range(80):  # давление падает сразу после останова насоса
        if any(a.kind == "L" and a.tag == "discharge_pressure" for a in rt.alarms.visible_alarms()):
            break
        await asyncio.sleep(0.1)
    alarms = (await client.get("/api/alarms")).json()["active"]
    low = next(a for a in alarms if a["tag"] == "discharge_pressure" and a["kind"] == "L")
    assert low["device_id"] == "plc_main"
    assert (await client.post(f"/api/alarms/{low['id']}/ack")).json()["ok"]
    kpi = (await client.get("/api/alarms/kpi")).json()
    assert kpi["rate_last_10min"] >= 1
    assert (await client.delete("/api/sim/fault")).json()["active"] == []
    assert (await client.post("/api/sim/fault", json={"fault": "nope"})).status_code == 404


async def test_i18n_dictionaries_are_complete(client) -> None:
    dicts = {}
    for lang in ("ru", "en", "zh"):
        r = await client.get(f"/i18n/{lang}.json")
        assert r.status_code == 200
        dicts[lang] = json.loads(r.text)
    assert set(dicts["ru"]) == set(dicts["en"]) == set(dicts["zh"])
    for lang, d in dicts.items():
        for key, text in d.items():
            placeholders = sorted(p for p in text.split("{")[1:])
            ru_placeholders = sorted(p for p in dicts["ru"][key].split("{")[1:])
            assert [p.split("}")[0] for p in placeholders] == [p.split("}")[0] for p in ru_placeholders], (
                lang,
                key,
            )
    index = await client.get("/")
    assert index.status_code == 200 and "SCADA Generator" in index.text
