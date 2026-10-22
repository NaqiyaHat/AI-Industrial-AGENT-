"""The Plant: one object that knows the assets, parts, failure modes and work orders.

Data is read from a folder of JSON files that stands in for a real CMMS
(SAP PM, Maximo, UpKeep...). To connect a real system you replace this class's
loading/saving methods; nothing else in the project has to change.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from .domain import Asset, FailureMode, SparePart, WorkOrder

_WORD = re.compile(r"[a-z0-9]+")


class Plant:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        cmms = self.data_dir / "cmms"
        self.assets = {a["id"]: Asset(**a) for a in _load(cmms / "assets.json")}
        self.failure_modes = {f["code"]: FailureMode(**f) for f in _load(cmms / "failure_modes.json")}
        self.spare_parts = [SparePart(**p) for p in _load(cmms / "spare_parts.json")]
        self._wo_path = cmms / "work_orders.json"
        self.work_orders = [WorkOrder(**w) for w in _load(self._wo_path)]

    # ---- look-ups -------------------------------------------------------

    def resolve_asset(self, text: str) -> list[Asset]:
        """Map free text ("the cooling pump", "p101") to assets, best match first.

        An exact tag match wins outright; otherwise assets are ranked by how many
        words of the query appear in their name, type, location and model.
        """
        query = text.lower()
        compact = re.sub(r"[^a-z0-9]", "", query)
        for asset in self.assets.values():
            if re.sub(r"[^a-z0-9]", "", asset.id.lower()) in compact:
                return [asset]

        words = set(_WORD.findall(query)) - {"the", "a", "on", "of", "pump", "unit"}
        scored = []
        for asset in self.assets.values():
            haystack = set(_WORD.findall(
                f"{asset.name} {asset.type} {asset.location} {asset.manufacturer} {asset.model}".lower()
            ))
            score = len(words & haystack)
            if score:
                scored.append((score, asset))
        scored.sort(key=lambda s: -s[0])
        return [a for _, a in scored]

    def failure_modes_for(self, asset_id: str) -> list[FailureMode]:
        asset = self.assets[asset_id]
        return [self.failure_modes[c] for c in asset.failure_modes if c in self.failure_modes]

    def work_orders_for(self, asset_id: str, open_only: bool = False) -> list[WorkOrder]:
        return [
            w for w in self.work_orders
            if w.asset_id == asset_id and (not open_only or w.status != "closed")
        ]

    def spare_parts_for(self, asset_id: str) -> list[SparePart]:
        return [p for p in self.spare_parts if asset_id in p.compatible_assets]

    # ---- writes ---------------------------------------------------------

    def create_work_order(self, wo: WorkOrder, sandbox: bool = True) -> WorkOrder:
        """Add a work order.

        In sandbox mode it is kept in memory only (so later checks in the same
        session see it) and never written to disk.
        """
        if wo.asset_id not in self.assets:
            raise ValueError(f"Unknown asset {wo.asset_id!r}")
        if not wo.id:
            wo.id = new_work_order_id()
        self.work_orders.append(wo)
        if sandbox:
            return wo
        self._wo_path.write_text(json.dumps([w.to_dict() for w in self.work_orders], indent=2) + "\n")
        return wo


def new_work_order_id() -> str:
    return f"WO-AUTO-{uuid.uuid4().hex[:8].upper()}"


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []
