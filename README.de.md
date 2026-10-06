# SCADA Generator

[Русский](README.md) · [English](README.en.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · [Español](README.es.md) · [Français](README.fr.md) · **Deutsch** · [Italiano](README.it.md)

## Überblick

YAML-konfigurierter Prototyp für industrielle Überwachung: Datenerfassung,
generierte HMI, Alarmzustände und kontinuierliche C++17/Python-Analyse.
Die Demo verwendet eine simulierte Pumpstation und einen lokalen Modbus-TCP-Server.
Simulationstests belegen weder einen industriellen Einsatz noch Feldgenauigkeit
oder eine unabhängige Protokollzertifizierung.

## Komponenten

- Adapter für Modbus TCP/RTU, OPC UA, MQTT und IEC 60870-5-104.
- HMI-Generierung aus `configs/config.yaml`.
- Alarme mit Hysterese, Verzögerung, Quittierung und Zurückstellung.
- Robuster EWMA-Detektor und Holt-Trendextrapolation in C++17 über pybind11.
- Multivariate PCA/MSPC-Analyse und Python-Alternative mit Paritätstests.
- REST-API, WebSocket und PostgreSQL-Archiv mit Migrationen.

## Schnellstart

```bash
pip install -r requirements.txt
python run.py --demo --open
```

Öffnen Sie `http://127.0.0.1:8000`. Im Demomodus ist PostgreSQL nicht erforderlich.
Fehler können in Szenarien eingespeist werden; Ergebnisse gelten für den Simulator.

## Konfiguration und Adapter

Geräte und Signale werden in `configs/config.yaml` beschrieben.
Adressfelder hängen vom Protokoll ab: `address`, `node`, `topic` oder `ioa`.
Optionale Bibliotheken: `pip install -r requirements-protocols.txt`.
Siehe [Mehrprotokoll-Beispiel](configs/examples/multi_protocol.yaml).
`python run.py --check` prüft Verbindung und Lesen, ohne Schreibbefehle zu senden.

## Build und Tests

```bash
pip install -r requirements-dev.txt
python scripts/build_native.py
python -m pytest -q
SCADA_FORCE_PYTHON_CORE=1 python -m pytest -q
ruff check .
ruff format --check .
```

PostgreSQL-Tests benötigen eine Testdatenbank über `SCADA_TEST_PG`.
Die vollständige Referenz steht in [English](README.en.md).

## Sicherheit und Grenzen

Standardmäßig wird `127.0.0.1` verwendet. Für Steuerbefehle `SCADA_API_TOKEN` konfigurieren:
bei gesetztem Token ist `X-API-Token` erforderlich.
Schreiben ist nur für `writable: true` und innerhalb von `min..max` erlaubt.
Ein Knoten ohne Redundanz; ein Bedienertoken ohne Rollen. OPC UA wird abgefragt.
Voralarmprognosen extrapolieren Trends und erkennen keine plötzlichen Ausfälle im Voraus.
Die Kompatibilität muss für jedes Gerät geprüft werden.
Kerndurchsatz ist nicht mit dem Durchsatz der gesamten Plattform gleichzusetzen.
Die Anwendung unterstützt Russisch, Englisch und Chinesisch; acht Sprachen gelten für die Dokumentation.

## Dokumentation und Lizenz

Diese Seite ist eine Kurzfassung. Detaillierte Referenz: [English](README.en.md) und
[Русский](README.md). Änderungen: [CHANGELOG.md](CHANGELOG.md).
MIT: [LICENSE.md](LICENSE.md). Autor: Anton Sergeev.
