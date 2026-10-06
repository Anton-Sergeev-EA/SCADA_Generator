# SCADA Generator

[Русский](README.md) · [English](README.en.md) · [中文](README.zh.md) · [हिन्दी](README.hi.md) · [Español](README.es.md) · **Français** · [Deutsch](README.de.md) · [Italiano](README.it.md)

## Présentation

Prototype de supervision industrielle configuré en YAML : acquisition de données,
IHM générée, cycle de vie des alarmes et analyse continue C++17/Python.
La démonstration utilise une station de pompage simulée et un serveur Modbus TCP local.
Les essais de simulation ne prouvent ni déploiement industriel, ni précision sur site,
ni certification indépendante des protocoles.

## Composants

- Adaptateurs Modbus TCP/RTU, OPC UA, MQTT et IEC 60870-5-104.
- IHM générée depuis `configs/config.yaml`.
- Alarmes avec hystérésis, temporisation, acquittement et mise en attente.
- Détecteur EWMA robuste et extrapolation de Holt en C++17 via pybind11.
- Analyse multivariée PCA/MSPC et solution Python avec tests de parité.
- API REST, WebSocket et archivage PostgreSQL avec migrations.

## Démarrage

```bash
pip install -r requirements.txt
python run.py --demo --open
```

Ouvrez `http://127.0.0.1:8000`. PostgreSQL est facultatif en mode démonstration.
Les scénarios permettent d'injecter des défauts ; les résultats concernent le simulateur.

## Configuration et adaptateurs

Les équipements et signaux sont définis dans `configs/config.yaml`.
Les champs d'adresse dépendent du protocole : `address`, `node`, `topic` ou `ioa`.
Installez les bibliothèques facultatives avec `pip install -r requirements-protocols.txt`.
Consultez [l'exemple multiprotocole](configs/examples/multi_protocol.yaml).
`python run.py --check` vérifie la connexion et la lecture sans envoyer d'écriture.

## Compilation et tests

```bash
pip install -r requirements-dev.txt
python scripts/build_native.py
python -m pytest -q
SCADA_FORCE_PYTHON_CORE=1 python -m pytest -q
ruff check .
ruff format --check .
```

Les tests PostgreSQL nécessitent une base de test configurée via `SCADA_TEST_PG`.
La référence complète est disponible en [anglais](README.en.md).

## Sécurité et limites

Le serveur écoute sur `127.0.0.1` par défaut. Configurez `SCADA_API_TOKEN` pour le contrôle :
les commandes exigent `X-API-Token` lorsque le jeton est configuré.
Les écritures sont limitées aux signaux `writable: true` et à leur plage `min..max`.
Un seul nœud sans redondance ; un jeton opérateur sans rôles. OPC UA utilise l'interrogation.
Les préalarmes extrapolent des tendances et ne prédisent pas les pannes soudaines.
La compatibilité doit être vérifiée pour chaque équipement.
Le débit du noyau ne représente pas le débit de toute la plateforme.
L'interface existe en russe, anglais et chinois ; les huit langues concernent la documentation.

## Documentation et licence

Cette page est un guide abrégé. Référence détaillée : [English](README.en.md) et
[Русский](README.md). Historique : [CHANGELOG.md](CHANGELOG.md).
MIT : [LICENSE.md](LICENSE.md). Auteur : Anton Sergeev.
