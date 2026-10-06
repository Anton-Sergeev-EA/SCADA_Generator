# SCADA Generator

[Русский](README.md) · [English](README.en.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · **Español** · [Français](README.fr.md) · [Deutsch](README.de.md) · [Italiano](README.it.md)

## Resumen

Prototipo de supervisión industrial configurado mediante YAML: adquisición de datos,
HMI generada, ciclo de vida de alarmas y análisis continuo C++17/Python.
La demostración utiliza una estación de bombeo simulada y un servidor Modbus TCP local.
Las pruebas de simulación no acreditan una instalación industrial, precisión en campo
ni certificación independiente de protocolos.

## Componentes

- Adaptadores Modbus TCP/RTU, OPC UA, MQTT e IEC 60870-5-104.
- HMI generada a partir de `configs/config.yaml`.
- Alarmas con histéresis, retardo, reconocimiento y aplazamiento.
- Detector EWMA robusto y extrapolación de tendencia de Holt en C++17 mediante pybind11.
- Análisis multivariante PCA/MSPC y alternativa Python con pruebas de equivalencia.
- API REST, WebSocket y archivo PostgreSQL con migraciones.

## Inicio rápido

```bash
pip install -r requirements.txt
python run.py --demo --open
```

Abra `http://127.0.0.1:8000`. PostgreSQL no es necesario en modo demo.
Los escenarios permiten inyectar fallos; los resultados corresponden al simulador.

## Configuración y adaptadores

Los dispositivos y señales se describen en `configs/config.yaml`.
Los campos mínimos dependen del protocolo: `address`, `node`, `topic` o `ioa`.
Las bibliotecas opcionales se instalan con `pip install -r requirements-protocols.txt`.
Consulte [el ejemplo multiprotocolo](configs/examples/multi_protocol.yaml).
`python run.py --check` comprueba lectura y conexión sin enviar escrituras.

## Compilación y pruebas

```bash
pip install -r requirements-dev.txt
python scripts/build_native.py
python -m pytest -q
SCADA_FORCE_PYTHON_CORE=1 python -m pytest -q
ruff check .
ruff format --check .
```

Las pruebas PostgreSQL necesitan una base de pruebas configurada con `SCADA_TEST_PG`.
Los contratos y ejemplos completos están en [English](README.en.md).

## Seguridad y límites

El servicio escucha en `127.0.0.1` por defecto. Configure `SCADA_API_TOKEN` antes de
habilitar control: las órdenes requieren `X-API-Token` cuando el token está configurado.
Las escrituras se limitan a señales `writable: true` y a su intervalo `min..max`.
Un nodo sin redundancia; un token de operador sin roles. OPC UA utiliza sondeo.
Las prealarmas extrapolan tendencias y no predicen fallos repentinos.
La compatibilidad con cada equipo requiere comprobación específica.
Las mediciones del núcleo no equivalen al rendimiento de toda la plataforma.
El interfaz de la aplicación admite ruso, inglés y chino; los ocho idiomas son documentación.

## Documentación y licencia

Esta página es una guía resumida. Referencia detallada: [English](README.en.md) y
[Русский](README.md). Historial: [CHANGELOG.md](CHANGELOG.md).
MIT: [LICENSE.md](LICENSE.md). Autor: Anton Sergeev.
