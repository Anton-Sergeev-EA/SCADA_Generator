"""C++ ядро и Python-реализация обязаны давать одинаковые результаты."""

import math
import random

import pytest

from scada_core.ml import fallback as py

native = pytest.importorskip("scada_core.ml._native", reason="C++ ядро не собрано")


def signal(seed: int = 0) -> list[float]:
    rnd = random.Random(seed)
    xs = [50 + rnd.gauss(0, 1) + 5 * math.sin(i / 20) for i in range(1500)]
    for i in range(900, 1500):
        xs[i] += 0.06 * (i - 900)  # дрейф
    xs[400] = 90.0  # выброс
    xs[600:630] = [xs[599]] * 30  # залипание
    xs[700] = float("nan")
    return xs


def test_detector_parity() -> None:
    a = native.StreamingDetector(native.DetectorConfig())
    b = py.StreamingDetector(py.DetectorConfig())
    flags_seen = 0
    for x in signal():
        ra, rb = a.update(x), b.update(x)
        assert ra.flags == rb.flags
        assert ra.ready == rb.ready
        for field in ("score", "z", "expected", "sigma", "baseline", "band_low", "band_high", "drift"):
            assert getattr(ra, field) == pytest.approx(getattr(rb, field), rel=1e-9, abs=1e-9)
        flags_seen |= ra.flags
    assert flags_seen == native.FLAG_SPIKE | native.FLAG_DRIFT | native.FLAG_STUCK


def test_forecaster_parity() -> None:
    a, b = native.HoltForecaster(), py.HoltForecaster()
    for t, x in enumerate(signal(1)):
        a.update(x, float(t))
        b.update(x, float(t))
        assert a.trend == pytest.approx(b.trend, rel=1e-9, abs=1e-12)
        assert a.time_to(120.0) == pytest.approx(b.time_to(120.0), rel=1e-9)


def test_bank_releases_gil_and_matches() -> None:
    cfg_n, cfg_p = native.DetectorConfig(), py.DetectorConfig()
    bn, bp = native.DetectorBank(3, cfg_n), py.DetectorBank(3, cfg_p)
    rnd = random.Random(3)
    for _ in range(200):
        row = [rnd.random() for _ in range(3)]
        assert [r.score for r in bn.update(row)] == pytest.approx([r.score for r in bp.update(row)])
