"""ECC sub-flow definitions — mirrors chipcompiler/tools/ecc/subflow.py in ecos-studio/ecc.

Each top-level flow step (floorplan, placement, …) is broken into a sequence of
named sub-steps stored in the step's subflow.json.  The sub-step list is what
the front-end renders as the progress breakdown inside a single step.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fecompiler.data.step import StateEnum, StepEnum
from fecompiler.utility.json import json_read, json_write


# ── sub-step name catalogue ────────────────────────────────────────────────────

class EccSubFlowEnum(Enum):
    """All possible sub-step names across all main steps."""

    load_data            = "load data"
    save_data            = "save data"
    analysis             = "analysis"
    init_floorplan       = "init floorplan"
    create_tracks        = "create tracks"
    place_io_pins        = "place io pins"
    tap_cell             = "tap cell"
    PDN                  = "PDN"
    set_clock_net        = "set clock net"
    run_net_optimization = "run net optimization"
    run_placement        = "run placement"
    run_CTS              = "run CTS"
    run_timing_opt_drv   = "run timing opt drv"
    run_timing_opt_hold  = "run timing opt hold"
    run_legalization     = "run legalization"
    run_routing          = "run routing"
    run_filler           = "run filler"
    run_DRC              = "run DRC"


# ── mapping: main step → ordered sub-step list ────────────────────────────────

_E = EccSubFlowEnum

_SUBFLOW_MAP: dict[StepEnum, list[EccSubFlowEnum]] = {
    StepEnum.STEP1: [
        _E.load_data, _E.init_floorplan, _E.create_tracks,
        _E.place_io_pins, _E.tap_cell, _E.PDN, _E.set_clock_net,
        _E.save_data, _E.analysis,
    ],
    StepEnum.STEP2: [
        _E.load_data, _E.run_net_optimization, _E.save_data, _E.analysis,
    ],
    StepEnum.STEP3: [
        _E.load_data, _E.run_placement, _E.save_data, _E.analysis,
    ],
    StepEnum.STEP4: [
        _E.load_data, _E.run_CTS, _E.save_data, _E.analysis,
    ],
    StepEnum.STEP5: [
        _E.load_data, _E.run_legalization, _E.save_data, _E.analysis,
    ],
    StepEnum.STEP6: [
        _E.load_data, _E.run_routing, _E.save_data, _E.analysis,
    ],
    StepEnum.STEP7: [
        _E.load_data, _E.run_DRC, _E.save_data,
    ],
}


# ── helpers ────────────────────────────────────────────────────────────────────

def _subflow_template(sub_step_name: str) -> dict[str, Any]:
    return {
        "name":               sub_step_name,
        "state":              StateEnum.Unstart.value,
        "runtime":            "",
        "peak memory (mb)":   0,
        "info":               {},
    }


def build_subflow(step_name: str) -> list[dict[str, Any]]:
    """Return the ordered sub-step list for *step_name*.

    Falls back to an empty list for unknown step names so callers
    never crash on an unrecognised step.
    """
    try:
        step_enum = StepEnum(step_name)
    except ValueError:
        return []
    sub_steps = _SUBFLOW_MAP.get(step_enum, [])
    return [_subflow_template(s.value) for s in sub_steps]


def init_subflow(workspace_step: Any) -> None:
    """Populate workspace_step.subflow["steps"] from disk or from defaults.

    Mirrors EccSubFlow.init_sub_flow() in ecc.
    If subflow.json already has content it is reused; otherwise the default
    sub-step list for the step is generated and written to disk.
    """
    path = workspace_step.subflow.get("path", "")
    existing = json_read(path) if path else {}

    if existing.get("steps"):
        workspace_step.subflow["steps"] = existing["steps"]
    else:
        steps = build_subflow(workspace_step.name)
        workspace_step.subflow["steps"] = steps
        if path:
            json_write(path, workspace_step.subflow)


def save_subflow(workspace_step: Any) -> None:
    """Persist workspace_step.subflow to its JSON file."""
    path = workspace_step.subflow.get("path", "")
    if path:
        json_write(path, workspace_step.subflow)


def update_substep(
    workspace_step: Any,
    sub_step_name: str,
    state: StateEnum | str,
    runtime: str = "",
    peak_memory: float = 0.0,
    info: dict[str, Any] | None = None,
) -> bool:
    """Update a single sub-step's state and persist.

    Returns True if the sub-step was found, False otherwise.
    """
    state_val = state.value if isinstance(state, StateEnum) else state
    for entry in workspace_step.subflow.get("steps", []):
        if entry.get("name") == sub_step_name:
            entry["state"]             = state_val
            entry["runtime"]           = runtime
            entry["peak memory (mb)"]  = peak_memory
            entry["info"]              = info or {}
            save_subflow(workspace_step)
            return True
    return False
