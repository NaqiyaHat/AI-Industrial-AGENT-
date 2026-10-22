"""Maintenance domain model: the nouns every maintenance team already uses.

Keeping these as plain dataclasses means the rest of the code (and the LLM)
works with "assets", "work orders" and "failure modes" instead of raw JSON
rows from whatever system the customer happens to run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class FailureMode:
    code: str
    name: str
    category: str
    indicators: list[str]  # sensor parameters that reveal this failure
    actions: list[str]  # recommended corrective actions
    mtbf_hours: int | None = None


@dataclass
class Asset:
    id: str
    name: str
    type: str
    location: str
    manufacturer: str
    model: str
    criticality: str  # A = production stops, B = degraded, C = minor
    failure_modes: list[str] = field(default_factory=list)


@dataclass
class SparePart:
    sku: str
    name: str
    compatible_assets: list[str]
    stock: int
    reorder_point: int
    lead_time_days: int
    unit_cost: float

    @property
    def needs_reorder(self) -> bool:
        return self.stock <= self.reorder_point


@dataclass
class WorkOrder:
    id: str
    asset_id: str
    type: str  # corrective | preventive | predictive
    priority: str  # low | medium | high | urgent
    description: str
    failure_mode: str | None = None
    status: str = "open"  # open | in_progress | closed
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Alarm:
    id: str
    asset_id: str
    parameter: str
    value: float
    threshold: float
    severity: str  # warning | critical
    timestamp: datetime
    duration_min: int = 0

    def summary(self) -> str:
        return (
            f"{self.id} | {self.asset_id} | {self.parameter}={self.value:.1f} "
            f"(limit {self.threshold:.1f}) | {self.severity} | "
            f"{self.timestamp:%Y-%m-%d %H:%M} | {self.duration_min} min"
        )
