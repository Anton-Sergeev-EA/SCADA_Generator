# SCADA Generator — образ с собранным C++ ядром.
#
#   docker build -t scada-generator .
#   docker run --rm -p 127.0.0.1:8000:8000 scada-generator          # демо
#   docker compose up --build                                       # ПЛК-эмулятор + PostgreSQL + SCADA

# ---------- сборка C++ ядра ----------
FROM python:3.12-slim AS native
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pybind11 cmake ninja
WORKDIR /src
COPY native native
COPY scada_core/ml scada_core/ml
RUN cmake -S native -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build \
    && ctest --test-dir build --output-on-failure

# ---------- итоговый образ ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app scada
COPY requirements.txt requirements-protocols.txt ./
# PROTOCOLS=all — все драйверы (OPC UA, MQTT, RTU, МЭК 104); PROTOCOLS=base — только Modbus TCP.
ARG PROTOCOLS=all
RUN pip install -r requirements.txt \
    && if [ "$PROTOCOLS" = "all" ]; then pip install -r requirements-protocols.txt; fi
COPY run.py modbus_emulator_new.py ./
COPY configs configs
COPY web web
COPY scada_core scada_core
COPY --from=native /src/scada_core/ml/_native*.so scada_core/ml/
RUN python -c "from scada_core.ml.core import BACKEND; assert BACKEND == 'cpp', BACKEND" \
    && mkdir -p models && chown scada:scada models
USER scada
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=2)" || exit 1
ENTRYPOINT ["python", "run.py", "--host", "0.0.0.0"]
CMD ["--demo"]
