from pathlib import Path

from scada_core.config.loader import TagConfig, load_app_config, parse_config_text
from scada_core.hmi.generator import generate_hmi, infer_widget

ROOT = Path(__file__).resolve().parents[1]


def test_areas_follow_process_flow() -> None:
    hmi = generate_hmi(load_app_config())
    assert [a["id"] for a in hmi["areas"]] == ["intake", "tank", "pump", "discharge"]
    xs = [a["x"] for a in hmi["areas"]]
    assert xs == sorted(xs)
    assert len(hmi["links"]) == 3
    assert hmi["stats"]["tags"] == 11


def test_main_widgets() -> None:
    hmi = generate_hmi(load_app_config())
    mains = {a["id"]: a["main"]["type"] for a in hmi["areas"]}
    assert mains == {"intake": "flow", "tank": "tank", "pump": "pump", "discharge": "valve"}


def test_widget_inference_multilingual() -> None:
    assert infer_widget(TagConfig(name="lvl", address=0, unit="%", label={"ru": "Уровень"})) == "tank"
    assert infer_widget(TagConfig(name="p1", address=0, unit="bar")) == "gauge"
    assert infer_widget(TagConfig(name="t_out", address=0, unit="°C")) == "thermometer"
    assert infer_widget(TagConfig(name="gate", address=0, function="coil")) == "valve"
    assert infer_widget(TagConfig(name="alarm_bit", address=0, function="coil")) == "indicator"
    assert infer_widget(TagConfig(name="level_setpoint", address=0, unit="%")) == "value"


def test_any_plant_can_be_generated() -> None:
    boiler = (ROOT / "web" / "assets" / "js" / "examples.js").read_text(encoding="utf-8")
    yaml_text = boiler.split("`")[1]
    hmi = generate_hmi(parse_config_text(yaml_text))
    assert [a["id"] for a in hmi["areas"]] == ["gas", "boiler", "circuit", "consumers"]
    assert hmi["title"]["zh"] == "3号锅炉房"


def test_groups_default_to_device() -> None:
    cfg = parse_config_text("devices: [{id: rtu, host: h, tags: [{name: a, address: 0}]}]")
    hmi = generate_hmi(cfg)
    assert hmi["areas"][0]["id"] == "rtu"
