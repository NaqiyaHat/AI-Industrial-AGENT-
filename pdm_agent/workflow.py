"""Alarm -> work order: the everyday maintenance routine, automated.

    alarm -> diagnose -> check history -> check spare parts
          -> draft work order -> notify technician -> submit to CMMS

Every step is ordinary code, so it is fast, testable and costs nothing to run.
In sandbox mode (the default) the two steps with side effects - notifying and
submitting - are logged but not executed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .diagnosis import diagnose
from .domain import Alarm, WorkOrder
from .plant import Plant, new_work_order_id

Notifier = Callable[[str], None]


@dataclass
class StepResult:
    name: str
    status: str  # ok | skipped | sandboxed | stopped
    detail: str


@dataclass
class WorkflowResult:
    alarm: Alarm
    steps: list[StepResult] = field(default_factory=list)
    work_order: WorkOrder | None = None

    def log(self, name: str, status: str, detail: str) -> None:
        self.steps.append(StepResult(name, status, detail))

    def render(self) -> str:
        marks = {"ok": "+", "skipped": "-", "sandboxed": "~", "stopped": "!"}
        lines = [f"Alarm {self.alarm.summary()}"]
        lines += [f"  [{marks[s.status]}] {s.name:<20} {s.detail}" for s in self.steps]
        return "\n".join(lines)


# Priority from alarm severity x asset criticality (A = most critical).
_PRIORITY = {
    ("critical", "A"): "urgent", ("critical", "B"): "high", ("critical", "C"): "medium",
    ("warning", "A"): "high", ("warning", "B"): "medium", ("warning", "C"): "low",
}


def alarm_to_work_order(
    alarm: Alarm,
    plant: Plant,
    notify: Notifier = print,
    sandbox: bool = True,
) -> WorkflowResult:
    result = WorkflowResult(alarm)
    asset = plant.assets.get(alarm.asset_id)
    if asset is None:
        result.log("resolve_asset", "stopped", f"unknown asset {alarm.asset_id}")
        return result

    # 1. Diagnose
    findings = diagnose(alarm, plant)
    top = findings[0] if findings else None
    result.log(
        "diagnose",
        "ok",
        ", ".join(f"{d.failure_mode.code} ({d.confidence})" for d in findings) or "no known cause",
    )

    # 2. History - skip if an open work order already covers this problem,
    #    so a sensor that keeps alarming does not flood the backlog.
    open_wos = plant.work_orders_for(asset.id, open_only=True)
    duplicate = next(
        (w for w in open_wos if top and w.failure_mode == top.failure_mode.code), None
    )
    if duplicate:
        result.log("check_history", "stopped", f"already covered by open {duplicate.id}")
        return result
    result.log("check_history", "ok", f"{len(open_wos)} open work order(s), none for this cause")

    # 3. Spare parts
    parts = plant.spare_parts_for(asset.id)
    parts_text = ", ".join(
        f"{p.sku} x{p.stock}{' (REORDER)' if p.needs_reorder else ''}" for p in parts
    ) or "none listed"
    result.log("check_spare_parts", "ok", parts_text)

    # 4. Draft work order. A low-confidence guess is not written onto the WO
    #    as the cause; the technician sees it in the description instead.
    failure_code = top.failure_mode.code if top and top.confidence != "low" else None
    actions = ", ".join(top.failure_mode.actions) if top else "inspect asset"
    wo = WorkOrder(
        id=new_work_order_id(),
        asset_id=asset.id,
        type="corrective" if alarm.severity == "critical" else "predictive",
        priority=_PRIORITY.get((alarm.severity, asset.criticality), "medium"),
        failure_mode=failure_code,
        description=(
            f"Auto-generated from {alarm.id}: {alarm.parameter} peaked at {alarm.value:.1f} "
            f"(limit {alarm.threshold:g}) for {alarm.duration_min} min. "
            f"Probable cause: {top.failure_mode.name if top else 'unknown'}. "
            f"Recommended: {actions}. Parts: {parts_text}."
        ),
    )
    result.work_order = wo
    result.log("draft_work_order", "ok", f"{wo.id} {wo.type}, priority {wo.priority}")

    # 5. Notify
    message = f"[{wo.priority.upper()}] {asset.name} ({asset.id}): {wo.description}"
    if sandbox:
        result.log("notify_technician", "sandboxed", "message prepared, not sent")
    else:
        notify(message)
        result.log("notify_technician", "ok", "sent")

    # 6. Submit
    plant.create_work_order(wo, sandbox=sandbox)
    if sandbox:
        result.log("submit_work_order", "sandboxed", "kept in memory, not written to CMMS")
    else:
        result.log("submit_work_order", "ok", f"{wo.id} saved")
    return result
