"""ML-слой на физической модели насосной станции: в норме молчит,
каждую неисправность замечает — и раньше, чем пороговые алармы."""

import math
import random
import time

import numpy as np
import pytest

from scada_core.config.loader import AppConfig
from scada_core.engine.codec import decode
from scada_core.ml import core
from scada_core.ml.engine import MLEngine
from scada_core.ml.multivariate import PCAMonitor
from scada_core.sim import DataStore, ProcessModel


def run_plant(config: AppConfig, fault: str | None, before: int = 700, after: int = 400, seed: int = 11):
    dev = config.devices[0]
    engine = MLEngine(config, model_dir=None)
    store = DataStore()
    model = ProcessModel(store, seed=seed)
    tables = {"holding_register": store.holding, "coil": store.coils}
    opened: dict[tuple[str, str | None], float] = {}
    engine.subscribe(
        lambda ev, ins: opened.setdefault((ins.kind, ins.tag), ins.started_at) if ev == "open" else None
    )
    t0 = time.time()
    for i in range(before + after):
        if i == before:
            if fault:
                model.inject(fault)
            baseline_insights = dict(opened)
            opened.clear()
        model.step(1.0)
        values = {
            t.name: decode(t, tables[t.function][t.address : t.address + t.register_count]) for t in dev.tags
        }
        engine.process(dev.id, values, ts=t0 + i)
    after_fault = {k: v - (t0 + before) for k, v in opened.items()}
    return baseline_insights, after_fault, engine


def test_normal_operation_is_quiet(config: AppConfig) -> None:
    baseline, after, engine = run_plant(config, None, after=600)
    # во время обучения первые минуты допускаем единичные выбросы
    assert not [k for k in after if k[0] != "spike"], after
    assert len(after) <= 2  # единичные выбросы шума допустимы, ложных предалармов нет
    assert engine.devices["plc_main"].pca.trained


@pytest.mark.parametrize(
    ("fault", "expected", "within_s"),
    [
        ("bearing_wear", ("forecast", "vibration"), 200),
        ("sensor_drift", ("drift", "bearing_temp"), 120),
        ("stuck_sensor", ("stuck", "discharge_pressure"), 40),
        ("leak", ("correlation", None), 60),
        ("pump_trip", ("forecast", "tank_level"), 150),
    ],
)
def test_faults_are_detected(config: AppConfig, fault: str, expected: tuple, within_s: float) -> None:
    _, after, _ = run_plant(config, fault)
    assert expected in after, f"{fault}: {after}"
    assert after[expected] <= within_s


def test_forecast_warns_before_real_alarm(config: AppConfig) -> None:
    """Износ подшипника: предаларм должен прийти за минуты до аларма H."""
    _, after, engine = run_plant(config, "bearing_wear", after=400)
    tag = config.find_tag("plc_main", "vibration")
    assert tag and tag.alarm_high
    # в модели аларм H по вибрации наступает примерно через 340 с после начала износа
    assert after[("forecast", "vibration")] < 340 - 120


@pytest.mark.parametrize("fault", ["leak", "current_spikes", "stuck_sensor"])
def test_no_false_predicted_alarms(config: AppConfig, fault: str) -> None:
    """Ступенька режима и импульсные помехи — не повод для предаларма."""
    _, after, _ = run_plant(config, fault, after=600)
    assert not [k for k in after if k[0] == "forecast"], after


def test_pump_trip_is_one_event_not_a_flood(config: AppConfig) -> None:
    _, after, engine = run_plant(config, "pump_trip")
    active = engine.active_insights()
    kinds = sorted(i.kind for i in active)
    assert kinds.count("event") == 1
    assert kinds.count("drift") <= 1  # всё остальное поглощено событием «смена режима»


def test_pca_attribution_points_at_faulty_tag() -> None:
    rnd = np.random.default_rng(0)
    base = rnd.normal(size=(400, 1))
    X = np.hstack([base, 2 * base, -base, rnd.normal(size=(400, 1))]) + rnd.normal(scale=0.05, size=(400, 4))
    mon = PCAMonitor(["a", "b", "c", "noise"], train_samples=400)
    mon.fit(X)
    row = X[0].copy()
    row[1] += 3.0  # нарушаем связь b = 2a
    res = None
    for _ in range(5):
        res = mon.score(row)
    assert res is not None and res.anomaly
    assert max(res.contributions, key=res.contributions.get) == "b"


def test_pca_save_load(tmp_path) -> None:
    rnd = np.random.default_rng(1)
    X = rnd.normal(size=(200, 3))
    a = PCAMonitor(["x", "y", "z"], train_samples=200)
    a.fit(X)
    np.savez(tmp_path / "m.npz", **a.to_arrays())
    b = PCAMonitor(["x", "y", "z"])
    with np.load(tmp_path / "m.npz") as data:
        assert b.load_arrays(data)
    assert b.score(X[5]).spe == pytest.approx(a.score(X[5]).spe)
    c = PCAMonitor(["x", "y", "other"])
    with np.load(tmp_path / "m.npz") as data:
        assert not c.load_arrays(data)  # сменился список тегов — модель устарела


def test_rebase_accepts_new_mode() -> None:
    det = core.StreamingDetector(core.detector_config())
    rnd = random.Random(2)
    for _ in range(400):
        det.update(10 + rnd.gauss(0, 0.2))
    flagged = [det.update(20 + rnd.gauss(0, 0.2)).flags & core.FLAG_DRIFT for _ in range(80)]
    assert any(flagged)
    det.rebase()
    later = [det.update(20 + rnd.gauss(0, 0.2)).score for _ in range(50)]
    assert max(later[10:]) < 0.5


def test_nan_does_not_poison_models(config: AppConfig) -> None:
    engine = MLEngine(config, model_dir=None)
    for i in range(50):
        engine.process("plc_main", {"tank_level": math.nan if i % 7 == 0 else 50.0}, ts=float(i))
    st = engine.tag_state("plc_main", "tank_level")
    assert st is not None and math.isfinite(st.expected)
