# SCADA Generator

[Русский](README.md) · [English](README.en.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · **Italiano**

## Panoramica

Prototipo di monitoraggio industriale configurato tramite YAML: acquisizione dati,
HMI generata, ciclo di vita degli allarmi e analisi continua C++17/Python.
La demo usa una stazione di pompaggio simulata e un server Modbus TCP locale.
I test di simulazione non dimostrano installazioni industriali, accuratezza sul campo
né certificazione indipendente dei protocolli.

## Componenti

- Adattatori Modbus TCP/RTU, OPC UA, MQTT e IEC 60870-5-104.
- HMI generata da `configs/config.yaml`.
- Allarmi con isteresi, ritardo, riconoscimento e sospensione.
- Rilevatore EWMA robusto ed estrapolazione di Holt in C++17 tramite pybind11.
- Analisi multivariata PCA/MSPC e alternativa Python con test di equivalenza.
- API REST, WebSocket e archivio PostgreSQL con migrazioni.

## Avvio rapido

```bash
pip install -r requirements.txt
python run.py --demo --open
```

Aprire `http://127.0.0.1:8000`. PostgreSQL non è necessario in modalità demo.
Gli scenari permettono di iniettare guasti; i risultati riguardano il simulatore.

## Configurazione e adattatori

Dispositivi e segnali sono descritti in `configs/config.yaml`.
I campi di indirizzamento dipendono dal protocollo: `address`, `node`, `topic` o `ioa`.
Installare le librerie opzionali con `pip install -r requirements-protocols.txt`.
Vedere [l'esempio multiprotocollo](configs/examples/multi_protocol.yaml).
`python run.py --check` verifica lettura e connessione senza inviare scritture.

## Compilazione e test

```bash
pip install -r requirements-dev.txt
python scripts/build_native.py
python -m pytest -q
SCADA_FORCE_PYTHON_CORE=1 python -m pytest -q
ruff check .
ruff format --check .
```

I test PostgreSQL richiedono un database di prova configurato con `SCADA_TEST_PG`.
La documentazione completa è disponibile in [English](README.en.md).

## Sicurezza e limiti

Il servizio ascolta su `127.0.0.1` per impostazione predefinita. Configurare `SCADA_API_TOKEN`:
quando il token è impostato, i comandi richiedono `X-API-Token`.
Le scritture sono limitate ai segnali `writable: true` e all'intervallo `min..max`.
Un nodo senza ridondanza; un token operatore senza ruoli. OPC UA usa il polling.
I preallarmi estrapolano tendenze e non prevedono guasti improvvisi.
La compatibilità deve essere verificata per ogni dispositivo.
La velocità del nucleo non rappresenta quella dell'intera piattaforma.
L'interfaccia supporta russo, inglese e cinese; le otto lingue riguardano la documentazione.

## Documentazione e licenza

Questa pagina è una guida sintetica. Riferimento dettagliato: [English](README.en.md) e
[Русский](README.md). Cronologia: [CHANGELOG.md](CHANGELOG.md).
MIT: [LICENSE.md](LICENSE.md). Autore: Anton Sergeev.
