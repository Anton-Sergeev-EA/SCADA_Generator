"""
Асинхронный репозиторий PostgreSQL.

* схема ведётся версионными миграциями (migrations/*.sql, таблица
  schema_migrations) вместо CREATE TABLE в коде;
* значения пишутся пачками (executemany) из фоновой очереди — поллер не
  ждёт базу, а при недоступности БД данные не теряются сразу, а копятся
  в ограниченном буфере;
* id тегов кешируются — раньше на каждое значение уходило 2 запроса;
* все запросы параметризованы.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from scada_core.config.loader import database_config

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def _dt(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None


class DatabaseRepository:
    def __init__(self, config: dict[str, Any] | None = None, buffer_size: int = 200_000) -> None:
        self._config = config
        self._pool: asyncpg.Pool | None = None
        self._tag_ids: dict[tuple[str, str], int] = {}
        self._queue: deque[tuple[str, str, float | None, str, float]] = deque(maxlen=buffer_size)
        self._alarm_rows: dict[tuple[str, float], int] = {}
        self._insight_rows: dict[int, int] = {}
        self._flusher: asyncio.Task | None = None
        self.written = 0
        self.dropped = 0

    @property
    def connected(self) -> bool:
        return self._pool is not None

    async def initialize(self) -> None:
        if self._pool is not None:
            return
        cfg = self._config or database_config()
        try:
            self._pool = await asyncpg.create_pool(
                host=cfg["host"],
                port=cfg["port"],
                database=cfg["name"],
                user=cfg["user"],
                password=cfg["password"],
                min_size=1,
                max_size=5,
            )
        except Exception as e:
            logger.error("Ошибка подключения к PostgreSQL: %s", e)
            raise
        await self.migrate()
        logger.info("PostgreSQL подключен")

    async def migrate(self) -> list[str]:
        """Применяет новые миграции по порядку, каждую в своей транзакции."""
        assert self._pool is not None
        applied: list[str] = []
        async with self._pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version VARCHAR(100) PRIMARY KEY,"
                " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            # Защита от одновременного запуска двух экземпляров.
            await conn.execute("SELECT pg_advisory_lock($1)", 7_310_2024)
            try:
                done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
                for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    if path.stem in done:
                        continue
                    async with conn.transaction():
                        await conn.execute(path.read_text(encoding="utf-8"))
                        await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", path.stem)
                    applied.append(path.stem)
                    logger.info("Миграция применена: %s", path.name)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", 7_310_2024)
        return applied

    async def close(self) -> None:
        if self._flusher:
            self._flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flusher
            self._flusher = None
        if self._pool:
            with contextlib.suppress(Exception):
                await self.flush()
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL отключен")

    # ---------- теги ----------
    async def _tag_id(
        self, conn: asyncpg.Connection, device_id: str, tag_name: str, meta: dict | None = None
    ) -> int:
        key = (device_id, tag_name)
        tag_id = self._tag_ids.get(key)
        if tag_id is None:
            tag_id = await conn.fetchval(
                """
                INSERT INTO tags (device_id, tag_name, unit, label) VALUES ($1, $2, $3, $4)
                ON CONFLICT (device_id, tag_name) DO UPDATE
                    SET unit = COALESCE(EXCLUDED.unit, tags.unit),
                        label = COALESCE(EXCLUDED.label, tags.label)
                RETURNING id
                """,
                device_id,
                tag_name,
                (meta or {}).get("unit"),
                json.dumps(meta["label"], ensure_ascii=False) if meta and meta.get("label") else None,
            )
            self._tag_ids[key] = tag_id
        return tag_id

    async def register_tags(self, tags: list[tuple[str, str, dict]]) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            for device_id, tag_name, meta in tags:
                self._tag_ids.pop((device_id, tag_name), None)
                await self._tag_id(conn, device_id, tag_name, meta)

    # ---------- история значений ----------
    def enqueue(self, device_id: str, tag_name: str, value: float | None, quality: str, ts: float) -> None:
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
        self._queue.append((device_id, tag_name, value, quality, ts))

    def start_background_flush(self, interval_s: float = 1.0) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await self.flush()
                except Exception as exc:  # noqa: BLE001 - БД может быть недоступна
                    logger.warning("Запись в PostgreSQL отложена: %s", exc)

        self._flusher = asyncio.create_task(loop())

    async def flush(self) -> int:
        if not self._queue or self._pool is None:
            return 0
        batch = list(self._queue)
        async with self._pool.acquire() as conn:
            rows = []
            for device_id, tag_name, value, quality, ts in batch:
                tag_id = await self._tag_id(conn, device_id, tag_name)
                rows.append((tag_id, value, quality, _dt(ts)))
            await conn.executemany(
                'INSERT INTO tag_history (tag_id, value, quality, "timestamp") VALUES ($1, $2, $3, $4)',
                rows,
            )
        for _ in range(len(batch)):
            self._queue.popleft()
        self.written += len(batch)
        return len(batch)

    async def save_tag_value(
        self, device_id: str, tag_name: str, value: float, quality: str = "GOOD"
    ) -> None:
        """Совместимость с версией 0.x: немедленная запись одного значения."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            tag_id = await self._tag_id(conn, device_id, tag_name)
            await conn.execute(
                "INSERT INTO tag_history (tag_id, value, quality) VALUES ($1, $2, $3)",
                tag_id,
                value,
                quality,
            )

    async def get_tag_history(
        self, device_id: str, tag_name: str, limit: int = 100, since_ts: float | None = None
    ) -> list[dict[str, Any]]:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT th.value, th.quality, th."timestamp"
                FROM tag_history th
                JOIN tags t ON th.tag_id = t.id
                WHERE t.device_id = $1 AND t.tag_name = $2
                  AND ($4::timestamptz IS NULL OR th."timestamp" >= $4)
                ORDER BY th."timestamp" DESC
                LIMIT $3
                """,
                device_id,
                tag_name,
                limit,
                _dt(since_ts),
            )
            return [dict(row) for row in rows]

    # ---------- алармы ----------
    async def save_alarm_event(self, event: str, alarm: dict[str, Any], ts: float) -> None:
        """raise -> новая строка; clear/ack -> обновление той же строки."""
        if self._pool is None:
            return
        key = (alarm["key"], alarm["raised_at"] or 0.0)
        async with self._pool.acquire() as conn:
            if event == "raise":
                tag_id = None
                if alarm["tag"] != "*":
                    tag_id = await self._tag_id(conn, alarm["device_id"], alarm["tag"])
                row_id = await conn.fetchval(
                    """
                    INSERT INTO alarms (tag_id, device_id, alarm_type, priority, value,
                                        limit_value, message, params, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                    """,
                    tag_id,
                    alarm["device_id"],
                    alarm["kind"],
                    alarm["priority"],
                    alarm["value"],
                    alarm["limit"],
                    alarm["message"].get("code"),
                    json.dumps(alarm["message"].get("params", {}), ensure_ascii=False),
                    _dt(ts),
                )
                self._alarm_rows[key] = row_id
                if len(self._alarm_rows) > 10_000:
                    self._alarm_rows.pop(next(iter(self._alarm_rows)))
                return
            row_id = self._alarm_rows.get(key)
            if row_id is None:
                return
            if event == "clear":
                await conn.execute("UPDATE alarms SET cleared_at = $2 WHERE id = $1", row_id, _dt(ts))
            elif event == "ack":
                await conn.execute(
                    "UPDATE alarms SET acknowledged = TRUE, acked_at = $2, acked_by = $3 WHERE id = $1",
                    row_id,
                    _dt(ts),
                    alarm.get("acked_by"),
                )

    async def save_alarm(
        self, device_id: str, tag_name: str, alarm_type: str, value: float, message: str
    ) -> None:
        """Совместимость с версией 0.x."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            tag_id = await self._tag_id(conn, device_id, tag_name)
            await conn.execute(
                "INSERT INTO alarms (tag_id, device_id, alarm_type, value, message)"
                " VALUES ($1, $2, $3, $4, $5)",
                tag_id,
                device_id,
                alarm_type,
                value,
                message,
            )

    # ---------- ML ----------
    async def save_insight(self, event: str, insight: dict[str, Any]) -> None:
        if self._pool is None or event == "update":
            return
        async with self._pool.acquire() as conn:
            if event == "open":
                row_id = await conn.fetchval(
                    """
                    INSERT INTO ml_insights
                        (device_id, tag_name, kind, severity, code, params, peak, started_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id
                    """,
                    insight["device_id"],
                    insight["tag"],
                    insight["kind"],
                    insight["severity"],
                    insight["code"],
                    json.dumps(insight["params"], ensure_ascii=False),
                    insight["peak"],
                    _dt(insight["started_at"]),
                )
                self._insight_rows[insight["id"]] = row_id
            elif event == "close":
                row_id = self._insight_rows.pop(insight["id"], None)
                if row_id is not None:
                    await conn.execute(
                        "UPDATE ml_insights SET ended_at = $2, peak = $3, params = $4 WHERE id = $1",
                        row_id,
                        _dt(insight["ended_at"]),
                        insight["peak"],
                        json.dumps(insight["params"], ensure_ascii=False),
                    )


_repository: DatabaseRepository | None = None


def get_repository() -> DatabaseRepository:
    global _repository
    if _repository is None:
        _repository = DatabaseRepository()
    return _repository
