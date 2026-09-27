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


async def test_health_endpoints(client) -> None:
    assert (await client.get("/api/health/live")).json() == {"status": "ok"}
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["devices"] == {"plc_main": True}
    assert body["database"] is None  # в демо архив не настроен — это не ошибка
    await client.runtime.simulator.stop()  # «обрыв связи» с ПЛК
    for _ in range(100):
        if not client.runtime.poller.device_online("plc_main"):
            break
        await asyncio.sleep(0.05)
    r = await client.get("/api/health")
    assert r.status_code == 503 and r.json()["devices"] == {"plc_main": False}
    assert r.json()["device_errors"]["plc_main"]  # причина видна мониторингу


async def test_ml_and_shelve_endpoints(client) -> None:
    assert (await client.post("/api/ml/rebase/plc_main?tag=tank_level")).json() == {"ok": True}
    assert (await client.post("/api/ml/rebase/nope")).json() == {"ok": False}
    assert (await client.post("/api/ml/retrain")).json() == {"retraining": ["plc_main"]}
    status = (await client.get("/api/ml/status")).json()
    assert status["devices"]["plc_main"]["pca"]["trained"] is False
    rt = client.runtime
    rt.alarms.set_condition("plc_main", "tank_level", "H", True, value=90, limit=85)
    alarm = rt.alarms.visible_alarms()[0]
    assert (await client.post(f"/api/alarms/{alarm.id}/shelve", json={"seconds": 60})).json()["ok"]
    assert (await client.get("/api/alarms/kpi")).json()["shelved"] == 1
    assert (await client.post("/api/alarms/1/shelve", json={"seconds": 1})).status_code == 422


def test_websocket_sends_snapshot_first(config) -> None:
    from fastapi.testclient import TestClient

    rt = ScadaRuntime(config, demo=False, use_db=False, model_dir=None)
    with TestClient(create_app(rt)) as tc, tc.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert "plc_main/tank_level" in msg["data"]["tags"]


async def test_archive_events_are_written_in_order(config) -> None:
    """Регрессия: raise/clear писались параллельными задачами, и UPDATE
    «возврат» мог выполниться раньше INSERT «аларм»."""
    log: list[str] = []

    class SlowRepo:
        connected = True

        async def save_alarm_event(self, event, alarm, ts):
            log.append(f"start:{event}")
            await asyncio.sleep(0.05 if event == "raise" else 0)
            log.append(f"end:{event}")

        async def save_insight(self, event, data):
            pass

        async def close(self):
            pass

    rt = ScadaRuntime(config, demo=False, use_db=False, model_dir=None)
    rt.attach_repository(SlowRepo())
    rt.alarms.set_condition("plc_main", "tank_level", "H", True, ts=1)
    rt.alarms.set_condition("plc_main", "tank_level", "H", False, ts=2)
    await rt.stop()
    assert log == ["start:raise", "end:raise", "start:clear", "end:clear"]


async def test_preview_does_not_leak_server_environment(client, monkeypatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "top-secret")
    yaml_text = "devices: [{id: a, host: h, tags: [{name: x, address: 0, label: '${DB_PASSWORD}'}]}]"
    r = await client.post("/api/hmi/preview", json={"yaml": yaml_text})
    assert r.status_code == 200
    assert "top-secret" not in r.text
