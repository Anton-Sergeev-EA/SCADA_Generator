"""Физическая модель насосной станции для демонстраций и тестов.

Резервуар (1 % уровня = 0,1 м³), центробежный насос с частотным
приводом и ПИ-регулятором уровня, переменный отбор потребителя.
В модель можно «подать» неисправность — так ML-аналитику можно
показать на живом процессе, без реального оборудования.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from scada_core.sim.datastore import DataStore

PUMP_CAPACITY = 95.0  # м³/ч на номинальных оборотах
RATED_RPM = 1450.0
M3_PER_PERCENT = 0.1

# Сценарии неисправностей: id -> описание для документации/API.
FAULTS = {
    "bearing_wear": "Износ подшипника: растут вибрация, ток и температура",
    "leak": "Утечка из резервуара: баланс подача/расход нарушен",
    "sensor_drift": "Дрейф датчика температуры подшипника",
    "stuck_sensor": "Залипание датчика давления",
    "current_spikes": "Импульсные помехи в цепи измерения тока",
    "pump_trip": "Аварийный останов насоса",
}


@dataclass
class ProcessState:
    t: float = 0.0
    level: float = 60.0
    speed: float = 0.0
    integ: float = 0.0
    bearing_temp: float = 38.0
    wear: float = 0.0
    temp_offset: float = 0.0
    stuck_pressure: float | None = None
    demand: float = 40.0
    faults: dict[str, float] = field(default_factory=dict)  # id -> время запуска


class ProcessModel:
    def __init__(self, store: DataStore, seed: int | None = 42) -> None:
        self.store = store
        self.rng = random.Random(seed)
        self.s = ProcessState()
        store.coils[0] = 1  # насос в работе
        store.coils[1] = 1  # задвижка открыта
        store.holding[10] = 600  # уставка 60.0 %
        self.s.speed = RATED_RPM * 0.42
        self.s.integ = 0.0
        self._publish(inflow=40.0, outflow=40.0, pressure=1.9, current=7.6, vibration=1.5)

    # ---------- управление неисправностями ----------
    def inject(self, fault: str) -> None:
        if fault not in FAULTS:
            raise KeyError(fault)
        self.s.faults[fault] = self.s.t
        if fault == "stuck_sensor":
            self.s.stuck_pressure = self.store.holding[6] / 100.0
        if fault == "pump_trip":
            self.store.coils[0] = 0

    def clear(self, fault: str | None = None) -> None:
        targets = list(self.s.faults) if fault is None else [fault]
        for f in targets:
            self.s.faults.pop(f, None)
            if f == "bearing_wear":
                self.s.wear = 0.0
            elif f == "sensor_drift":
                self.s.temp_offset = 0.0
            elif f == "stuck_sensor":
                self.s.stuck_pressure = None
            elif f == "pump_trip":
                self.store.coils[0] = 1

    @property
    def active_faults(self) -> list[str]:
        return list(self.s.faults)

    # ---------- шаг модели ----------
    def step(self, dt: float = 1.0) -> None:
        s, st, rng = self.s, self.store, self.rng
        s.t += dt
        running = bool(st.coils[0])
        valve_open = bool(st.coils[1])
        setpoint = min(max(st.holding[10] / 10.0, 5.0), 95.0)

        if valve_open:
            demand = (
                38.0
                + 10.0 * math.sin(2 * math.pi * s.t / 180.0)
                + 4.0 * math.sin(2 * math.pi * s.t / 47.0 + 1.0)
                + rng.gauss(0.0, 0.8)
            )
        else:
            demand = 0.0
        demand = max(0.0, demand)
        leak = 8.0 if "leak" in s.faults else 0.0

        # Потребитель не может забрать больше, чем есть в резервуаре.
        if s.level <= 0.5:
            demand = min(demand, PUMP_CAPACITY * s.speed / RATED_RPM)
            leak = 0.0
        s.demand = demand

        # ПИ-регулятор уровня с упреждением по расходу.
        measured_level = self._measured_level()
        err = setpoint - measured_level
        if running:
            s.integ = min(max(s.integ + 0.002 * err * dt, -0.5), 0.5)
            cmd = demand / PUMP_CAPACITY + 0.05 * err + s.integ
            target = RATED_RPM * min(max(cmd, 0.0), 1.0)
        else:
            s.integ = 0.0
            target = 0.0
        s.speed += (target - s.speed) * min(1.0, dt / 3.0)
        frac = s.speed / RATED_RPM

        if "bearing_wear" in s.faults:
            s.wear = min(2.0, s.wear + dt / 300.0)
        if "sensor_drift" in s.faults:
            s.temp_offset += 0.05 * dt

        inflow = PUMP_CAPACITY * frac
        s.level += (inflow - demand - leak) / 3600.0 * dt / M3_PER_PERCENT
        s.level = min(max(s.level, 0.0), 100.0)

        if running and frac > 0.02:
            pressure = 1.2 + 6.0 * frac**2 - 0.01 * demand + rng.gauss(0.0, 0.02)
            current = 6.0 + 22.0 * frac**3 + 3.0 * s.wear + rng.gauss(0.0, 0.15)
            vibration = 1.1 + 0.9 * frac + 2.6 * s.wear**1.5 + rng.gauss(0.0, 0.05)
        else:
            pressure = 0.3 + rng.gauss(0.0, 0.01)
            current = 0.0
            vibration = 0.05 + abs(rng.gauss(0.0, 0.01))
        if "current_spikes" in s.faults and running and rng.random() < 0.15:
            current += 8.0 + rng.random() * 4.0
        temp_target = 24.0 + 38.0 * frac * (1.0 + 0.6 * s.wear)
        s.bearing_temp += (temp_target - s.bearing_temp) * dt / 150.0

        self._publish(
            inflow=inflow + rng.gauss(0.0, 0.3),
            outflow=demand,
            pressure=pressure,
            current=current,
            vibration=vibration,
        )

    def _measured_level(self) -> float:
        raw = self.store.holding[0]
        return (raw - 0x10000 if raw > 0x7FFF else raw) / 10.0

    def _publish(
        self, *, inflow: float, outflow: float, pressure: float, current: float, vibration: float
    ) -> None:
        s, h, rng = self.s, self.store.holding, self.rng
        level_meas = s.level + rng.gauss(0.0, 0.08)
        h[0] = _u16(round(level_meas * 10))
        h[1] = _u16(round(max(inflow, 0.0) * 10))
        h[2] = _u16(round(max(outflow, 0.0) * 10))
        h[3] = _u16(round(s.speed))
        h[4] = _u16(round(max(current, 0.0) * 10))
        h[5] = _u16(round((s.bearing_temp + s.temp_offset + rng.gauss(0.0, 0.08)) * 10))
        p = s.stuck_pressure if s.stuck_pressure is not None else max(pressure, 0.0)
        h[6] = _u16(round(p * 100))
        h[7] = _u16(round(max(vibration, 0.0) * 100))


def _u16(v: int) -> int:
    return v & 0xFFFF
