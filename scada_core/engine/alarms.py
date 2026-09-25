"""Движок алармов по модели состояний ISA-18.2 / IEC 62682.

Раньше аларм записывался в БД на КАЖДОМ цикле опроса, пока значение
было за порогом, — 3600 записей в час на один «висящий» аларм. Теперь
аларм — это объект с состояниями; события порождаются только при
переходах, а зона возврата (deadband) и задержка (on-delay) убирают
«дребезг» на шумном сигнале.

Состояния:            условие активно       условие снято
    NORMAL        ->  ACTIVE_UNACK
    ACTIVE_UNACK  --квитирование-->  ACTIVE_ACK  --снято-->  NORMAL
    ACTIVE_UNACK  --снято-->  RTN_UNACK  --квитирование-->  NORMAL
"""

from __future__ import annotations

import itertools
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from scada_core.config.loader import AlarmConfig, TagConfig

NORMAL = "NORMAL"
ACTIVE_UNACK = "ACTIVE_UNACK"
ACTIVE_ACK = "ACTIVE_ACK"
RTN_UNACK = "RTN_UNACK"

PRIORITY = {"HH": "high", "LL": "high", "COMM": "high", "H": "medium", "L": "medium"}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Alarm:
    id: int
    key: str
    device_id: str
    tag: str
    kind: str  # HH | H | L | LL | COMM | PRED_H | PRED_L | ...
    priority: str
    state: str = NORMAL
    value: float | None = None
    limit: float | None = None
    message: dict[str, Any] = field(default_factory=dict)  # {code, params}
    raised_at: float | None = None
    cleared_at: float | None = None
    acked_at: float | None = None
    acked_by: str | None = None
    activations: int = 0
    shelved_until: float | None = None

    @property
    def active(self) -> bool:
        return self.state in (ACTIVE_UNACK, ACTIVE_ACK)

    @property
    def visible(self) -> bool:
        return self.state != NORMAL

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["active"] = self.active
        return d


@dataclass
class AlarmEvent:
    """Переход аларма: raise | clear | ack | shelve | unshelve."""

    event: str
    alarm: Alarm
    ts: float

    def to_dict(self) -> dict[str, Any]:
        return {"event": self.event, "ts": self.ts, "alarm": self.alarm.to_dict()}


Listener = Callable[[AlarmEvent], None]


class AlarmEngine:
    def __init__(self, config: AlarmConfig | None = None) -> None:
        self.cfg = config or AlarmConfig()
        self._alarms: dict[str, Alarm] = {}
        self._pending: dict[str, float] = {}  # key -> момент, когда условие стало истинным
        self._ids = itertools.count(1)
        self._listeners: list[Listener] = []
        self._activations: deque[tuple[float, str]] = deque(maxlen=20000)
        self.journal: deque[dict[str, Any]] = deque(maxlen=2000)

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _emit(self, event: str, alarm: Alarm, ts: float) -> None:
        ev = AlarmEvent(event, alarm, ts)
        self.journal.append(ev.to_dict())
        for listener in self._listeners:
            listener(ev)

    def _get(self, device_id: str, tag: str, kind: str, priority: str) -> Alarm:
        key = f"{device_id}/{tag}/{kind}"
        alarm = self._alarms.get(key)
        if alarm is None:
            alarm = Alarm(next(self._ids), key, device_id, tag, kind, priority)
            self._alarms[key] = alarm
        return alarm

    # ---------- общий механизм условий ----------
    def set_condition(
        self,
        device_id: str,
        tag: str,
        kind: str,
        active: bool,
        *,
        value: float | None = None,
        limit: float | None = None,
        message: dict[str, Any] | None = None,
        priority: str | None = None,
        on_delay_s: float = 0.0,
        ts: float | None = None,
    ) -> Alarm:
        ts = time.time() if ts is None else ts
        alarm = self._get(device_id, tag, kind, priority or PRIORITY.get(kind, "low"))
        alarm.value = value if value is not None else alarm.value
        if limit is not None:
            alarm.limit = limit
        if message is not None and (active or not alarm.message):
            alarm.message = message
        key = alarm.key

        if alarm.shelved_until is not None and ts >= alarm.shelved_until:
            alarm.shelved_until = None
            self._emit("unshelve", alarm, ts)

        if active:
            if alarm.active:
                return alarm
            started = self._pending.setdefault(key, ts)
            if ts - started < on_delay_s:
                return alarm
            self._pending.pop(key, None)
            alarm.state = ACTIVE_UNACK
            alarm.raised_at = ts
            alarm.cleared_at = None
            alarm.acked_at = None
            alarm.acked_by = None
            alarm.activations += 1
            if not kind.startswith("PRED_"):  # предупреждения ML — не алармы для KPI
                self._activations.append((ts, key))
            if alarm.shelved_until is None:
                self._emit("raise", alarm, ts)
        else:
            self._pending.pop(key, None)
            if alarm.state == ACTIVE_UNACK:
                # Предупреждения ИИ (PRED_*) — «alert» по ISA-18.2: после
                # возврата не требуют квитирования и не засоряют список.
                alarm.state = NORMAL if kind.startswith("PRED_") else RTN_UNACK
                alarm.cleared_at = ts
                self._emit("clear", alarm, ts)
            elif alarm.state == ACTIVE_ACK:
                alarm.state = NORMAL
                alarm.cleared_at = ts
                self._emit("clear", alarm, ts)
        return alarm

    # ---------- пороговые алармы тега ----------
    def evaluate_tag(
        self, device_id: str, tag: TagConfig, value: float | None, ts: float | None = None
    ) -> None:
        if value is None:
            return
        for kind, limit in tag.limits().items():
            alarm = self._alarms.get(f"{device_id}/{tag.name}/{kind}")
            was_active = alarm is not None and alarm.active
            high = kind in ("H", "HH")
            if high:
                cond = value > limit if not was_active else value > limit - tag.deadband
            else:
                cond = value < limit if not was_active else value < limit + tag.deadband
            self.set_condition(
                device_id,
                tag.name,
                kind,
                cond,
                value=value,
                limit=limit,
                message={
                    "code": "alarm.limit",
                    "params": {"tag": tag.name, "kind": kind, "limit": limit, "value": value},
                },
                on_delay_s=tag.on_delay_s,
                ts=ts,
            )

    def comm_status(self, device_id: str, online: bool, error: str | None = None) -> None:
        self.set_condition(
            device_id,
            "*",
            "COMM",
            not online,
            message={"code": "alarm.comm", "params": {"device": device_id, "error": error or ""}},
        )

    # ---------- действия оператора ----------
    def acknowledge(self, alarm_id: int, user: str = "operator", ts: float | None = None) -> bool:
        ts = time.time() if ts is None else ts
        for alarm in self._alarms.values():
            if alarm.id == alarm_id:
                if alarm.state == ACTIVE_UNACK:
                    alarm.state = ACTIVE_ACK
                elif alarm.state == RTN_UNACK:
                    alarm.state = NORMAL
                else:
                    return False
                alarm.acked_at = ts
                alarm.acked_by = user
                self._emit("ack", alarm, ts)
                return True
        return False

    def acknowledge_all(self, user: str = "operator") -> int:
        ids = [a.id for a in self._alarms.values() if a.state in (ACTIVE_UNACK, RTN_UNACK)]
        return sum(self.acknowledge(i, user) for i in ids)

    def shelve(self, alarm_id: int, seconds: float, ts: float | None = None) -> bool:
        ts = time.time() if ts is None else ts
        for alarm in self._alarms.values():
            if alarm.id == alarm_id:
                alarm.shelved_until = ts + max(1.0, seconds)
                self._emit("shelve", alarm, ts)
                return True
        return False

    # ---------- чтение ----------
    def exists(self, key: str) -> bool:
        return key in self._alarms

    def get(self, alarm_id: int) -> Alarm | None:
        return next((a for a in self._alarms.values() if a.id == alarm_id), None)

    def visible_alarms(self) -> list[Alarm]:
        items = [a for a in self._alarms.values() if a.visible]
        return sorted(items, key=lambda a: (PRIORITY_RANK.get(a.priority, 9), -(a.raised_at or 0)))

    def state_for_tag(self, device_id: str, tag: str) -> Alarm | None:
        """Самый приоритетный видимый аларм тега (для подсветки на мнемосхеме)."""
        candidates = [
            a
            for a in self._alarms.values()
            if a.device_id == device_id and a.tag == tag and a.visible and not a.shelved_until
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda a: (PRIORITY_RANK.get(a.priority, 9), not a.active))

    # ---------- KPI ISA-18.2 / EEMUA 191 ----------
    def kpi(self, now: float | None = None, window_s: float = 3600.0) -> dict[str, Any]:
        now = time.time() if now is None else now
        recent = [(t, k) for t, k in self._activations if now - t <= window_s]
        last10 = [k for t, k in recent if now - t <= 600]
        buckets = Counter(int((now - t) // 600) for t, _ in recent)
        n_buckets = max(1, int(window_s // 600))
        per10 = [buckets.get(i, 0) for i in range(n_buckets)]
        avg10 = sum(per10) / n_buckets
        flood_buckets = sum(1 for c in per10 if c > self.cfg.flood_per_10min)

        chattering: list[str] = []
        by_key: dict[str, list[float]] = {}
        for t, k in recent:
            by_key.setdefault(k, []).append(t)
        for k, times in by_key.items():
            times.sort()
            for i in range(len(times) - self.cfg.chatter_count + 1):
                if times[i + self.cfg.chatter_count - 1] - times[i] <= self.cfg.chatter_window_s:
                    chattering.append(k)
                    break

        standing = [
            a.key
            for a in self._alarms.values()
            if a.active and a.raised_at and now - a.raised_at > self.cfg.standing_after_s
        ]
        top = Counter(k for _, k in recent).most_common(5)
        if avg10 > 10:
            level = "overloaded"
        elif avg10 > 5:
            level = "reactive"
        elif avg10 > 2:
            level = "stable"
        elif avg10 > 1:
            level = "robust"
        else:
            level = "predictive"
        return {
            "window_s": window_s,
            "rate_last_10min": len(last10),
            "avg_per_10min": round(avg10, 2),
            "peak_per_10min": max(per10) if per10 else 0,
            "flood_percent": round(100.0 * flood_buckets / n_buckets, 1),
            "in_flood": len(last10) > self.cfg.flood_per_10min,
            "chattering": chattering,
            "standing": standing,
            "bad_actors": [{"key": k, "count": c} for k, c in top],
            "active": sum(1 for a in self._alarms.values() if a.active),
            "unacked": sum(1 for a in self._alarms.values() if a.state in (ACTIVE_UNACK, RTN_UNACK)),
            "shelved": sum(1 for a in self._alarms.values() if a.shelved_until),
            "performance_level": level,
        }
