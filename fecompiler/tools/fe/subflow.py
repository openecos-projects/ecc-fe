"""Generic subflow helpers used by all step runners."""

from __future__ import annotations

from enum import Enum
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.utility.json import json_read, json_write


class EccSubFlowEnum(Enum):
    """Backward-compatible placeholder enum for removed legacy ECC subflows."""


def build_subflow(step_name: str) -> list[dict[str, Any]]:
    """Return default subflow template for a step.

    Legacy ECC subflow templates are removed; callers should initialize their own
    step-specific subflows (prepare/slang/verilator).
    """
    _ = step_name
    return []


def init_subflow(workspace_step: Any) -> None:
    """Populate workspace_step.subflow["steps"] from disk or empty defaults."""
    path = workspace_step.subflow.get("path", "")
    existing = json_read(path) if path else {}

    if existing.get("steps"):
        workspace_step.subflow["steps"] = existing["steps"]
    else:
        workspace_step.subflow["steps"] = build_subflow(workspace_step.name)
        if path:
            json_write(path, workspace_step.subflow)


def save_subflow(workspace_step: Any) -> bool:
    """Persist workspace_step.subflow to its JSON file."""
    path = workspace_step.subflow.get("path", "")
    if path:
        return json_write(path, workspace_step.subflow)
    return False


def reset_subflow(workspace_step: Any) -> None:
    """Reset all declared sub-steps before a new step execution."""
    for entry in workspace_step.subflow.get("steps", []):
        entry["state"] = StateEnum.Unstart.value
        entry["runtime"] = ""
        entry["peak memory (mb)"] = 0
        entry["info"] = {}
    saved = save_subflow(workspace_step)
    if not saved:
        return

    from fecompiler.runtime.subflow_events import publish_subflow_stage

    # Publish the complete skeleton before tools start completing stages. This
    # is required for a workspace's first run, when subflow.json was empty when
    # the GUI initially opened the step.
    for entry in workspace_step.subflow.get("steps", []):
        publish_subflow_stage(workspace_step, entry)


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
            entry["state"] = state_val
            entry["runtime"] = runtime
            entry["peak memory (mb)"] = peak_memory
            entry["info"] = info or {}
            saved = save_subflow(workspace_step)
            if saved:
                from fecompiler.runtime.subflow_events import publish_subflow_stage

                publish_subflow_stage(workspace_step, entry)
            return True
    return False


def update_substep_ok(
    workspace_step: Any,
    sub_step_name: str,
    ok: bool,
    *,
    info: dict[str, Any] | None = None,
) -> bool:
    """Convenience helper: map bool result to Success/Incomplete."""
    return update_substep(
        workspace_step,
        sub_step_name,
        StateEnum.Success if ok else StateEnum.Incomplete,
        info=info,
    )
