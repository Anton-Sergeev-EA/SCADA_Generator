#!/usr/bin/env python3
"""Сборка C++ ядра аналитики одной командой (Linux, macOS, Windows).

    pip install pybind11 cmake ninja
    python scripts/build_native.py

Результат: scada_core/ml/_native.*.so|pyd. Без него система работает на
Python-реализации тех же алгоритмов (медленнее, результаты идентичны).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "native"


def run(*cmd: str) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> int:
    if shutil.which("cmake") is None:
        print("cmake не найден: pip install cmake ninja pybind11", file=sys.stderr)
        return 1
    generator = ["-G", "Ninja"] if shutil.which("ninja") else []
    run(
        "cmake",
        "-S",
        "native",
        "-B",
        str(BUILD),
        *generator,
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DPython3_EXECUTABLE={sys.executable}",
    )
    run("cmake", "--build", str(BUILD), "--config", "Release")
    run("ctest", "--test-dir", str(BUILD), "-C", "Release", "--output-on-failure")
    subprocess.run(
        [sys.executable, "-c", "from scada_core.ml.core import BACKEND; print('ML backend:', BACKEND)"],
        check=True,
        cwd=ROOT,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
