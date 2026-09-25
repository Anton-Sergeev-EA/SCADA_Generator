"""Преобразование регистров Modbus <-> инженерные значения."""

from __future__ import annotations

import math
import struct

from scada_core.config.loader import TagConfig


def decode(tag: TagConfig, raw: list[int] | list[bool]) -> float:
    """Сырые регистры/биты -> инженерное значение (raw * scale + offset).

    Многорегистровые типы — big-endian, старшее слово первым (порядок
    Modbus по умолчанию).
    """
    if tag.is_bit:
        return 1.0 if raw[0] else 0.0
    words = [int(w) & 0xFFFF for w in raw[: tag.register_count]]
    if tag.type == "uint16":
        value: float = words[0]
    elif tag.type == "int16":
        value = struct.unpack(">h", struct.pack(">H", words[0]))[0]
    else:
        packed = struct.pack(">HH", words[0], words[1])
        fmt = {"uint32": ">I", "int32": ">i", "float32": ">f"}[tag.type]
        value = struct.unpack(fmt, packed)[0]
    return value * tag.scale + tag.offset


def encode(tag: TagConfig, value: float) -> list[int]:
    """Инженерное значение -> слова регистров для записи."""
    if tag.is_bit:
        return [1 if value else 0]
    if not math.isfinite(value):
        raise ValueError("значение должно быть конечным числом")
    raw = (value - tag.offset) / tag.scale if tag.scale else value
    if tag.type == "float32":
        packed = struct.pack(">f", raw)
    else:
        iv = round(raw)
        limits = {
            "uint16": (0, 0xFFFF, ">H"),
            "int16": (-0x8000, 0x7FFF, ">h"),
            "uint32": (0, 0xFFFFFFFF, ">I"),
            "int32": (-0x80000000, 0x7FFFFFFF, ">i"),
        }
        lo, hi, fmt = limits[tag.type]
        if not lo <= iv <= hi:
            raise ValueError(f"значение вне диапазона типа {tag.type}")
        packed = struct.pack(fmt, iv)
    return list(struct.unpack(f">{len(packed) // 2}H", packed))


def plan_blocks(tags: list[TagConfig], max_regs: int = 120, max_gap: int = 8) -> list[dict]:
    """Группирует теги в блочные чтения, чтобы опрашивать устройство
    минимальным числом запросов (вместо одного запроса на тег)."""
    blocks: list[dict] = []
    by_func: dict[str, list[TagConfig]] = {}
    for t in tags:
        by_func.setdefault(t.function, []).append(t)
    for func, items in by_func.items():
        limit = 1968 if func in ("coil", "discrete_input") else max_regs
        items.sort(key=lambda t: t.address)
        current: dict | None = None
        for t in items:
            end = t.address + t.register_count
            if (
                current is not None
                and t.address - current["end"] <= max_gap
                and end - current["start"] <= limit
            ):
                current["end"] = max(current["end"], end)
                current["tags"].append(t)
            else:
                current = {"function": func, "start": t.address, "end": end, "tags": [t]}
                blocks.append(current)
    return blocks
