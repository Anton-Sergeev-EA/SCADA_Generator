"""Чистая Python-реализация аналитического ядра.

Зеркально повторяет native/src/*.cpp, чтобы система работала и без
собранного C++-модуля (например, на Windows без компилятора). Совпадение
результатов проверяется тестом tests/test_native_parity.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

FLAG_SPIKE = 1
FLAG_DRIFT = 2
FLAG_STUCK = 4

_BAND_SIGMA = 3.0
_BASELINE_GUARD_SIGMA = 3.0
_VAR_SLOWDOWN = 0.2


def _clamp_abs(v: float, limit: float) -> float:
    return max(-limit, min(limit, v))


@dataclass
class DetectorConfig:
    fast_alpha: float = 0.1
    slow_alpha: float = 0.005
    warmup: int = 30
    z_threshold: float = 4.0
    mid_alpha: float = 0.05
    drift_threshold: float = 3.0
    drift_warmup: int = 200
    stuck_window: int = 15
    min_std: float = 1e-3
    huber_c: float = 3.0


@dataclass
class DetectorResult:
    score: float = 0.0
    z: float = 0.0
    expected: float = 0.0
    sigma: float = 0.0
    baseline: float = 0.0
    band_low: float = 0.0
    band_high: float = 0.0
    drift: float = 0.0
    drift_direction: int = 0
    flags: int = 0
    ready: bool = False


class StreamingDetector:
    def __init__(self, config: DetectorConfig | None = None) -> None:
        self._cfg = config or DetectorConfig()
        self.reset()

    def reset(self) -> None:
        self._n = 0
        self._fast_mean = self._fast_var = 0.0
        self._base_mean = self._base_var = 0.0
        self._mid_mean = 0.0
        self._drifting = False
        self._last_x = 0.0
        self._same = 0

    @property
    def count(self) -> int:
        return self._n

    def rebase(self) -> None:
        self._base_mean = self._mid_mean
        self._base_var = max(self._base_var, self._fast_var)
        self._drifting = False

    def _floor(self, level: float) -> float:
        return max(self._cfg.min_std, 1e-4 * abs(level))

    def update(self, x: float) -> DetectorResult:
        cfg = self._cfg
        res = DetectorResult()
        if not math.isfinite(x):
            res.expected = self._fast_mean
            res.baseline = self._base_mean
            return res

        self._n += 1
        if self._n == 1:
            self._fast_mean = self._base_mean = self._mid_mean = x
            self._fast_var = self._base_var = 0.0
            self._last_x = x
            self._same = 0
            s = self._floor(x)
            res.expected = res.baseline = x
            res.sigma = s
            res.band_low = x - _BAND_SIGMA * s
            res.band_high = x + _BAND_SIGMA * s
            return res

        ready = self._n > cfg.warmup
        sigma = max(math.sqrt(self._fast_var), self._floor(self._fast_mean))
        bsigma = max(math.sqrt(self._base_var), self._floor(self._base_mean))
        r = x - self._fast_mean
        z = r / sigma
        inv_n = 1.0 / self._n
        self._mid_mean += max(cfg.mid_alpha, inv_n) * (x - self._mid_mean)
        drift = abs(self._mid_mean - self._base_mean) / bsigma
        drift_ready = self._n > max(cfg.warmup, cfg.drift_warmup)

        eps = 1e-9 + 1e-9 * abs(x)
        self._same = self._same + 1 if abs(x - self._last_x) <= eps else 0
        self._last_x = x

        flags = 0
        direction = 0
        if ready:
            if abs(z) > cfg.z_threshold:
                flags |= FLAG_SPIKE
            if drift_ready:
                off = cfg.drift_threshold * (2.0 / 3.0)
                self._drifting = drift > off if self._drifting else drift > cfg.drift_threshold
            if self._drifting:
                flags |= FLAG_DRIFT
                direction = 1 if self._mid_mean >= self._base_mean else -1
            if self._same >= cfg.stuck_window and math.sqrt(self._base_var) > 3.0 * self._floor(
                self._base_mean
            ):
                flags |= FLAG_STUCK

        af = max(cfg.fast_alpha, inv_n)
        rc = _clamp_abs(r, cfg.huber_c * sigma) if ready else r
        self._fast_mean += af * rc
        self._fast_var = (1.0 - af) * (self._fast_var + af * rc * rc)

        learn_base = (not drift_ready) or (drift < 0.5 * cfg.drift_threshold and not flags & FLAG_STUCK)
        if learn_base:
            a_s = max(cfg.slow_alpha, inv_n)
            rb = x - self._base_mean
            if drift_ready:
                rb = _clamp_abs(rb, _BASELINE_GUARD_SIGMA * bsigma)
            av = a_s * _VAR_SLOWDOWN if drift_ready else a_s
            self._base_mean += a_s * rb
            self._base_var = (1.0 - av) * (self._base_var + av * rb * rb)

        raw = 0.0
        if ready:
            drift_part = drift / cfg.drift_threshold if drift_ready else 0.0
            if self._drifting:
                drift_part = max(drift_part, 1.0)
            raw = max(
                abs(z) / cfg.z_threshold,
                drift_part,
                1.0 if flags & FLAG_STUCK else 0.0,
            )

        res.score = raw / (1.0 + raw)
        res.z = z
        res.expected = x - r
        res.sigma = sigma
        res.baseline = self._base_mean
        res.band_low = res.expected - _BAND_SIGMA * sigma
        res.band_high = res.expected + _BAND_SIGMA * sigma
        res.drift = drift
        res.drift_direction = direction
        res.flags = flags
        res.ready = ready
        return res


class DetectorBank:
    def __init__(self, size: int, config: DetectorConfig) -> None:
        self._detectors = [StreamingDetector(config) for _ in range(size)]

    def update(self, values: list[float]) -> list[DetectorResult]:
        return [d.update(v) for d, v in zip(self._detectors, values, strict=False)]

    def rebase_all(self) -> None:
        for d in self._detectors:
            d.rebase()

    def rebase(self, index: int) -> None:
        if 0 <= index < len(self._detectors):
            self._detectors[index].rebase()

    def __len__(self) -> int:
        return len(self._detectors)


@dataclass
class ForecastConfig:
    alpha: float = 0.2
    beta: float = 0.01
    noise_alpha: float = 0.05
    long_alpha: float = 0.002
    min_samples: int = 120
    horizon_s: float = 1800.0
    significance: float = 3.0


class HoltForecaster:
    def __init__(self, config: ForecastConfig | None = None) -> None:
        self._cfg = config or ForecastConfig()
        self.reset()

    def reset(self) -> None:
        self._n = 0
        self._level = self._trend = 0.0
        self._resid_var = self._trend_ms = 0.0
        self._last_t = 0.0

    @property
    def level(self) -> float:
        return self._level

    @property
    def trend(self) -> float:
        return self._trend

    @property
    def noise(self) -> float:
        return math.sqrt(self._resid_var)

    @property
    def trend_scale(self) -> float:
        return max(math.sqrt(self._trend_ms), self.noise / self._cfg.horizon_s)

    @property
    def trend_significant(self) -> bool:
        cfg = self._cfg
        return self._n >= cfg.min_samples and abs(self._trend) > cfg.significance * self.trend_scale

    @property
    def count(self) -> int:
        return self._n

    def update(self, x: float, t_seconds: float) -> None:
        if not (math.isfinite(x) and math.isfinite(t_seconds)):
            return
        cfg = self._cfg
        if self._n == 0:
            self._level = x
            self._trend = 0.0
            self._resid_var = 0.0
            self._last_t = t_seconds
            self._n = 1
            return
        dt = t_seconds - self._last_t
        if dt <= 0.0:
            return
        self._n += 1
        pred = self._level + self._trend * dt
        err = x - pred
        new_level = cfg.alpha * x + (1.0 - cfg.alpha) * pred
        slope = (new_level - self._level) / dt
        inv_n = 1.0 / self._n
        g = max(cfg.noise_alpha, inv_n)
        self._trend = cfg.beta * slope + (1.0 - cfg.beta) * self._trend
        self._level = new_level
        self._resid_var = (1.0 - g) * (self._resid_var + g * err * err)
        if not self.trend_significant:
            gl = max(cfg.long_alpha, inv_n)
            self._trend_ms = (1.0 - gl) * self._trend_ms + gl * self._trend * self._trend
        self._last_t = t_seconds

    def predict(self, dt_seconds: float) -> float:
        return self._level + self._trend * dt_seconds

    def time_to(self, threshold: float) -> float:
        cfg = self._cfg
        if not self.trend_significant or not math.isfinite(threshold):
            return -1.0
        eta = (threshold - self._level) / self._trend
        if eta <= 0.0 or eta > cfg.horizon_s:
            return -1.0
        return eta
