import pytest

from scada_core.config.loader import ConfigError, ConfigLoader, load_app_config, parse_config_text


def test_default_config_is_valid() -> None:
    cfg = load_app_config()
    assert cfg.default_language == "ru"
    assert set(cfg.name) == {"ru", "en", "zh"}
    names = [t.name for _, t in cfg.iter_tags()]
    assert "tank_level" in names and len(names) == len(set(names))


def test_legacy_loader_interface() -> None:
    loader = ConfigLoader()
    loader.load()
    assert loader.get_devices()[0]["id"] == "plc_main"


def test_label_fallbacks() -> None:
    cfg = parse_config_text(
        """
devices:
  - id: d
    host: h
    tags:
      - {name: a, address: 0, label: "Только русский"}
      - {name: b, address: 1, label: {en: "Only English"}}
      - {name: c, address: 2}
"""
    )
    a, b, c = cfg.devices[0].tags
    assert a.label["zh"] == "Только русский"
    assert b.label["ru"] == "Only English"
    assert c.label["en"] == "c"


def errors_of(text: str) -> list[str]:
    with pytest.raises(ConfigError) as exc:
        parse_config_text(text)
    return [path for path, _ in exc.value.errors]


def test_validation_reports_every_problem_with_path() -> None:
    paths = errors_of(
        """
devices:
  - id: d
    host: h
    tags:
      - {name: a}
      - {name: b, address: 1, function: holding}
      - {name: b, address: 2}
      - {name: c, address: 3, alarm_low: 50, alarm_high: 10}
      - {name: d, address: 70000}
"""
    )
    assert "devices[0].tags[0].address" in paths
    assert "devices[0].tags[1].function" in paths
    assert "devices[0].tags[2].name" in paths
    assert "devices[0].tags[3]" in paths
    assert "devices[0].tags[4].address" in paths


def test_yaml_syntax_error_has_line() -> None:
    paths = errors_of("devices:\n  - id: [unclosed\n")
    assert paths[0].startswith("строка")


def test_empty_devices() -> None:
    assert errors_of("project: {name: x}") == ["devices"]


def test_environment_substitution(monkeypatch) -> None:
    text = "devices: [{id: d, host: '${PLC_HOST:-localhost}', port: ${PLC_PORT:-502}, tags: []}]"
    monkeypatch.delenv("PLC_HOST", raising=False)
    monkeypatch.delenv("PLC_PORT", raising=False)
    dev = parse_config_text(text).devices[0]
    assert (dev.host, dev.port) == ("localhost", 502)
    monkeypatch.setenv("PLC_HOST", "10.0.0.7")
    monkeypatch.setenv("PLC_PORT", "1502")
    dev = parse_config_text(text).devices[0]
    assert (dev.host, dev.port) == ("10.0.0.7", 1502)
    assert errors_of("devices: [{id: d, host: '${NO_SUCH_VAR_X}', tags: []}]") == ["${NO_SUCH_VAR_X}"]
