"""pdm-agent: an AI agent for predictive maintenance."""

from .domain import Alarm, Asset, FailureMode, SparePart, WorkOrder
from .plant import Plant

__all__ = ["Alarm", "Asset", "FailureMode", "Plant", "SparePart", "WorkOrder"]
__version__ = "0.1.0"
