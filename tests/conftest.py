from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # конфиг и web/ ищутся относительно корня проекта

from scada_core.config.loader import AppConfig, load_app_config  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def port() -> int:
    return free_port()


@pytest.fixture
def config(port: int) -> AppConfig:
    """Штатная конфигурация, но эмулятор — на свободном порту."""
    cfg = load_app_config(ROOT / "configs" / "config.yaml")
    for dev in cfg.devices:
        dev.port = port
        dev.host = "127.0.0.1"
        dev.retries = 1
        dev.timeout = 1.0
        dev.poll_interval_ms = 200
    return cfg
