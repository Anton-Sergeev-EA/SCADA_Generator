"""ML-движок: объединяет потоковые детекторы, прогноз и PCA-модель
в понятные оператору «инсайты» с объяснением на трёх языках.

Инсайт — это не аларм: он не требует действий по регламенту, а
подсказывает, ГДЕ и ПОЧЕМУ процесс ведёт себя необычно, до того как
сработают пороговые алармы.
"""

from __future__ import annotations

import itertools
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from scada_core.config.loader import AppConfig, DeviceConfig, TagConfig
from scada_core.ml import core
from scada_core.ml.multivariate import PCAMonitor

logger = logging.getLogger(__name__)

# Сколько отсчётов подряд условие должно держаться, чтобы открыть инсайт,
# и сколько отсчётов «тишины» нужно, чтобы его закрыть.
# Предаларм должен подтверждаться 15 с: ступенька режима (регулятор отработал
# возмущение) кратковременно похожа на тренд, но быстро выходит на полку.
OPEN_AFTER = {"spike": 1, "event": 1, "drift": 1, "stuck": 1, "correlation": 1, "forecast": 15}
CLOSE_AFTER = {"spike": 15, "event": 20, "drift": 10, "stuck": 5, "correlation": 10, "forecast": 5}
# Если столько тегов «прыгнули» одновременно — это смена режима процесса
# (пуск/останов), а не пять отдельных выбросов: показываем одно событие.
EVENT_MIN_TAGS = 3
GROUP_WINDOW_S = 60.0
# Прогноз не экстраполируем дальше, чем в EXTRAPOLATION_RATIO раз от
# длительности самой тенденции: 30 с роста не дают права предсказывать
# на 10 минут вперёд, 3 минуты устойчивого роста — дают.
EXTRAPOLATION_RATIO = 4.0
MIN_EXTRAPOLATION_S = 60.0
SEVERITY = {
    "spike": "info",
    "event": "info",
    "drift": "warning",
    "stuck": "warning",
    "correlation": "warning",
    "forecast": "warning",
}


@dataclass
class Insight:
    id: int
    key: str
    device_id: str
    tag: str | None
    kind: str
    severity: str
    code: str
    params: dict[str, Any]
    started_at: float
    updated_at: float
    ended_at: float | None = None
    peak: float = 0.0

    @property
    def active(self) -> bool:
        return self.ended_at is None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["active"] = self.active
        return d


@dataclass
class TagState:
    score: float = 0.0
    health: float = 100.0
    flags: int = 0
    ready: bool = False
    expected: float | None = None
    band_low: float | None = None
    band_high: float | None = None
    baseline: float | None = None
    trend_per_min: float | None = None
    eta_s: float | None = None
    eta_kind: str | None = None
    eta_limit: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class _Track:
    hits: int = 0
    quiet: int = 0
    insight: Insight | None = None


@dataclass
class _DeviceModel:
    device: DeviceConfig
    tags: list[TagConfig]
    bank: Any
    forecasters: dict[str, Any]
    pca: PCAMonitor
    last_mspc: dict[str, Any] | None = None
    states: dict[str, TagState] = field(default_factory=dict)
    group: set[str] = field(default_factory=set)  # теги текущей «смены режима»
    trend_since: dict[str, tuple[float, int]] = field(default_factory=dict)
    drift_started: dict[str, float] = field(default_factory=dict)


InsightListener = Callable[[str, Insight], None]


def _r(x: float, nd: int = 2) -> float:
    return round(float(x), nd)


class MLEngine:
    def __init__(self, config: AppConfig, model_dir: str | Path | None = "models") -> None:
        self.config = config
        self.cfg = config.ml
        self.model_dir = Path(model_dir) if model_dir else None
        self._ids = itertools.count(1)
        self._tracks: dict[str, _Track] = {}
        self._listeners: list[InsightListener] = []
        self.insights: deque[Insight] = deque(maxlen=500)
        self.devices: dict[str, _DeviceModel] = {}
        self.samples = 0
        det_cfg = core.detector_config(warmup=self.cfg.warmup, z_threshold=self.cfg.z_threshold)
        for dev in config.active_devices:
            tags = [t for t in dev.tags if t.ml and not t.is_bit]
            if not tags:
                continue
            fc = {
                t.name: core.HoltForecaster(
                    core.forecast_config(horizon_s=max(self.cfg.forecast_horizon_s, 60.0))
                )
                for t in tags
            }
            pca = PCAMonitor([t.name for t in tags], train_samples=self.cfg.train_samples)
            model = _DeviceModel(dev, tags, core.DetectorBank(len(tags), det_cfg), fc, pca)
            model.states = {t.name: TagState() for t in tags}
            self.devices[dev.id] = model
            self._load_model(model)

    # ---------- подписка ----------
    def subscribe(self, listener: InsightListener) -> None:
        self._listeners.append(listener)

    def _notify(self, event: str, insight: Insight) -> None:
        for listener in self._listeners:
            listener(event, insight)

    # ---------- основной цикл ----------
    def process(self, device_id: str, values: dict[str, float | None], ts: float | None = None) -> None:
        """values: tag_name -> инженерное значение (None = нет данных)."""
        if not self.cfg.enabled:
            return
        model = self.devices.get(device_id)
        if model is None:
            return
        ts = time.time() if ts is None else ts
        self.samples += 1
        row = [values.get(t.name) for t in model.tags]
        clean = [float(v) if v is not None else math.nan for v in row]
        results = model.bank.update(clean)
        members = self._update_group(model, results, clean, ts)
        for tag, value, res in zip(model.tags, clean, results, strict=True):
            st = model.states[tag.name]
            if math.isnan(value):
                st.ready = False
                continue
            fc = model.forecasters[tag.name]
            # Выброс не должен «разворачивать» тренд: прогнозу отдаём
            # ожидаемое значение вместо импульсной помехи.
            spike = bool(int(res.flags) & core.FLAG_SPIKE) and res.ready
            fc.update(float(res.expected) if spike else value, ts)
            st.score = float(res.score)
            st.flags = int(res.flags)
            st.ready = bool(res.ready)
            st.expected = float(res.expected)
            st.band_low = float(res.band_low)
            st.band_high = float(res.band_high)
            st.baseline = float(res.baseline)
            st.trend_per_min = float(fc.trend) * 60.0
            target = 100.0 * min(1.0, max(0.0, 1.0 - (st.score - 0.25) / 0.5))
            st.health += 0.2 * (target - st.health) if st.ready else 0.0
            self._forecast(model, tag, st, fc, value, ts)
            self._per_tag_insights(model, tag, st, res, value, ts, suppress=tag.name in members)

        self._multivariate(model, clean, ts)

    def _update_group(
        self, model: _DeviceModel, results: list[Any], values: list[float], ts: float
    ) -> set[str]:
        """Группировка по первопричине: если много сигналов отклонились
        одновременно (скачок) или почти одновременно (дрейф в течение
        GROUP_WINDOW_S), это одна смена режима — пуск/останов, переключение,
        — а не N независимых проблем. Оператор видит одно событие и одной
        кнопкой может принять новый режим как норму."""
        dev = model.device.id
        spiking, drifting = set(), set()
        for tag, res, value in zip(model.tags, results, values, strict=True):
            if math.isnan(value):
                continue
            flags = int(res.flags)
            if flags & core.FLAG_SPIKE:
                spiking.add(tag.name)
            if flags & core.FLAG_DRIFT:
                drifting.add(tag.name)
                if tag.name not in model.drift_started:
                    model.drift_started[tag.name] = ts
            else:
                model.drift_started.pop(tag.name, None)
        recent = {n for n, t0 in model.drift_started.items() if ts - t0 <= GROUP_WINDOW_S}
        trigger = len(spiking) >= EVENT_MIN_TAGS or len(recent) >= EVENT_MIN_TAGS
        if trigger or model.group:
            model.group |= spiking | (drifting if model.group else recent)
        active = trigger or bool(model.group & (spiking | drifting))
        if active and model.group:
            self._absorb(dev, model.group, ts)
        members = sorted(model.group)
        self._track(
            f"{dev}/*/event",
            active,
            dev,
            None,
            "event",
            "ml.process_event",
            lambda: {"tags": members, "count": len(members)},
            1.0,
            ts,
        )
        track = self._tracks.get(f"{dev}/*/event")
        if track is None or track.insight is None:
            if not active:
                model.group.clear()
        return set(model.group)

    def _absorb(self, device_id: str, members: set[str], ts: float) -> None:
        """Закрывает отдельные инсайты тегов, вошедших в групповое событие."""
        for name in members:
            for kind in ("spike", "drift"):
                tr = self._tracks.get(f"{device_id}/{name}/{kind}")
                if tr and tr.insight is not None:
                    tr.insight.ended_at = ts
                    self._notify("close", tr.insight)
                    tr.insight = None
                    tr.hits = tr.quiet = 0

    def _forecast(
        self, model: _DeviceModel, tag: TagConfig, st: TagState, fc: Any, value: float, ts: float
    ) -> None:
        best: tuple[float, str, float] | None = None
        sign = (1 if fc.trend > 0 else -1) if fc.trend_significant else 0
        since = model.trend_since.get(tag.name)
        if sign == 0:
            model.trend_since.pop(tag.name, None)
            since = None
        elif since is None or since[1] != sign:
            since = model.trend_since[tag.name] = (ts, sign)
        reach = max(MIN_EXTRAPOLATION_S, EXTRAPOLATION_RATIO * (ts - since[0])) if since else 0.0
        for kind, limit in tag.limits().items():
            high = kind in ("H", "HH")
            if (high and value >= limit) or (not high and value <= limit):
                continue  # порог уже пересечён — это работа аларма, не прогноза
            eta = fc.time_to(limit)
            if eta > reach:
                continue
            if 0 < eta <= self.cfg.forecast_horizon_s and (best is None or eta < best[0]):
                best = (eta, kind, limit)
        if best:
            st.eta_s, st.eta_kind, st.eta_limit = best
        else:
            st.eta_s = st.eta_kind = st.eta_limit = None
        self._track(
            f"{model.device.id}/{tag.name}/forecast",
            best is not None,
            model.device.id,
            tag.name,
            "forecast",
            "ml.forecast",
            lambda: {
                "tag": tag.name,
                "kind": best[1],
                "limit": best[2],
                "eta_s": round(best[0]),
                "eta_at": ts + best[0],
                "value": _r(value),
                "trend_per_min": _r(fc.trend * 60.0, 3),
                "unit": tag.unit,
            },
            1.0 - best[0] / self.cfg.forecast_horizon_s if best else 0.0,
            ts,
        )

    def _per_tag_insights(
        self,
        model: _DeviceModel,
        tag: TagConfig,
        st: TagState,
        res: Any,
        value: float,
        ts: float,
        *,
        suppress: bool = False,
    ) -> None:
        dev = model.device.id
        flags = int(res.flags)
        self._track(
            f"{dev}/{tag.name}/spike",
            bool(flags & core.FLAG_SPIKE) and not suppress,
            dev,
            tag.name,
            "spike",
            "ml.spike",
            lambda: {
                "tag": tag.name,
                "value": _r(value),
                "expected": _r(res.expected),
                "z": _r(abs(res.z), 1),
                "unit": tag.unit,
            },
            st.score,
            ts,
        )
        self._track(
            f"{dev}/{tag.name}/drift",
            bool(flags & core.FLAG_DRIFT) and not suppress,
            dev,
            tag.name,
            "drift",
            "ml.drift_up" if res.drift_direction >= 0 else "ml.drift_down",
            lambda: {
                "tag": tag.name,
                "value": _r(value),
                "baseline": _r(res.baseline),
                "sigma": _r(res.drift, 1),
                "unit": tag.unit,
            },
            st.score,
            ts,
        )
        # Значение на границе диапазона (0, min, max) — это остановленное
        # оборудование или насыщение, а не «залипший» датчик.
        at_rail = value == 0.0 or any(
            lim is not None and abs(value - lim) <= 1e-9 for lim in (tag.min, tag.max)
        )
        track = self._tracks.get(f"{dev}/{tag.name}/stuck")
        started = track.insight.started_at if track and track.insight else ts
        self._track(
            f"{dev}/{tag.name}/stuck",
            bool(flags & core.FLAG_STUCK) and not at_rail,
            dev,
            tag.name,
            "stuck",
            "ml.stuck",
            lambda: {
                "tag": tag.name,
                "value": _r(value),
                "seconds": round(ts - started + 15),
                "unit": tag.unit,
            },
            st.score,
            ts,
        )

    def _multivariate(self, model: _DeviceModel, row: list[float], ts: float) -> None:
        arr = np.array(row, dtype=float)
        pca = model.pca
        if not pca.trained:
            if pca.add_training_sample(arr):
                logger.info("MSPC model for %s trained: %d components", model.device.id, pca.components)
                self._save_model(model)
            return
        res = pca.score(arr)
        if res is None:
            return
        top = sorted(res.contributions.items(), key=lambda kv: -kv[1])[:3]
        model.last_mspc = {
            "spe_ratio": _r(res.spe_ratio, 3),
            "t2_ratio": _r(res.t2_ratio, 3),
            "anomaly": res.anomaly,
            "contributions": {k: _r(v, 3) for k, v in res.contributions.items()},
            "ts": ts,
        }
        self._track(
            f"{model.device.id}/*/correlation",
            res.anomaly,
            model.device.id,
            None,
            "correlation",
            "ml.correlation",
            lambda: {
                "top": [{"tag": k, "share": _r(v, 3)} for k, v in top if v > 0.05],
                "spe_ratio": _r(res.spe_ratio, 2),
                "t2_ratio": _r(res.t2_ratio, 2),
                "mode": "spe" if res.spe_ratio >= res.t2_ratio else "t2",
            },
            min(1.0, max(res.spe_ratio, res.t2_ratio) / 10.0),
            ts,
        )

    # ---------- жизненный цикл инсайтов ----------
    def _track(
        self,
        key: str,
        active: bool,
        device_id: str,
        tag: str | None,
        kind: str,
        code: str,
        params: Callable[[], dict[str, Any]],
        score: float,
        ts: float,
    ) -> None:
        tr = self._tracks.get(key)
        if tr is None:
            if not active:
                return
            tr = self._tracks[key] = _Track()
        if active:
            tr.hits += 1
            tr.quiet = 0
            if tr.insight is None and tr.hits >= OPEN_AFTER[kind]:
                tr.insight = Insight(
                    id=next(self._ids),
                    key=key,
                    device_id=device_id,
                    tag=tag,
                    kind=kind,
                    severity=SEVERITY[kind],
                    code=code,
                    params=params(),
                    started_at=ts,
                    updated_at=ts,
                    peak=score,
                )
                self.insights.append(tr.insight)
                self._notify("open", tr.insight)
            elif tr.insight is not None:
                tr.insight.params = params()
                tr.insight.code = code
                tr.insight.updated_at = ts
                tr.insight.peak = max(tr.insight.peak, score)
                self._notify("update", tr.insight)
        else:
            tr.hits = 0
            if tr.insight is None:
                return
            tr.quiet += 1
            if tr.quiet >= CLOSE_AFTER[kind]:
                tr.insight.ended_at = ts
                self._notify("close", tr.insight)
                tr.insight = None

    # ---------- управление ----------
    def rebase(self, device_id: str, tag: str | None = None) -> bool:
        """Принять текущее состояние как новую норму (после штатной смены режима)."""
        model = self.devices.get(device_id)
        if model is None:
            return False
        if tag is None:
            model.bank.rebase_all()
            model.drift_started.clear()
        else:
            idx = next((i for i, t in enumerate(model.tags) if t.name == tag), None)
            if idx is None:
                return False
            model.bank.rebase(idx)
        return True

    def retrain(self, device_id: str | None = None) -> list[str]:
        done = []
        for dev_id, model in self.devices.items():
            if device_id in (None, dev_id):
                model.pca.reset()
                done.append(dev_id)
        return done

    def reset_insights(self) -> None:
        self.insights.clear()
        self._tracks.clear()
        for model in self.devices.values():
            model.group.clear()
            model.drift_started.clear()

    def active_insights(self) -> list[Insight]:
        return [i for i in self.insights if i.active]

    def tag_state(self, device_id: str, tag: str) -> TagState | None:
        model = self.devices.get(device_id)
        return model.states.get(tag) if model else None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.enabled,
            "backend": core.BACKEND,
            "samples": self.samples,
            "devices": {
                dev_id: {
                    "pca": m.pca.describe(),
                    "mspc": m.last_mspc,
                    "tags": [t.name for t in m.tags],
                }
                for dev_id, m in self.devices.items()
            },
            "active_insights": len(self.active_insights()),
        }

    # ---------- сохранение моделей ----------
    def _model_path(self, model: _DeviceModel) -> Path | None:
        return self.model_dir / f"mspc_{model.device.id}.npz" if self.model_dir else None

    def _save_model(self, model: _DeviceModel) -> None:
        path = self._model_path(model)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(path, **model.pca.to_arrays())
        except OSError as exc:
            logger.warning("Cannot save model %s: %s", path, exc)

    def _load_model(self, model: _DeviceModel) -> None:
        path = self._model_path(model)
        if path is None or not path.exists():
            return
        try:
            with np.load(path, allow_pickle=False) as data:
                if model.pca.load_arrays(data):
                    logger.info("MSPC model for %s loaded from %s", model.device.id, path)
                else:
                    logger.info("Model %s is outdated (tag list changed) — retraining", path)
        except (OSError, KeyError, ValueError) as exc:
            logger.warning("Cannot load model %s: %s", path, exc)
