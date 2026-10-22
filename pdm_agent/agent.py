"""The conversational maintenance agent, powered by Claude.

Claude does the language part (understanding the technician, deciding what to
look up, writing the answer). Every fact comes from a tool that reads the plant
data, the sensors or the manuals, and every write goes through a confirmation.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import anthropic

from .domain import WorkOrder
from .manuals import ManualIndex
from .plant import Plant
from .sensors import detect_alarms, forecast, load_readings, load_sensor_configs

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are a maintenance assistant for an industrial plant. You help \
technicians and maintenance planners understand equipment condition and decide what to do.

Ground every statement about the plant in tool results: asset details, work orders, spare \
parts, sensor alarms, trend forecasts and manual excerpts. If the tools do not have the \
answer, say so plainly rather than guessing. When you quote a procedure from a manual, cite \
the manual file name.

Resolve vague equipment references with find_asset first. If more than one asset matches, \
ask which one is meant. Before creating a work order, check for an open work order that \
already covers the problem. Keep answers short and practical: technicians read them on a \
phone on the shop floor."""

TOOLS = [
    {
        "name": "find_asset",
        "description": "Find assets matching a free-text reference such as 'P-101', 'cooling pump' or 'the compressor'. Returns candidate assets, best match first.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Equipment reference as the user wrote it"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_asset_status",
        "description": "Full picture of one asset: details, known failure modes, open work orders, compatible spare parts with stock, sensor alarms in the last 30 days and the trend forecast (days until the warning limit).",
        "input_schema": {
            "type": "object",
            "properties": {"asset_id": {"type": "string", "description": "Exact asset id, e.g. P-101"}},
            "required": ["asset_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "search_manuals",
        "description": "Search equipment manuals for procedures, specifications and troubleshooting steps. Returns the most relevant sections with their source file.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "create_work_order",
        "description": "Create a maintenance work order. The user is asked to confirm before it is saved. Only call this when the user asks for a work order or agrees to one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "type": {"type": "string", "enum": ["corrective", "preventive", "predictive"]},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                "description": {"type": "string"},
                "failure_mode": {
                    "type": ["string", "null"],
                    "description": "Failure mode code from get_asset_status, or null if unknown",
                },
            },
            "required": ["asset_id", "type", "priority", "description", "failure_mode"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

Confirm = Callable[[WorkOrder], bool]


class Toolbox:
    """Executes the tools above against local data. Usable without Claude too."""

    def __init__(self, data_dir: str | Path, confirm: Confirm, sandbox: bool = True):
        self.data_dir = Path(data_dir)
        self.plant = Plant(self.data_dir)
        self.manuals = ManualIndex(self.data_dir / "manuals")
        self.sensors = load_sensor_configs(self.data_dir)
        self.confirm = confirm
        self.sandbox = sandbox

    def run(self, name: str, args: dict) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ValueError(f"Unknown tool {name}")
        return json.dumps(handler(**args), default=str)

    def tool_find_asset(self, query: str) -> list[dict]:
        return [
            {"id": a.id, "name": a.name, "location": a.location, "criticality": a.criticality}
            for a in self.plant.resolve_asset(query)[:5]
        ]

    def tool_get_asset_status(self, asset_id: str) -> dict:
        asset = self.plant.assets.get(asset_id)
        if asset is None:
            return {"error": f"No asset {asset_id}. Use find_asset first."}
        status = {
            "asset": asdict(asset),
            "failure_modes": [asdict(f) for f in self.plant.failure_modes_for(asset_id)],
            "open_work_orders": [w.to_dict() for w in self.plant.work_orders_for(asset_id, open_only=True)],
            "spare_parts": [
                {**asdict(p), "needs_reorder": p.needs_reorder} for p in self.plant.spare_parts_for(asset_id)
            ],
            "sensors": [],
        }
        for cfg in self.sensors:
            if cfg.asset_id != asset_id:
                continue
            readings = load_readings(self.data_dir / "sensors" / cfg.file)
            since = readings[-1][0] - timedelta(days=30)
            recent = [a for a in detect_alarms(readings, cfg) if a.timestamp >= since]
            status["sensors"].append({
                "parameter": cfg.parameter,
                "unit": cfg.unit,
                "latest_reading": readings[-1][1],
                "latest_time": readings[-1][0],
                "warning_limit": cfg.warning,
                "critical_limit": cfg.critical,
                "alarms_last_30_days": [a.summary() for a in recent],
                "forecast": forecast(readings, cfg).summary(),
            })
        return status

    def tool_search_manuals(self, query: str) -> list[dict]:
        return [asdict(p) for p in self.manuals.search(query)]

    def tool_create_work_order(self, **fields) -> dict:
        wo = WorkOrder(id="", **fields)
        if fields["asset_id"] not in self.plant.assets:
            return {"error": f"No asset {fields['asset_id']}"}
        if not self.confirm(wo):
            return {"status": "cancelled by user"}
        wo = self.plant.create_work_order(wo, sandbox=self.sandbox)
        return {"status": "sandbox: not saved" if self.sandbox else "saved", "work_order": wo.to_dict()}


class MaintenanceAgent:
    def __init__(self, toolbox: Toolbox, model: str = MODEL, client: anthropic.Anthropic | None = None):
        self.toolbox = toolbox
        self.model = model
        self.client = client or anthropic.Anthropic()
        self.messages: list = []

    def ask(self, question: str) -> str:
        self.messages.append({"role": "user", "content": question})
        while True:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.messages,
                output_config={"effort": "medium"},
                # If a safety classifier declines, retry on Anthropic's recommended model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                self.messages.pop()  # leave history consistent for the next question
                return "Sorry, I can't help with that request."

            # Keep the full content (including thinking blocks) for the next turn.
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                return "".join(b.text for b in response.content if b.type == "text").strip()

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    output, is_error = self.toolbox.run(block.name, block.input), False
                except Exception as exc:  # report tool failures back to Claude
                    output, is_error = f"{type(exc).__name__}: {exc}", True
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                })
            self.messages.append({"role": "user", "content": results})
