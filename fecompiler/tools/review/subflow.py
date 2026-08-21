"""RTL review sub-flow definitions."""

from __future__ import annotations

from enum import Enum
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.tools.fe.subflow import reset_subflow
from fecompiler.utility.json import json_read


class ReviewSubFlowEnum(Enum):
    collect_sources = "collect sources"
    scan_rtl = "scan rtl"
    analyze_quality = "analyze quality"
    report = "report"


def _template(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "state": StateEnum.Unstart.value,
        "runtime": "",
        "peak memory (mb)": 0,
        "info": {},
    }


def init_review_subflow(workspace_step: Any) -> None:
    path = workspace_step.subflow.get("path", "")
    existing = json_read(path) if path else {}
    if existing.get("steps"):
        workspace_step.subflow["steps"] = existing["steps"]
    else:
        workspace_step.subflow["steps"] = [
            _template(s.value) for s in ReviewSubFlowEnum
        ]
    reset_subflow(workspace_step)
