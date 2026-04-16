"""Verilator sub-flow definitions — one per tool step (lint / sim)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.utility.json import json_read, json_write


# ── lint sub-steps ─────────────────────────────────────────────────────────────

class LintSubFlowEnum(Enum):
    lint   = "lint"
    report = "report"


# ── sim sub-steps ──────────────────────────────────────────────────────────────

class SimSubFlowEnum(Enum):
    compile  = "compile"
    simulate = "simulate"
    report   = "report"


# ── helpers ────────────────────────────────────────────────────────────────────

def _template(name: str) -> dict[str, Any]:
    return {
        "name":             name,
        "state":            StateEnum.Unstart.value,
        "runtime":          "",
        "peak memory (mb)": 0,
        "info":             {},
    }


def _init(workspace_step: Any, sub_steps: list[Enum]) -> None:
    path     = workspace_step.subflow.get("path", "")
    existing = json_read(path) if path else {}
    if existing.get("steps"):
        workspace_step.subflow["steps"] = existing["steps"]
    else:
        workspace_step.subflow["steps"] = [_template(s.value) for s in sub_steps]
        if path:
            json_write(path, workspace_step.subflow)


def init_lint_subflow(workspace_step: Any) -> None:
    _init(workspace_step, list(LintSubFlowEnum))


def init_sim_subflow(workspace_step: Any) -> None:
    _init(workspace_step, list(SimSubFlowEnum))
