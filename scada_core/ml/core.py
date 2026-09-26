"""Выбор реализации аналитического ядра: C++ (если собрано) или Python."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_force_python = os.getenv("SCADA_FORCE_PYTHON_CORE", "").lower() in {"1", "true", "yes"}

try:
    if _force_python:
        raise ImportError("forced by SCADA_FORCE_PYTHON_CORE")
    from scada_core.ml import _native as _impl  # type: ignore[attr-defined]

    BACKEND = "cpp"
except ImportError:  # pragma: no cover - зависит от сборки
    from scada_core.ml import fallback as _impl  # type: ignore[no-redef]

    BACKEND = "python"
    if not _force_python:
        logger.info(
            "C++ ядро не собрано — используется Python-реализация (см. README, раздел «Сборка C++ ядра»)"
        )

DetectorConfig = _impl.DetectorConfig
DetectorBank = _impl.DetectorBank
StreamingDetector = _impl.StreamingDetector
ForecastConfig = _impl.ForecastConfig
HoltForecaster = _impl.HoltForecaster
FLAG_SPIKE: int = _impl.FLAG_SPIKE
FLAG_DRIFT: int = _impl.FLAG_DRIFT
FLAG_STUCK: int = _impl.FLAG_STUCK


def _configure(obj: Any, overrides: dict[str, Any]) -> Any:
    for key, value in overrides.items():
        if not hasattr(obj, key):
            raise AttributeError(f"unknown option {key!r}")
        setattr(obj, key, value)
    return obj


def detector_config(**overrides: Any) -> Any:
    """DetectorConfig с переопределёнными полями (одинаково для C++ и Python)."""
    return _configure(DetectorConfig(), overrides)


def forecast_config(**overrides: Any) -> Any:
    """ForecastConfig с переопределёнными полями (одинаково для C++ и Python)."""
    return _configure(ForecastConfig(), overrides)


__all__ = [
    "BACKEND",
    "FLAG_DRIFT",
    "FLAG_SPIKE",
    "FLAG_STUCK",
    "DetectorBank",
    "DetectorConfig",
    "ForecastConfig",
    "HoltForecaster",
    "StreamingDetector",
    "detector_config",
    "forecast_config",
]
