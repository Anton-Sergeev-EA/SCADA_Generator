from scada_core.config.loader import AlarmConfig, TagConfig
from scada_core.engine.alarms import ACTIVE_ACK, ACTIVE_UNACK, NORMAL, RTN_UNACK, AlarmEngine

TAG = TagConfig(name="p", address=0, alarm_high=10.0, alarm_low=2.0, deadband=1.0)


def engine() -> tuple[AlarmEngine, list[str]]:
    eng = AlarmEngine(AlarmConfig(chatter_count=3, chatter_window_s=60))
    events: list[str] = []
    eng.subscribe(lambda ev: events.append(f"{ev.event}:{ev.alarm.kind}"))
    return eng, events


def test_one_event_per_transition_not_per_poll() -> None:
    """Регрессия: раньше аларм писался на каждом цикле опроса."""
    eng, events = engine()
    for i in range(100):
        eng.evaluate_tag("d", TAG, 15.0, ts=float(i))
    assert events == ["raise:H"]


def test_deadband_prevents_chattering() -> None:
    eng, events = engine()
    eng.evaluate_tag("d", TAG, 10.5, ts=0)
    eng.evaluate_tag("d", TAG, 9.5, ts=1)  # внутри зоны возврата — аларм держится
    assert events == ["raise:H"]
    eng.evaluate_tag("d", TAG, 8.9, ts=2)
    assert events == ["raise:H", "clear:H"]


def test_on_delay() -> None:
    eng, events = engine()
    tag = TagConfig(name="p", address=0, alarm_high=10.0, on_delay_s=3)
    eng.evaluate_tag("d", tag, 11, ts=0)
    eng.evaluate_tag("d", tag, 11, ts=2)
    assert events == []
    eng.evaluate_tag("d", tag, 11, ts=3.1)
    assert events == ["raise:H"]


def test_isa_state_machine() -> None:
    eng, _ = engine()
    eng.evaluate_tag("d", TAG, 11, ts=0)
    alarm = eng.visible_alarms()[0]
    assert alarm.state == ACTIVE_UNACK
    eng.evaluate_tag("d", TAG, 5, ts=1)
    assert alarm.state == RTN_UNACK
    assert eng.acknowledge(alarm.id)
    assert alarm.state == NORMAL
    eng.evaluate_tag("d", TAG, 11, ts=2)
    eng.acknowledge(alarm.id)
    assert alarm.state == ACTIVE_ACK
    eng.evaluate_tag("d", TAG, 5, ts=3)
    assert alarm.state == NORMAL
    assert alarm.activations == 2


def test_alarm_carries_device_id() -> None:
    """Регрессия: device_id был захардкожен как plc_main."""
    eng, _ = engine()
    eng.evaluate_tag("boiler_2", TAG, 0.5, ts=0)
    alarm = eng.visible_alarms()[0]
    assert alarm.device_id == "boiler_2" and alarm.kind == "L"


def test_shelving_suppresses_raise() -> None:
    eng, events = engine()
    eng.evaluate_tag("d", TAG, 11, ts=0)
    alarm = eng.visible_alarms()[0]
    eng.acknowledge(alarm.id, ts=1)
    eng.shelve(alarm.id, 100, ts=1)
    eng.evaluate_tag("d", TAG, 5, ts=2)
    eng.evaluate_tag("d", TAG, 11, ts=3)
    assert events.count("raise:H") == 1


def test_predicted_alerts_need_no_ack_and_are_excluded_from_kpi() -> None:
    eng, _ = engine()
    eng.set_condition("d", "p", "PRED_H", True, ts=0)
    eng.set_condition("d", "p", "PRED_H", False, ts=10)
    assert eng.visible_alarms() == []
    assert eng.kpi(now=20)["rate_last_10min"] == 0


def test_kpi_chattering_and_level() -> None:
    eng, _ = engine()
    for i in range(4):
        eng.evaluate_tag("d", TAG, 11, ts=i * 10)
        eng.evaluate_tag("d", TAG, 5, ts=i * 10 + 5)
    kpi = eng.kpi(now=60)
    assert kpi["chattering"] == ["d/p/H"]
    assert kpi["rate_last_10min"] == 4
    assert kpi["bad_actors"][0] == {"key": "d/p/H", "count": 4}


def test_comm_alarm() -> None:
    eng, events = engine()
    eng.comm_status("d", False, "timeout")
    eng.comm_status("d", True)
    assert events == ["raise:COMM", "clear:COMM"]
