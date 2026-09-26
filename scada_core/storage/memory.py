"""Оперативная история тегов в памяти (кольцевые буферы).

Используется интерфейсом для трендов «здесь и сейчас» и позволяет
запускать систему вообще без PostgreSQL (режим --demo).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Sample:
    ts: float
    value: float | None
    quality: str
    score: float | None = None
    band_low: float | None = None
    band_high: float | None = None


class MemoryStore:
    def __init__(self, history_size: int = 3600) -> None:
        self.history_size = max(10, history_size)
        self._series: dict[str, deque[Sample]] = {}
        self.latest: dict[str, Sample] = {}

    def append(self, key: str, sample: Sample) -> None:
        series = self._series.get(key)
        if series is None:
            series = self._series[key] = deque(maxlen=self.history_size)
        series.append(sample)
        self.latest[key] = sample

    def history(self, key: str, seconds: float | None = None, now: float | None = None) -> dict:
        series = self._series.get(key)
        if not series:
            return {"ts": [], "value": [], "quality": [], "band_low": [], "band_high": []}
        items: list[Sample] = list(series)
        if seconds is not None:
            end = now if now is not None else items[-1].ts
            items = [s for s in items if s.ts >= end - seconds]
        return {
            "ts": [s.ts for s in items],
            "value": [s.value for s in items],
            "quality": [s.quality for s in items],
            "score": [s.score for s in items],
            "band_low": [s.band_low for s in items],
            "band_high": [s.band_high for s in items],
        }

    def keys(self) -> list[str]:
        return list(self._series)

    def stats(self) -> dict[str, Any]:
        return {"series": len(self._series), "samples": sum(len(s) for s in self._series.values())}
