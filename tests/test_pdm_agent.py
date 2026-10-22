import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdm_agent.agent import MaintenanceAgent, Toolbox
from pdm_agent.diagnosis import diagnose
from pdm_agent.domain import Alarm
from pdm_agent.manuals import ManualIndex
from pdm_agent.plant import Plant
from pdm_agent.sensors import SensorConfig, detect_alarms, forecast
from pdm_agent.workflow import alarm_to_work_order

DATA = Path(__file__).resolve().parent.parent / "data"
CFG = SensorConfig("x.csv", "P-101", "bearing_temperature_c", warning=75, critical=85, spike_delta=6)


@pytest.fixture
def data_dir(tmp_path):
    shutil.copytree(DATA, tmp_path / "data")
    return tmp_path / "data"


def series(values, start=datetime(2026, 1, 1), step=timedelta(minutes=10)):
    return [(start + i * step, v) for i, v in enumerate(values)]


def make_alarm(severity="critical", value=86.0):
    return Alarm("ALM-1", "P-101", "bearing_temperature_c", value, 85, severity, datetime(2026, 1, 1))


# ---- sensors -------------------------------------------------------------

def test_one_alarm_per_event_reported_at_peak():
    readings = series([65] * 50 + [80, 87, 82] + [65] * 10)
    [alarm] = detect_alarms(readings, CFG)
    assert alarm.value == 87 and alarm.severity == "critical" and alarm.duration_min == 20


def test_jump_above_baseline_is_caught_below_absolute_limit():
    [alarm] = detect_alarms(series([60] * 50 + [67] + [60] * 5), CFG)
    assert alarm.severity == "warning" and alarm.threshold == 66


def test_normal_noise_raises_no_alarm():
    assert detect_alarms(series([65, 66, 64, 65.5] * 40), CFG) == []


def test_forecast_estimates_days_to_limit():
    readings = series([60 + d * 0.5 for d in range(20)], step=timedelta(days=1))  # 69.5 today
    fc = forecast(readings, CFG)
    assert fc.slope_per_day == pytest.approx(0.5)
    assert fc.days_to_limit == pytest.approx((75 - 69.5) / 0.5)


def test_forecast_flat_trend_has_no_eta():
    assert forecast(series([65] * 10, step=timedelta(days=1)), CFG).days_to_limit is None


def test_real_dataset_contains_the_october_critical_event():
    from pdm_agent.sensors import load_readings, load_sensor_configs

    [cfg] = load_sensor_configs(DATA)
    alarms = detect_alarms(load_readings(DATA / "sensors" / cfg.file), cfg)
    critical = [a for a in alarms if a.severity == "critical"]
    assert [a.timestamp.date().isoformat() for a in critical] == ["2025-10-09"]


# ---- plant, diagnosis, manuals ------------------------------------------

def test_resolve_asset_by_tag_and_by_description():
    plant = Plant(DATA)
    assert plant.resolve_asset("check p101 please")[0].id == "P-101"
    assert plant.resolve_asset("the air compressor")[0].id == "COMP-201"


def test_diagnosis_ranks_primary_indicator_first():
    findings = diagnose(make_alarm(), Plant(DATA))
    assert findings[0].failure_mode.code == "BRG-OVERHEAT"
    assert findings[0].confidence == "high"


def test_manual_search_finds_procedure():
    [top, *_] = ManualIndex(DATA / "manuals").search("how to replace bearing")
    assert "replacement" in top.heading.lower()


# ---- workflow -------------------------------------------------------------

def test_workflow_sandbox_does_not_write_or_notify(data_dir):
    before = (data_dir / "cmms" / "work_orders.json").read_text()
    sent = []
    result = alarm_to_work_order(make_alarm(), Plant(data_dir), notify=sent.append, sandbox=True)
    assert result.work_order.priority == "urgent"
    assert result.work_order.failure_mode == "BRG-OVERHEAT"
    assert sent == []
    assert (data_dir / "cmms" / "work_orders.json").read_text() == before


def test_workflow_live_saves_and_dedupes(data_dir):
    sent = []
    plant = Plant(data_dir)
    first = alarm_to_work_order(make_alarm(), plant, notify=sent.append, sandbox=False)
    second = alarm_to_work_order(make_alarm(), plant, notify=sent.append, sandbox=False)
    assert first.work_order and second.work_order is None
    saved = json.loads((data_dir / "cmms" / "work_orders.json").read_text())
    assert sum(w["id"] == first.work_order.id for w in saved) == 1
    assert len(sent) == 1


def test_low_confidence_cause_not_written_on_work_order(data_dir):
    alarm = Alarm("ALM-2", "P-101", "vibration_velocity_mm_s", 5, 4.5, "warning", datetime(2026, 1, 1))
    plant = Plant(data_dir)
    plant.assets["P-101"].failure_modes = ["BRG-OVERHEAT"]  # vibration is only secondary here
    result = alarm_to_work_order(alarm, plant, sandbox=True)
    assert result.work_order.failure_mode is None


# ---- Claude agent loop (fake client, no network) -------------------------

class FakeClient:
    """Replays scripted responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def test_agent_runs_tools_and_returns_answer(data_dir):
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", content=[
            tool_use("t1", "find_asset", {"query": "cooling pump 1"}),
            tool_use("t2", "search_manuals", {"query": "bearing overheating"}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[text("Check the grease first.")]),
    ])
    agent = MaintenanceAgent(Toolbox(data_dir, confirm=lambda wo: False), client=client)
    assert agent.ask("Why is pump 1 hot?") == "Check the grease first."

    # Both tool results go back in a single user message.
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert json.loads(results[0]["content"])[0]["id"] == "P-101"
    assert client.requests[0]["model"] == "claude-opus-5-5"


def test_agent_work_order_requires_confirmation(data_dir):
    order = {"asset_id": "P-101", "type": "corrective", "priority": "high",
             "description": "Regrease DE bearing", "failure_mode": "BRG-OVERHEAT"}
    responses = lambda: [
        SimpleNamespace(stop_reason="tool_use", content=[tool_use("t1", "create_work_order", order)]),
        SimpleNamespace(stop_reason="end_turn", content=[text("done")]),
    ]
    declined = FakeClient(responses())
    MaintenanceAgent(Toolbox(data_dir, confirm=lambda wo: False), client=declined).ask("make a WO")
    assert "cancelled" in declined.requests[1]["messages"][-1]["content"][0]["content"]

    accepted = FakeClient(responses())
    MaintenanceAgent(Toolbox(data_dir, confirm=lambda wo: True, sandbox=False), client=accepted).ask("make a WO")
    saved = json.loads((data_dir / "cmms" / "work_orders.json").read_text())
    assert saved[-1]["description"] == "Regrease DE bearing"


def test_agent_tool_error_is_reported_not_raised(data_dir):
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", content=[tool_use("t1", "no_such_tool", {})]),
        SimpleNamespace(stop_reason="end_turn", content=[text("sorry")]),
    ])
    MaintenanceAgent(Toolbox(data_dir, confirm=lambda wo: False), client=client).ask("hi")
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True
