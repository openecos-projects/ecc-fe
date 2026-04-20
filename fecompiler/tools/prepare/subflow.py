"""Prepare sub-flow definitions."""

from __future__ import annotations

from enum import Enum
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.utility.json import json_read, json_write


class PrepareSubFlowEnum(Enum):
    collect_inputs = "collect inputs"
    merge_filelist = "merge filelist"
    persist_state  = "persist state"
    report         = "report"


def _template(name: str) -> dict[str, Any]:
    return {
        "name":             name,
        "state":            StateEnum.Unstart.value,
        "runtime":          "",
        "peak memory (mb)": 0,
        "info":             {},
    }


def init_prepare_subflow(workspace_step: Any) -> None:
    path = workspace_step.subflow.get("path", "")
    existing = json_read(path) if path else {}
    if existing.get("steps"):
        workspace_step.subflow["steps"] = existing["steps"]
    else:
        workspace_step.subflow["steps"] = [
            _template(s.value) for s in PrepareSubFlowEnum
        ]
        if path:
            json_write(path, workspace_step.subflow)
