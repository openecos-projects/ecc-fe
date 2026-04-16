"""Verilator sub-flow definitions."""

from __future__ import annotations

from enum import Enum
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.utility.json import json_write


class VerilatorSubFlowEnum(Enum):
    lint     = "lint"
    compile  = "compile"
    simulate = "simulate"
    report   = "report"


_SUBSTEPS = [
    VerilatorSubFlowEnum.lint,
    VerilatorSubFlowEnum.compile,
    VerilatorSubFlowEnum.simulate,
    VerilatorSubFlowEnum.report,
]


def build_verilator_subflow() -> list[dict[str, Any]]:
    return [
        {
            "name":             s.value,
            "state":            StateEnum.Unstart.value,
            "runtime":          "",
            "peak memory (mb)": 0,
            "info":             {},
        }
        for s in _SUBSTEPS
    ]


def init_verilator_subflow(workspace_step: Any) -> None:
    from fecompiler.utility.json import json_read
    path     = workspace_step.subflow.get("path", "")
    existing = json_read(path) if path else {}
    if existing.get("steps"):
        workspace_step.subflow["steps"] = existing["steps"]
    else:
        workspace_step.subflow["steps"] = build_verilator_subflow()
        if path:
            json_write(path, workspace_step.subflow)
