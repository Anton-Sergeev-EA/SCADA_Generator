"""Память устройства Modbus: 4 таблицы по 1000 адресов."""

from __future__ import annotations

SIZE = 1000


class DataStore:
    def __init__(self, size: int = SIZE) -> None:
        self.size = size
        self.holding = [0] * size
        self.input = [0] * size
        self.coils = [0] * size
        self.discrete = [0] * size
