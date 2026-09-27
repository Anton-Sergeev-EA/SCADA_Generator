"""Сборка системы: опрос -> история -> алармы -> ML -> веб-интерфейс.

ScadaRuntime владеет всеми компонентами и раздаёт события подписчикам
(WebSocket-клиентам интерфейса) через asyncio-очереди.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from scada_core import __version__
from scada_core.config.loader import AppConfig, TagConfig
from scada_core.drivers import DriverError, available_protocols
from scada_core.engine.alarms import AlarmEngine, AlarmEvent
from scada_core.engine.codec import decode
from scada_core.engine.data_poller import QUALITY_GOOD, DataPoller, TagValue
from scada_core.hmi.generator import generate_hmi
from scada_core.ml.engine import Insight, MLEngine
from scada_core.storage.memory import MemoryStore, Sample

logger = logging.getLogger(__name__)


class ScadaRuntime:
    def __init__(
        self,
        config: AppConfig,
        *,
        demo: bool = False,
        use_db: bool | None = None,
        model_dir: str | None = "models",
    ) -> None:
        self.config = config
        self.demo = demo
        self.started_at = time.time()
        self.store = MemoryStore(config.storage.history_size)
        self.alarms = AlarmEngine(config.alarms)
        self.ml = MLEngine(config, model_dir=None if demo else model_dir)
        self.poller = DataPoller(config)
        self.hmi = generate_hmi(config)
        self.simulator: Any = None
        self.repo: Any = None
        self._use_db = (not demo and config.storage.postgres_enabled) if use_db is None else use_db
        self._subscribers: set[asyncio.Queue] = set()
        # События для архива пишутся строго по очереди одной задачей: иначе
        # UPDATE «возврат аларма» мог выполниться раньше INSERT «аларм».
        self._db_events: asyncio.Queue | None = None
        self._db_worker: asyncio.Task | None = None
        self._tags = {(d.id, t.name): t for d, t in config.iter_tags()}

        self.poller.add_callback("data", self._on_data)
        self.poller.add_callback("status", self._on_status)
        self.alarms.subscribe(self._on_alarm)
        self.ml.subscribe(self._on_insight)

    # ---------- запуск / останов ----------
    async def start(self) -> None:
        if self.demo:
            from scada_core.sim import Simulator

            dev = self.config.active_devices[0]
            self.simulator = Simulator(host="127.0.0.1", port=dev.port)
            self.pretrain(600)
            await self.simulator.start()
        if self._use_db:
            from scada_core.database.repository import DatabaseRepository

            repo = DatabaseRepository()
            try:
                await repo.initialize()
                await repo.register_tags(
                    [(d.id, t.name, {"unit": t.unit, "label": t.label}) for d, t in self.config.iter_tags()]
                )
                repo.start_background_flush()
                self.attach_repository(repo)
            except Exception as exc:  # noqa: BLE001
                logger.error("PostgreSQL недоступен, работаем без архива: %s", exc)
        await self.poller.start()

    async def stop(self) -> None:
        await self.poller.stop()
        if self.simulator:
            await self.simulator.stop()
        if self._db_events is not None and self._db_worker is not None:
            try:  # дописываем то, что уже в очереди, но не ждём вечно
                await asyncio.wait_for(self._db_events.join(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Не все события успели записаться в архив")
            self._db_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._db_worker
        if self.repo:
            await self.repo.close()

    def attach_repository(self, repo: Any) -> None:
        """Подключает архив и запускает единственного «писателя» событий."""
        self.repo = repo
        self._db_events = asyncio.Queue(maxsize=10_000)
        self._db_worker = asyncio.create_task(self._db_loop())

    async def _db_loop(self) -> None:
        assert self._db_events is not None
        while True:
            make_coro = await self._db_events.get()
            try:
                await make_coro()
            except Exception as exc:  # noqa: BLE001 - БД могла стать недоступной
                logger.warning("Запись события в архив не удалась: %s", exc)
            finally:
                self._db_events.task_done()

    def _to_db(self, make_coro: Any) -> None:
        if self._db_events is None:
            return
        try:
            self._db_events.put_nowait(make_coro)
        except asyncio.QueueFull:
            logger.warning("Очередь архива переполнена — событие пропущено")

    def pretrain(self, seconds: int) -> None:
        """Демо: «прокручиваем» модель процесса вперёд, чтобы ML-модели были
        обучены к моменту, когда зритель откроет интерфейс. Прогрев идёт на
        той же модели, что и живой эмулятор, — без скачка на стыке."""
        from scada_core.sim import DataStore, ProcessModel

        dev = self.config.active_devices[0]
        if self.simulator is not None:
            store, model = self.simulator.store, self.simulator.model
        else:
            store = DataStore()
            model = ProcessModel(store, seed=7)
        t0 = time.time() - seconds
        tables = {
            "holding_register": store.holding,
            "input_register": store.input,
            "coil": store.coils,
            "discrete_input": store.discrete,
        }
        for i in range(seconds):
            model.step(1.0)
            values = {}
            for tag in dev.tags:
                table = tables[tag.function]
                values[tag.name] = decode(tag, table[tag.address : tag.address + tag.register_count])
            ts = t0 + i
            self.ml.process(dev.id, values, ts=ts)
            for tag in dev.tags:  # история для трендов — чтобы графики не были пустыми
                st = self.ml.tag_state(dev.id, tag.name)
                ready = bool(st and st.ready)
                self.store.append(
                    f"{dev.id}/{tag.name}",
                    Sample(
                        ts=ts,
                        value=values[tag.name],
                        quality=QUALITY_GOOD,
                        score=st.score if ready else None,
                        band_low=st.band_low if ready else None,
                        band_high=st.band_high if ready else None,
                    ),
                )
        # Инсайты прогрева не показываем оператору.
        self.ml.reset_insights()
        logger.info("Demo: ML pre-trained on %d s of simulated normal operation", seconds)

    # ---------- обработка данных ----------
    async def _on_data(self, values: list[TagValue]) -> None:
        if not values:
            return
        device_id = values[0].device_id
        ts = values[0].ts
        ml_input: dict[str, float | None] = {}
        for tv in values:
            tag = self._tags.get((tv.device_id, tv.tag_name))
            if tag is None:
                continue
            value = tv.value if tv.quality == QUALITY_GOOD else None
            ml_input[tv.tag_name] = value
            self.alarms.evaluate_tag(tv.device_id, tag, value, ts)
            if self.repo:
                self.repo.enqueue(tv.device_id, tv.tag_name, tv.value, tv.quality, tv.ts)

        self.ml.process(device_id, ml_input, ts)
        self._sync_predictive_alarms(device_id, ts)

        for tv in values:
            st = self.ml.tag_state(tv.device_id, tv.tag_name)
            self.store.append(
                tv.key,
                Sample(
                    ts=tv.ts,
                    value=tv.value,
                    quality=tv.quality,
                    score=st.score if st and st.ready else None,
                    band_low=st.band_low if st and st.ready else None,
                    band_high=st.band_high if st and st.ready else None,
                ),
            )
        self.broadcast({"type": "snapshot", "data": self.snapshot(device_id)})

    def _sync_predictive_alarms(self, device_id: str, ts: float) -> None:
        """Предаларм: низкоприоритетный аларм «порог будет пересечён через N мин»."""
        for (dev_id, name), tag in self._tags.items():
            if dev_id != device_id or not tag.limits():
                continue
            st = self.ml.tag_state(dev_id, name)
            active = bool(st and st.eta_s is not None)
            kind = f"PRED_{st.eta_kind}" if active and st else None
            for k in tag.limits():
                pred_kind = f"PRED_{k}"
                on = active and pred_kind == kind
                if not on and not self.alarms.exists(f"{dev_id}/{name}/{pred_kind}"):
                    continue
                self.alarms.set_condition(
                    dev_id,
                    name,
                    pred_kind,
                    on,
                    value=None,
                    limit=tag.limits()[k],
                    priority="low",
                    message={
                        "code": "alarm.predicted",
                        "params": {
                            "tag": name,
                            "kind": k,
                            "limit": tag.limits()[k],
                            "eta_s": round(st.eta_s) if on and st and st.eta_s else None,
                        },
                    },
                    on_delay_s=3.0,
                    ts=ts,
                )

    async def _on_status(self, device_id: str, online: bool, error: str | None) -> None:
        self.alarms.comm_status(device_id, online, error)
        self.broadcast({"type": "device", "data": {"id": device_id, "online": online, "error": error}})

    def _on_alarm(self, event: AlarmEvent) -> None:
        payload = event.to_dict()
        self.broadcast({"type": "alarm", "data": payload})
        if self.repo:
            repo = self.repo
            self._to_db(lambda: repo.save_alarm_event(event.event, payload["alarm"], event.ts))

    def _on_insight(self, event: str, insight: Insight) -> None:
        if event == "update":
            return  # обновления уходят в snapshot, чтобы не забивать канал
        data = insight.to_dict()
        self.broadcast({"type": "insight", "event": event, "data": data})
        if self.repo:
            repo = self.repo
            self._to_db(lambda: repo.save_insight(event, data))

    # ---------- подписчики ----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast(self, message: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            if q.full():  # медленный клиент — выбрасываем самое старое
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(message)

    # ---------- состояние для интерфейса ----------
    def tag_snapshot(self, device_id: str, tag: TagConfig) -> dict[str, Any]:
        key = f"{device_id}/{tag.name}"
        latest = self.store.latest.get(key)
        st = self.ml.tag_state(device_id, tag.name)
        alarm = self.alarms.state_for_tag(device_id, tag.name)
        return {
            "key": key,
            "value": latest.value if latest else None,
            "quality": latest.quality if latest else "UNKNOWN",
            "ts": latest.ts if latest else None,
            "ml": st.to_dict() if st else None,
            "alarm": (
                {"kind": alarm.kind, "state": alarm.state, "priority": alarm.priority} if alarm else None
            ),
        }

    def health(self) -> dict[str, Any]:
        groups: dict[str, list[float]] = {}
        for (dev_id, name), tag in self._tags.items():
            st = self.ml.tag_state(dev_id, name)
            h = st.health if st and st.ready else 100.0
            alarm = self.alarms.state_for_tag(dev_id, name)
            if alarm and alarm.active:
                cap = {"high": 35.0, "medium": 65.0, "low": 85.0}.get(alarm.priority, 85.0)
                h = min(h, cap)
            groups.setdefault(tag.group or dev_id, []).append(h)
        group_health = {g: round(min(v)) for g, v in groups.items()}
        for model in self.ml.devices.values():
            if model.last_mspc and model.last_mspc["anomaly"]:
                for g in group_health:
                    group_health[g] = min(group_health[g], 90)
        values = list(group_health.values()) or [100]
        plant = round(0.5 * min(values) + 0.5 * sum(values) / len(values))
        if any(not self.poller.device_online(d.id) for d in self.config.active_devices):
            plant = min(plant, 30) if self.poller.cycles else plant
        return {"plant": plant, "groups": group_health}

    def snapshot(self, device_id: str | None = None) -> dict[str, Any]:
        tags = {
            f"{d}/{t.name}": self.tag_snapshot(d, t)
            for (d, _), t in self._tags.items()
            if device_id in (None, d)
        }
        return {
            "ts": time.time(),
            "tags": tags,
            "health": self.health(),
            "alarms": [a.to_dict() for a in self.alarms.visible_alarms()],
            "insights": [i.to_dict() for i in self.ml.active_insights()],
            "devices": {d.id: self.poller.device_online(d.id) for d in self.config.active_devices},
            "mspc": {k: m.last_mspc for k, m in self.ml.devices.items()},
        }

    def readiness(self) -> dict[str, Any]:
        """Готовность для мониторинга/оркестратора (см. /api/health)."""
        devices = {d.id: self.poller.device_online(d.id) for d in self.config.active_devices}
        db_ok = None if not self._use_db else bool(self.repo and self.repo.connected)
        ready = self.poller.is_running and all(devices.values()) and db_ok is not False
        return {
            "status": "ok" if ready else "degraded",
            "poller": self.poller.is_running,
            "devices": devices,
            "device_errors": {d: self.poller.device_error(d) for d, online in devices.items() if not online},
            "database": db_ok,
            "db_queue": self._db_events.qsize() if self._db_events else 0,
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    def meta(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "name": self.config.name,
            "default_language": self.config.default_language,
            "demo": self.demo,
            "ml_backend": self.ml.status()["backend"],
            "database": bool(self.repo),
            "started_at": self.started_at,
            "faults": list(self._fault_catalog()) if self.demo else [],
            "devices": [
                {"id": d.id, "name": d.name, "protocol": d.protocol} for d in self.config.active_devices
            ],
            "protocols": available_protocols(),
        }

    def _fault_catalog(self) -> list[str]:
        from scada_core.sim import FAULTS

        return list(FAULTS)

    # ---------- управление ----------
    async def write(self, device_id: str, tag_name: str, value: float) -> None:
        tag = self._tags.get((device_id, tag_name))
        if tag is None:
            raise KeyError(f"{device_id}/{tag_name}")
        if not tag.writable:
            raise PermissionError("tag is read-only")
        if not tag.is_bit:
            if (tag.min is not None and value < tag.min) or (tag.max is not None and value > tag.max):
                raise ValueError(f"value out of range [{tag.min}, {tag.max}]")
        driver = self.poller.driver(device_id)
        if driver is None:
            raise RuntimeError("device is not polled")
        try:
            await driver.write(tag, value)
        except DriverError as exc:
            raise RuntimeError(str(exc)) from exc
        # Смена уставки/режима оператором — штатное событие: новая норма.
        self.ml.rebase(device_id)
        self.alarms.journal.append(
            {"event": "write", "ts": time.time(), "tag": f"{device_id}/{tag_name}", "value": value}
        )
