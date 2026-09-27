"""PostgreSQL: миграции на базе версии 0.x и запись данных.

Запускается, если задан SCADA_TEST_PG (например, в CI:
SCADA_TEST_PG=postgresql://scada:pass@localhost:5432/postgres). Для
каждого теста создаётся отдельная временная база.
"""

import os
import time
import uuid
from datetime import datetime, timezone

import pytest

asyncpg = pytest.importorskip("asyncpg")
DSN = os.getenv("SCADA_TEST_PG")
pytestmark = pytest.mark.skipif(not DSN, reason="SCADA_TEST_PG не задан")

from scada_core.database.repository import DatabaseRepository  # noqa: E402
from scada_core.engine.alarms import AlarmEngine  # noqa: E402

LEGACY_SCHEMA = """
CREATE TABLE tags (id SERIAL PRIMARY KEY, device_id VARCHAR(100) NOT NULL,
                   tag_name VARCHAR(100) NOT NULL, UNIQUE(device_id, tag_name));
CREATE TABLE tag_history (id BIGSERIAL PRIMARY KEY, tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                          value FLOAT, quality VARCHAR(20) DEFAULT 'GOOD',
                          timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE alarms (id BIGSERIAL PRIMARY KEY, tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                     alarm_type VARCHAR(20) NOT NULL, value FLOAT, message TEXT,
                     acknowledged BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""


@pytest.fixture
async def db():
    admin = await asyncpg.connect(DSN)
    name = f"scada_test_{uuid.uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    params = admin._params  # noqa: SLF001 - берём параметры подключения из DSN
    addr = admin._addr  # noqa: SLF001
    cfg = {
        "host": addr[0],
        "port": addr[1],
        "name": name,
        "user": params.user,
        "password": params.password,
    }
    try:
        yield cfg
    finally:
        await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name)
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def connect(cfg):
    return await asyncpg.connect(
        host=cfg["host"], port=cfg["port"], database=cfg["name"], user=cfg["user"], password=cfg["password"]
    )


async def test_migrates_legacy_database_without_losing_time(db) -> None:
    conn = await connect(db)
    await conn.execute(LEGACY_SCHEMA)
    await conn.execute("SET TIME ZONE 'Europe/Moscow'")
    await conn.execute("INSERT INTO tags (device_id, tag_name) VALUES ('plc_main', 'temperature')")
    await conn.execute(
        "INSERT INTO tag_history (tag_id, value, timestamp) VALUES (1, 123, '2026-09-20 10:00:00')"
    )
    await conn.execute(f"ALTER DATABASE \"{db['name']}\" SET timezone TO 'Europe/Moscow'")
    await conn.close()

    repo = DatabaseRepository(db)
    await repo.initialize()
    assert await repo.migrate() == []  # повторный запуск ничего не делает
    await repo.close()

    conn = await connect(db)
    versions = [r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions == ["001_initial", "002_timezone_indexes_lifecycle"]
    ts = await conn.fetchval("SELECT timestamp FROM tag_history")
    assert ts == datetime(2026, 9, 20, 7, 0, tzinfo=timezone.utc)  # 10:00 МСК
    idx = await conn.fetchval("SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_tag_history_tag_ts'")
    assert idx == 1
    await conn.close()


async def test_values_alarms_and_insights_are_written(db) -> None:
    repo = DatabaseRepository(db)
    await repo.initialize()
    await repo.register_tags([("plc_main", "tank_level", {"unit": "%", "label": {"ru": "Уровень"}})])
    now = time.time()
    for i in range(50):
        repo.enqueue("plc_main", "tank_level", 50.0 + i, "GOOD", now + i)
    repo.enqueue("plc_main", "tank_level", None, "COMM_FAIL", now + 60)
    assert await repo.flush() == 51
    hist = await repo.get_tag_history("plc_main", "tank_level", limit=5)
    assert hist[0]["quality"] == "COMM_FAIL" and hist[1]["value"] == 99.0

    engine = AlarmEngine()
    events = []
    engine.subscribe(events.append)
    engine.set_condition("plc_main", "tank_level", "H", True, value=99, limit=85, ts=now)
    engine.set_condition("plc_main", "tank_level", "H", False, value=80, ts=now + 5)
    engine.acknowledge(engine.visible_alarms()[0].id, "ivanov", ts=now + 9)
    for ev in events:
        await repo.save_alarm_event(ev.event, ev.alarm.to_dict(), ev.ts)

    await repo.save_insight(
        "open",
        {
            "id": 1,
            "device_id": "plc_main",
            "tag": "tank_level",
            "kind": "drift",
            "severity": "warning",
            "code": "ml.drift_up",
            "params": {"sigma": 3.2},
            "peak": 0.6,
            "started_at": now,
        },
    )
    await repo.save_insight(
        "close",
        {"id": 1, "params": {"sigma": 4.0}, "peak": 0.7, "ended_at": now + 30},
    )
    await repo.close()

    conn = await connect(db)
    row = await conn.fetchrow("SELECT * FROM alarms")
    assert row["device_id"] == "plc_main" and row["alarm_type"] == "H"
    assert row["acknowledged"] and row["acked_by"] == "ivanov" and row["cleared_at"] is not None
    assert await conn.fetchval("SELECT count(*) FROM alarms") == 1  # одна строка на активацию
    ins = await conn.fetchrow("SELECT * FROM ml_insights")
    assert ins["ended_at"] is not None and ins["peak"] == 0.7
    await conn.close()
