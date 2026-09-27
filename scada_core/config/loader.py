"""Загрузка и проверка конфигурации установки (configs/config.yaml)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

LANGUAGES = ("ru", "en", "zh")
FUNCTIONS = ("holding_register", "input_register", "coil", "discrete_input")
# Число регистров Modbus на тип; для остальных протоколов тип — это формат
# значения (bool — дискретный сигнал).
TYPES = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2, "bool": 1, "float": 2}
PROTOCOLS = ("modbus_tcp", "modbus_rtu", "opcua", "mqtt", "iec104")
# Чем адресуется тег в каждом протоколе.
TAG_ADDRESS = {
    "modbus_tcp": "address",
    "modbus_rtu": "address",
    "opcua": "node",
    "mqtt": "topic",
    "iec104": "ioa",
}
# Параметры устройства, специфичные для протокола (хранятся в options).
DEVICE_OPTIONS = {
    "modbus_rtu": ("serial_port", "baudrate", "parity", "stopbits", "bytesize"),
    "opcua": ("endpoint", "username", "password", "security"),
    "mqtt": ("username", "password", "tls", "client_id", "stale_s"),
    "iec104": ("common_address",),
}
DEFAULT_PORTS = {"modbus_tcp": 502, "mqtt": 1883, "iec104": 2404}
WIDGETS = ("tank", "pump", "valve", "gauge", "thermometer", "flow", "value", "indicator")
DEFAULT_CONFIG_PATH = Path(os.getenv("SCADA_CONFIG", "configs/config.yaml"))

Label = dict[str, str]


class ConfigError(ValueError):
    """Ошибка конфигурации; errors — список (путь, сообщение)."""

    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{p}: {m}" for p, m in errors))


def make_label(value: Any, fallback: str) -> Label:
    """Строку или словарь {ru,en,zh} приводит к словарю на всех языках."""
    if isinstance(value, dict):
        base = next((str(value[k]) for k in LANGUAGES if value.get(k)), fallback)
        return {lang: str(value.get(lang) or base) for lang in LANGUAGES}
    text = str(value) if value else fallback
    return {lang: text for lang in LANGUAGES}


@dataclass
class TagConfig:
    name: str
    address: int | None = None  # Modbus: номер регистра/бита
    node: str | None = None  # OPC UA: NodeId, например ns=2;s=Tank.Level
    topic: str | None = None  # MQTT: топик со значением
    json_path: str | None = None  # MQTT: путь в JSON-сообщении, например data.value
    command_topic: str | None = None  # MQTT: куда публиковать команды записи
    ioa: int | None = None  # МЭК 104: адрес объекта информации
    command_ioa: int | None = None  # МЭК 104: адрес объекта команды
    function: str = "holding_register"
    type: str = "uint16"
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    label: Label = field(default_factory=dict)
    min: float | None = None
    max: float | None = None
    alarm_hh: float | None = None
    alarm_high: float | None = None
    alarm_low: float | None = None
    alarm_ll: float | None = None
    deadband: float = 0.0
    on_delay_s: float = 0.0
    writable: bool = False
    ml: bool = True
    widget: str | None = None
    group: str | None = None
    decimals: int | None = None

    @property
    def is_bit(self) -> bool:
        return self.function in ("coil", "discrete_input") or self.type == "bool"

    @property
    def source(self) -> str:
        """Адрес тега в терминах его протокола — для оператора и журнала."""
        if self.node:
            return self.node
        if self.topic:
            return self.topic + (f" → {self.json_path}" if self.json_path else "")
        if self.ioa is not None:
            return f"IOA {self.ioa}"
        short = {"holding_register": "HR", "input_register": "IR", "coil": "CO", "discrete_input": "DI"}
        return f"{short.get(self.function, self.function)} {self.address}"

    @property
    def register_count(self) -> int:
        return 1 if self.is_bit else TYPES[self.type]

    def limits(self) -> dict[str, float]:
        pairs = {
            "HH": self.alarm_hh,
            "H": self.alarm_high,
            "L": self.alarm_low,
            "LL": self.alarm_ll,
        }
        return {k: float(v) for k, v in pairs.items() if v is not None}


@dataclass
class DeviceConfig:
    id: str
    host: str = "localhost"
    port: int = 502
    slave_id: int = 1
    protocol: str = "modbus_tcp"
    options: dict[str, Any] = field(default_factory=dict)
    name: Label = field(default_factory=dict)
    enabled: bool = True
    timeout: float = 3.0
    retries: int = 3
    poll_interval_ms: int = 1000
    tags: list[TagConfig] = field(default_factory=list)


@dataclass
class MLConfig:
    enabled: bool = True
    warmup: int = 30
    z_threshold: float = 4.0
    train_samples: int = 300
    forecast_horizon_s: float = 900.0


@dataclass
class AlarmConfig:
    standing_after_s: float = 600.0
    chatter_count: int = 3
    chatter_window_s: float = 60.0
    flood_per_10min: int = 10


@dataclass
class StorageConfig:
    history_size: int = 3600
    postgres_enabled: bool = True


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AppConfig:
    name: Label
    default_language: str
    flow: list[str]
    devices: list[DeviceConfig]
    groups: dict[str, Label] = field(default_factory=dict)
    ml: MLConfig = field(default_factory=MLConfig)
    alarms: AlarmConfig = field(default_factory=AlarmConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    @property
    def active_devices(self) -> list[DeviceConfig]:
        return [d for d in self.devices if d.enabled]

    def iter_tags(self) -> list[tuple[DeviceConfig, TagConfig]]:
        return [(d, t) for d in self.active_devices for t in d.tags]

    def find_tag(self, device_id: str, tag_name: str) -> TagConfig | None:
        for d in self.devices:
            if d.id == device_id:
                return next((t for t in d.tags if t.name == tag_name), None)
        return None


def _num(raw: dict[str, Any], key: str, path: str, errors: list, *, integer: bool = False) -> Any:
    value = raw.get(key)
    if value is None:
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        errors.append((f"{path}.{key}", f"ожидалось число, получено {value!r}"))
        return None


def _count_conflict(tag: TagConfig, raw: dict[str, Any]) -> bool:
    """Старый формат задавал count явно; он должен совпадать с типом."""
    count = raw.get("count")
    if count is None or tag.is_bit:
        return False
    try:
        return int(count) not in (1, tag.register_count)
    except (TypeError, ValueError):
        return True


def _parse_tag(raw: Any, path: str, errors: list) -> TagConfig | None:
    if not isinstance(raw, dict):
        errors.append((path, "тег должен быть словарём"))
        return None
    name = raw.get("name")
    if not name or not isinstance(name, str):
        errors.append((f"{path}.name", "обязательное поле"))
        return None
    address = _num(raw, "address", path, errors, integer=True)
    if address is not None and not 0 <= address <= 65535:
        errors.append((f"{path}.address", "адрес Modbus должен быть в диапазоне 0..65535"))
    function = raw.get("function", "holding_register")
    if function not in FUNCTIONS:
        errors.append((f"{path}.function", f"допустимо: {', '.join(FUNCTIONS)}"))
        function = "holding_register"
    dtype = raw.get("type", "uint16")
    if dtype not in TYPES:
        errors.append((f"{path}.type", f"допустимо: {', '.join(TYPES)}"))
        dtype = "uint16"
    widget = raw.get("widget")
    if widget is not None and widget not in WIDGETS:
        errors.append((f"{path}.widget", f"допустимо: {', '.join(WIDGETS)}"))
        widget = None

    tag = TagConfig(
        name=name,
        address=address,
        node=str(raw["node"]) if raw.get("node") else None,
        topic=str(raw["topic"]) if raw.get("topic") else None,
        json_path=str(raw["json_path"]) if raw.get("json_path") else None,
        command_topic=str(raw["command_topic"]) if raw.get("command_topic") else None,
        ioa=_num(raw, "ioa", path, errors, integer=True),
        command_ioa=_num(raw, "command_ioa", path, errors, integer=True),
        function=function,
        type=dtype,
        scale=_num(raw, "scale", path, errors) or 1.0,
        offset=_num(raw, "offset", path, errors) or 0.0,
        unit=str(raw.get("unit", "")),
        label=make_label(raw.get("label"), name),
        min=_num(raw, "min", path, errors),
        max=_num(raw, "max", path, errors),
        alarm_hh=_num(raw, "alarm_hh", path, errors),
        alarm_high=_num(raw, "alarm_high", path, errors),
        alarm_low=_num(raw, "alarm_low", path, errors),
        alarm_ll=_num(raw, "alarm_ll", path, errors),
        deadband=abs(_num(raw, "deadband", path, errors) or 0.0),
        on_delay_s=max(0.0, _num(raw, "on_delay_s", path, errors) or 0.0),
        writable=bool(raw.get("writable", False)),
        ml=bool(raw.get("ml", function in ("holding_register", "input_register") and dtype != "bool")),
        widget=widget,
        group=str(raw["group"]) if raw.get("group") else None,
        decimals=_num(raw, "decimals", path, errors, integer=True),
    )
    lim = tag.limits()
    order = [lim.get(k) for k in ("LL", "L", "H", "HH")]
    present = [v for v in order if v is not None]
    if present != sorted(present):
        errors.append((path, "пороги должны идти по возрастанию: LL < L < H < HH"))
    if _count_conflict(tag, raw):
        errors.append((f"{path}.count", "count не согласуется с type"))
    return tag


def parse_config(data: Any) -> AppConfig:
    """Разбирает словарь конфигурации; при ошибках бросает ConfigError."""
    errors: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        raise ConfigError([("$", "корень конфигурации должен быть словарём")])

    project = data.get("project") or {}
    raw_devices = data.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        errors.append(("devices", "нужен непустой список устройств"))
        raw_devices = []

    devices: list[DeviceConfig] = []
    seen_ids: set[str] = set()
    for i, rd in enumerate(raw_devices):
        path = f"devices[{i}]"
        if not isinstance(rd, dict):
            errors.append((path, "устройство должно быть словарём"))
            continue
        dev_id = rd.get("id")
        if not dev_id:
            errors.append((f"{path}.id", "обязательное поле"))
            continue
        if dev_id in seen_ids:
            errors.append((f"{path}.id", f"повторяющийся id {dev_id!r}"))
        seen_ids.add(dev_id)
        tags: list[TagConfig] = []
        names: set[str] = set()
        protocol = str(rd.get("protocol", "modbus_tcp"))
        if protocol not in PROTOCOLS:
            errors.append((f"{path}.protocol", f"допустимо: {', '.join(PROTOCOLS)}"))
            protocol = "modbus_tcp"
        key = TAG_ADDRESS[protocol]
        for j, rt in enumerate(rd.get("tags") or []):
            tpath = f"{path}.tags[{j}]"
            tag = _parse_tag(rt, tpath, errors)
            if tag is None:
                continue
            if getattr(tag, key) is None:
                errors.append((f"{tpath}.{key}", f"обязательное поле для протокола {protocol}"))
            if tag.name in names:
                errors.append((f"{path}.tags[{j}].name", f"повторяющееся имя {tag.name!r}"))
            names.add(tag.name)
            tags.append(tag)
        options = {k: rd[k] for k in DEVICE_OPTIONS.get(protocol, ()) if rd.get(k) is not None}
        if protocol == "modbus_rtu" and not options.get("serial_port"):
            errors.append(
                (f"{path}.serial_port", "обязательное поле: /dev/ttyUSB0, COM3 или socket://шлюз:порт")
            )
        if protocol == "opcua" and not options.get("endpoint"):
            errors.append((f"{path}.endpoint", "обязательное поле: opc.tcp://хост:4840"))
        devices.append(
            DeviceConfig(
                id=str(dev_id),
                protocol=protocol,
                options=options,
                host=str(rd.get("host", "localhost")),
                port=_num(rd, "port", path, errors, integer=True) or DEFAULT_PORTS.get(protocol, 502),
                slave_id=_num(rd, "slave_id", path, errors, integer=True) or 1,
                name=make_label(rd.get("name"), str(dev_id)),
                enabled=bool(rd.get("enabled", True)),
                timeout=_num(rd, "timeout", path, errors) or 3.0,
                retries=_num(rd, "retries", path, errors, integer=True) or 3,
                poll_interval_ms=max(100, _num(rd, "poll_interval_ms", path, errors, integer=True) or 1000),
                tags=tags,
            )
        )

    ml_raw = data.get("ml") or {}
    al_raw = data.get("alarms") or {}
    st_raw = data.get("storage") or {}
    pg_raw = st_raw.get("postgres") or {}
    sv_raw = data.get("server") or {}
    lang = project.get("default_language", "ru")
    if lang not in LANGUAGES:
        errors.append(("project.default_language", f"допустимо: {', '.join(LANGUAGES)}"))
        lang = "ru"

    if errors:
        raise ConfigError(errors)

    return AppConfig(
        name=make_label(project.get("name"), "SCADA Generator"),
        default_language=lang,
        flow=[str(x) for x in project.get("flow") or []],
        devices=devices,
        groups={str(k): make_label(v, str(k)) for k, v in (project.get("groups") or {}).items()},
        ml=MLConfig(
            enabled=bool(ml_raw.get("enabled", True)),
            warmup=int(ml_raw.get("warmup", 30)),
            z_threshold=float(ml_raw.get("z_threshold", 4.0)),
            train_samples=int(ml_raw.get("train_samples", 300)),
            forecast_horizon_s=float(ml_raw.get("forecast_horizon_s", 900.0)),
        ),
        alarms=AlarmConfig(
            standing_after_s=float(al_raw.get("standing_after_s", 600)),
            chatter_count=int(al_raw.get("chatter_count", 3)),
            chatter_window_s=float(al_raw.get("chatter_window_s", 60)),
            flood_per_10min=int(al_raw.get("flood_per_10min", 10)),
        ),
        storage=StorageConfig(
            history_size=int(st_raw.get("history_size", 3600)),
            postgres_enabled=bool(pg_raw.get("enabled", True)),
        ),
        server=ServerConfig(
            host=str(sv_raw.get("host", "127.0.0.1")),
            port=int(sv_raw.get("port", 8000)),
        ),
    )


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(text: str, *, use_environment: bool = True) -> str:
    """Подставляет ${VAR} и ${VAR:-по_умолчанию} из окружения — один и тот
    же config.yaml работает на стенде, в Docker и на объекте.

    use_environment=False — только значения по умолчанию. Так разбирается
    YAML, присланный через интерфейс: иначе ${DB_PASSWORD} в подписи тега
    показал бы секрет сервера на мнемосхеме."""

    def repl(m: re.Match[str]) -> str:
        if not use_environment:
            return m.group(2) if m.group(2) is not None else m.group(0)
        value = os.getenv(m.group(1))
        if value is None:
            if m.group(2) is None:
                raise ConfigError([(f"${{{m.group(1)}}}", "переменная окружения не задана")])
            return m.group(2)
        return value

    return _ENV_REF.sub(repl, text)


def parse_config_text(text: str, *, use_environment: bool = True) -> AppConfig:
    text = expand_env(text, use_environment=use_environment)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"строка {mark.line + 1}" if mark else "YAML"
        raise ConfigError([(where, str(getattr(exc, "problem", exc)))]) from exc
    return parse_config(data)


def load_app_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Конфиг не найден: {p}")
    return parse_config_text(p.read_text(encoding="utf-8"))


def database_config() -> dict[str, Any]:
    """Параметры PostgreSQL из переменных окружения (.env)."""
    password = os.getenv("DB_PASSWORD")
    if not password:
        logger.warning("DB_PASSWORD не задан — скопируйте .env.example в .env и укажите пароль.")
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "name": os.getenv("DB_NAME", "scada_generator"),
        "user": os.getenv("DB_USER", "scada"),
        "password": password,
    }


class ConfigLoader:
    """Совместимый со старым кодом интерфейс: словари вместо dataclass."""

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH) -> None:
        self.config_path = Path(config_path)
        self._config: dict[str, Any] | None = None
        self.app: AppConfig | None = None

    def load(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Конфиг не найден: {self.config_path}")
        text = expand_env(self.config_path.read_text(encoding="utf-8"))
        self._config = yaml.safe_load(text)
        self.app = parse_config(self._config)
        logger.info("Конфигурация загружена из %s", self.config_path)
        return self._config

    def get_devices(self) -> list[dict[str, Any]]:
        if self._config is None:
            self.load()
        assert self._config is not None
        return [d for d in self._config.get("devices", []) if d.get("enabled", True)]

    def get_database_config(self) -> dict[str, Any]:
        return database_config()


_config_loader: ConfigLoader | None = None


def get_config() -> ConfigLoader:
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
        _config_loader.load()
    return _config_loader
