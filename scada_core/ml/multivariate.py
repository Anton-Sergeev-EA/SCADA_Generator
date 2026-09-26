"""Многомерный статистический контроль процесса (MSPC) на основе PCA.

Пороговые алармы и потоковый детектор смотрят на каждый тег отдельно.
Но многие отказы видны только во ВЗАИМОСВЯЗИ сигналов: при утечке из
резервуара каждый датчик по отдельности в норме, а баланс
«подача — расход — уровень» нарушен. PCA-модель учит, как теги
установки обычно меняются вместе, и считает две статистики:

  * SPE (Q, квадрат ошибки реконструкции) — «нарушена связь между
    сигналами», новая картина, которой не было в обучении;
  * T² Хотеллинга — «привычная картина, но в необычно сильной форме».

Вклады тегов в SPE объясняют, КАКИЕ сигналы разошлись.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class MSPCResult:
    spe: float
    t2: float
    spe_ratio: float  # SPE / контрольный предел
    t2_ratio: float
    contributions: dict[str, float]  # доля вклада тега в SPE, сумма = 1
    anomaly: bool


class PCAMonitor:
    def __init__(
        self,
        variables: list[str],
        *,
        train_samples: int = 300,
        variance_target: float = 0.9,
        quantile: float = 0.995,
        smoothing: float = 0.3,
        persistence: int = 3,
    ) -> None:
        self.variables = list(variables)
        self.train_samples = max(train_samples, 2 * len(variables) + 10)
        self.variance_target = variance_target
        self.quantile = quantile
        self.smoothing = smoothing
        self.persistence = persistence
        self._buffer: list[np.ndarray] = []
        self.trained = False
        self.components = 0
        self.explained: list[float] = []
        self._mean = self._std = self._P = self._lam = None  # type: ignore[assignment]
        self.spe_limit = self.t2_limit = 1.0
        self._ewma = 0.0
        self._over = 0
        self._M = self._m_diag = self._rbc_mean = None
        self._contrib = None

    @property
    def progress(self) -> float:
        return 1.0 if self.trained else len(self._buffer) / self.train_samples

    def reset(self) -> None:
        self._buffer.clear()
        self.trained = False
        self._ewma = 0.0
        self._over = 0

    def add_training_sample(self, row: np.ndarray) -> bool:
        if np.all(np.isfinite(row)):
            self._buffer.append(np.asarray(row, dtype=float))
        if len(self._buffer) >= self.train_samples:
            self.fit(np.vstack(self._buffer))
            self._buffer.clear()
            return True
        return False

    def fit(self, X: np.ndarray) -> None:
        X = np.asarray(X, dtype=float)
        n, p = X.shape
        mean = X.mean(axis=0)
        std = X.std(axis=0, ddof=1)
        # Теги-константы не несут информации — чтобы не делить на ноль.
        std = np.where(std < 1e-9, 1.0, std)
        Z = (X - mean) / std
        _, s, vt = np.linalg.svd(Z, full_matrices=False)
        lam = (s**2) / (n - 1)
        total = lam.sum() if lam.sum() > 0 else 1.0
        cum = np.cumsum(lam) / total
        k = int(np.searchsorted(cum, self.variance_target) + 1)
        k = max(1, min(k, p - 1 if p > 1 else 1))
        self._mean, self._std = mean, std
        self._P = vt[:k].T
        self._lam = np.maximum(lam[:k], 1e-9)
        self.components = k
        self.explained = [float(v) for v in (lam / total)[:k]]

        scores = Z @ self._P
        resid = Z - scores @ self._P.T
        spe = np.sum(resid**2, axis=1)
        t2 = np.sum(scores**2 / self._lam, axis=1)
        # Эмпирические пределы + запас: не требуют гипотезы нормальности.
        self.spe_limit = float(max(np.quantile(spe, self.quantile) * 1.25, 1e-6))
        self.t2_limit = float(max(np.quantile(t2, self.quantile) * 1.25, 1e-6))
        # Вклады тегов — reconstruction-based contribution (Alcala & Qin)
        # по комбинированному индексу phi = SPE/lim + T2/lim (Yue & Qin):
        # одна формула объясняет и «новую картину», и «слишком сильную».
        p_dim = self._P.shape[0]
        c_res = np.eye(p_dim) - self._P @ self._P.T
        d_t2 = self._P @ np.diag(1.0 / self._lam) @ self._P.T
        self._M = c_res / self.spe_limit + d_t2 / self.t2_limit
        self._m_diag = np.maximum(np.diag(self._M), 1e-12)
        # Типичный вклад каждого тега в норме — вычитаем его, иначе теги
        # с «чистым шумом» попадали бы в объяснение любой аномалии.
        self._rbc_mean = np.mean((Z @ self._M) ** 2 / self._m_diag, axis=0)
        self.trained = True
        self._ewma = 0.0
        self._over = 0

    def score(self, row: np.ndarray) -> MSPCResult | None:
        if not self.trained or not np.all(np.isfinite(row)):
            return None
        z = (np.asarray(row, dtype=float) - self._mean) / self._std
        t = z @ self._P
        resid = z - t @ self._P.T
        spe = float(np.sum(resid**2))
        t2 = float(np.sum(t**2 / self._lam))
        spe_ratio = spe / self.spe_limit
        t2_ratio = t2 / self.t2_limit
        ratio = max(spe_ratio, t2_ratio)
        self._ewma = self.smoothing * ratio + (1 - self.smoothing) * self._ewma
        self._over = self._over + 1 if self._ewma > 1.0 else 0
        rbc = (self._M @ z) ** 2 / self._m_diag
        excess = np.maximum(rbc - self._rbc_mean, 0.0)
        rel = excess / (float(excess.sum()) or 1.0)
        # Сглаживаем вклады, чтобы объяснение не «прыгало» от отсчёта к отсчёту.
        self._contrib = rel if self._contrib is None else 0.2 * rel + 0.8 * self._contrib
        contrib = {v: float(c) for v, c in zip(self.variables, self._contrib, strict=True)}
        return MSPCResult(
            spe=spe,
            t2=t2,
            spe_ratio=spe_ratio,
            t2_ratio=t2_ratio,
            contributions=contrib,
            anomaly=self._over >= self.persistence,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "variables": self.variables,
            "trained": self.trained,
            "progress": round(self.progress, 3),
            "components": self.components,
            "explained_variance": self.explained,
            "spe_limit": self.spe_limit,
            "t2_limit": self.t2_limit,
            "train_samples": self.train_samples,
        }

    # ---- сохранение модели между перезапусками ----
    def to_arrays(self) -> dict[str, np.ndarray]:
        if not self.trained:
            raise RuntimeError("model is not trained")
        return {
            "variables": np.array(self.variables),
            "mean": self._mean,
            "std": self._std,
            "P": self._P,
            "lam": self._lam,
            "limits": np.array([self.spe_limit, self.t2_limit]),
            "explained": np.array(self.explained),
            "M": self._M,
            "rbc_mean": self._rbc_mean,
        }

    def load_arrays(self, data: Any) -> bool:
        if list(map(str, data["variables"])) != self.variables:
            return False  # конфигурация тегов изменилась — модель устарела
        self._mean, self._std = data["mean"], data["std"]
        self._P, self._lam = data["P"], data["lam"]
        self.spe_limit, self.t2_limit = (float(x) for x in data["limits"])
        self.explained = [float(x) for x in data["explained"]]
        self._M = data["M"]
        self._m_diag = np.maximum(np.diag(self._M), 1e-12)
        self._rbc_mean = data["rbc_mean"]
        self._contrib = None
        self.components = int(self._P.shape[1])
        self.trained = True
        return True
