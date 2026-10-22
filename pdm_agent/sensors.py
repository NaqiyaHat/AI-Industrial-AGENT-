"""Condition monitoring: turn raw sensor readings into alarms and forecasts.

Two complementary checks:

* ``detect_alarms`` - something is wrong *now* (a reading crossed a limit, or
  jumped well above its own recent baseline).
* ``forecast`` - something will be wrong *later*: fit a straight line to the
  recent trend and estimate how many days remain before the limit is reached.
  This is the "predictive" in predictive maintenance.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .domain import Alarm

Reading = tuple[datetime, float]


@dataclass
class SensorConfig:
    file: str
    asset_id: str
    parameter: str
    warning: float
    critical: float
    spike_delta: float  # rise above the 24 h median that counts as abnormal
    unit: str = ""


@dataclass
class Forecast:
    asset_id: str
    parameter: str
    current_baseline: float
    slope_per_day: float
    limit: float
    days_to_limit: float | None  # None when the trend is flat or falling

    def summary(self) -> str:
        trend = f"{self.slope_per_day:+.3f}/day"
        if self.days_to_limit is None:
            eta = "no upward trend"
        else:
            eta = f"~{self.days_to_limit:.0f} days until {self.limit:g} limit"
        return f"{self.asset_id} {self.parameter}: baseline {self.current_baseline:.1f}, trend {trend}, {eta}"


def load_sensor_configs(data_dir: str | Path) -> list[SensorConfig]:
    path = Path(data_dir) / "sensors" / "sensors.json"
    return [SensorConfig(**c) for c in json.loads(path.read_text())]


def load_readings(path: str | Path) -> list[Reading]:
    """Read a ``timestamp,value`` CSV, skipping blank or malformed rows."""
    readings: list[Reading] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if len(row) < 2 or not row[1].strip():
                continue
            try:
                readings.append((_parse_time(row[0]), float(row[1])))
            except ValueError:
                continue
    readings.sort(key=lambda r: r[0])
    return readings


def _parse_time(text: str) -> datetime:
    for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            pass
    raise ValueError(f"Unrecognised timestamp {text!r}")


def detect_alarms(readings: list[Reading], cfg: SensorConfig, baseline_points: int = 144) -> list[Alarm]:
    """Group consecutive abnormal readings into one alarm per event.

    A reading is abnormal when it is above the warning limit, or more than
    ``spike_delta`` above the median of the previous ``baseline_points``
    readings (144 x 10 min = 24 h). Each event is reported once, at its peak.
    """
    alarms: list[Alarm] = []
    event: list[Reading] = []
    window: list[float] = []
    event_baseline = 0.0

    def close_event() -> None:
        if not event:
            return
        peak_time, peak = max(event, key=lambda r: r[1])
        severity = "critical" if peak >= cfg.critical else "warning"
        if severity == "critical":
            threshold = cfg.critical
        elif peak >= cfg.warning:
            threshold = cfg.warning
        else:  # caught by the jump-above-baseline rule
            threshold = round(event_baseline + cfg.spike_delta, 1)
        minutes = int((event[-1][0] - event[0][0]).total_seconds() // 60)
        alarms.append(Alarm(
            id=f"ALM-{cfg.asset_id}-{peak_time:%Y%m%d%H%M}",
            asset_id=cfg.asset_id,
            parameter=cfg.parameter,
            value=peak,
            threshold=threshold,
            severity=severity,
            timestamp=peak_time,
            duration_min=minutes,
        ))
        event.clear()

    for ts, value in readings:
        baseline = statistics.median(window) if len(window) >= 12 else value
        abnormal = value >= cfg.warning or value - baseline >= cfg.spike_delta
        if abnormal:
            if not event:
                event_baseline = baseline
            event.append((ts, value))
        else:
            close_event()
            # Only normal readings feed the baseline, so a long fault cannot
            # drag the baseline up and hide itself.
            window.append(value)
            if len(window) > baseline_points:
                window.pop(0)
    close_event()
    return alarms


def forecast(readings: list[Reading], cfg: SensorConfig, days: int = 60) -> Forecast:
    """Fit a least-squares line to daily medians of the last ``days`` days."""
    if not readings:
        raise ValueError("No readings")
    end = readings[-1][0]
    daily: dict[int, list[float]] = {}
    for ts, value in readings:
        age = (end - ts).days
        if age < days:
            daily.setdefault(-age, []).append(value)
    xs = sorted(daily)
    ys = [statistics.median(daily[x]) for x in xs]

    if len(xs) < 2:
        slope, intercept = 0.0, ys[0]
    else:
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        var = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
        intercept = my - slope * mx

    baseline = intercept  # fitted value at day 0 (= today)
    days_to_limit = None
    if slope > 1e-6:
        days_to_limit = max(0.0, (cfg.warning - baseline) / slope)
    return Forecast(cfg.asset_id, cfg.parameter, baseline, slope, cfg.warning, days_to_limit)
