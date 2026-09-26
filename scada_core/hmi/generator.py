"""Генератор мнемосхемы оператора из конфигурации.

То, что отличает SCADA Generator: мнемосхему не рисуют вручную в
редакторе. Узлы (group) выстраиваются по ходу технологического потока
(project.flow), для каждого тега выбирается подходящий элемент
(резервуар, насос, задвижка, манометр…) — по полю widget или
автоматически по имени, единицам и типу сигнала. Результат —
декларативная JSON-схема, которую веб-интерфейс рисует в SVG.
"""

from __future__ import annotations

import math
import re
from itertools import pairwise
from typing import Any

from scada_core.config.loader import AppConfig, DeviceConfig, TagConfig, make_label

AREA_W = 280
AREA_GAP = 60
MAIN_H = 200
TILE_H = 54
PAD = 16
TOP = 40
MAIN_TYPES = ("tank", "pump", "valve", "gauge", "thermometer", "flow")

_RULES: list[tuple[str, str]] = [
    (r"setpoint|уставк|задани", "value"),
    (r"valve|задвиж|клапан|gate", "valve"),
    (r"pump|насос|motor_run", "pump"),
    (r"level|уров", "tank"),
    (r"temp|темп|°c|degc", "thermometer"),
    (r"press|давл|bar|kpa|mpa|psi", "gauge"),
    (r"flow|расход|подач|m³/h|m3/h|l/s|л/с", "flow"),
]


def infer_widget(tag: TagConfig) -> str:
    """Выбор элемента мнемосхемы, если widget не задан явно."""
    if tag.widget:
        return tag.widget
    text = f"{tag.name} {tag.unit} {tag.label.get('en', '')} {tag.label.get('ru', '')}".lower()
    if tag.is_bit:
        if re.search(r"valve|gate|задвиж|клапан|阀", text):
            return "valve"
        return "indicator"
    for pattern, widget in _RULES:
        if re.search(pattern, text):
            if widget == "tank" and tag.unit not in ("%", "m", "м", "mm", "мм"):
                continue
            if widget == "pump" and tag.unit not in ("rpm", "об/мин", "%", "Hz", "Гц"):
                continue
            return widget
    return "value"


def infer_decimals(tag: TagConfig) -> int:
    if tag.decimals is not None:
        return max(0, tag.decimals)
    if tag.is_bit or tag.scale == 0:
        return 0
    return max(0, min(4, -math.floor(math.log10(abs(tag.scale))))) if tag.scale < 1 else 0


def _widget(dev: DeviceConfig, tag: TagConfig, kind: str) -> dict[str, Any]:
    return {
        "key": f"{dev.id}/{tag.name}",
        "device": dev.id,
        "tag": tag.name,
        "type": kind,
        "label": tag.label or make_label(None, tag.name),
        "unit": tag.unit,
        "min": tag.min,
        "max": tag.max,
        "limits": tag.limits(),
        "writable": tag.writable,
        "bit": tag.is_bit,
        "decimals": infer_decimals(tag),
        "ml": tag.ml and not tag.is_bit,
    }


def generate_hmi(config: AppConfig) -> dict[str, Any]:
    groups: dict[str, list[tuple[DeviceConfig, TagConfig]]] = {}
    for dev, tag in config.iter_tags():
        groups.setdefault(tag.group or dev.id, []).append((dev, tag))

    order = [g for g in config.flow if g in groups]
    order += [g for g in groups if g not in order]
    device_names = {d.id: d.name for d in config.devices}

    areas: list[dict[str, Any]] = []
    max_h = 0
    for i, gid in enumerate(order):
        members = groups[gid]
        widgets = [_widget(d, t, infer_widget(t)) for d, t in members]
        main = next((w for w in widgets if w["type"] in ("tank", "pump", "valve")), None)
        if main is None:
            main = next((w for w in widgets if w["type"] in MAIN_TYPES and not w["bit"]), None)
        tiles = [w for w in widgets if w is not main]
        x = PAD + i * (AREA_W + AREA_GAP)
        y = TOP
        cursor = y + 44
        if main is not None:
            main.update(x=x + PAD, y=cursor, w=AREA_W - 2 * PAD, h=MAIN_H)
            cursor += MAIN_H + 12
        for w in tiles:
            w.update(x=x + PAD, y=cursor, w=AREA_W - 2 * PAD, h=TILE_H - 6)
            cursor += TILE_H
        h = cursor - y + PAD
        max_h = max(max_h, h)
        label = config.groups.get(gid) or device_names.get(gid) or make_label(None, gid)
        areas.append(
            {
                "id": gid,
                "label": label,
                "x": x,
                "y": y,
                "w": AREA_W,
                "h": h,
                "main": main,
                "tiles": tiles,
            }
        )

    for a in areas:  # одинаковая высота узлов — схема читается как одна линия
        a["h"] = max_h
    pipe_y = TOP + 44 + MAIN_H // 2
    links = []
    for a, b in pairwise(areas):
        links.append(
            {
                "from": a["id"],
                "to": b["id"],
                "points": [[a["x"] + a["w"], pipe_y], [b["x"], pipe_y]],
            }
        )

    all_tags = config.iter_tags()
    width = PAD * 2 + len(areas) * AREA_W + max(0, len(areas) - 1) * AREA_GAP
    return {
        "version": 1,
        "title": config.name,
        "width": width,
        "height": TOP + max_h + PAD,
        "pipe_y": pipe_y,
        "areas": areas,
        "links": links,
        "stats": {
            "devices": len(config.active_devices),
            "tags": len(all_tags),
            "areas": len(areas),
            "alarm_limits": sum(len(t.limits()) for _, t in all_tags),
            "ml_tags": sum(1 for _, t in all_tags if t.ml and not t.is_bit),
            "writable": sum(1 for _, t in all_tags if t.writable),
        },
    }
