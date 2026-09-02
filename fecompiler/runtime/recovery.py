from __future__ import annotations

from pathlib import Path
from typing import Any

from fecompiler.analysis import clear_step_qor
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import load_workspace
from fecompiler.engine.flow import EngineFlow


def recover_interrupted_operations(
    directory: str | Path,
    *,
    active_operation_ids: set[str],
    operation_id: str = "",
) -> dict[str, list[dict[str, str]]]:
    workspace = load_workspace(str(directory))
    if workspace is None:
        raise FileNotFoundError(f"frontend workspace not found: {directory}")
    engine = EngineFlow(workspace)
    recovered: list[dict[str, str]] = []

    for step in engine.flow.get("steps", []):
        if step.get("state") != StateEnum.Ongoing.value:
            continue
        marker = _runtime_marker(step)
        marker_operation_id = str(marker.get("operation_id", "")) if marker else ""
        if operation_id and marker_operation_id != operation_id:
            continue
        if marker_operation_id in active_operation_ids:
            continue

        name = str(step.get("name", ""))
        tool = str(step.get("tool", ""))
        step["state"] = StateEnum.Incomplete.value
        step.setdefault("runtime", "")
        step.setdefault("peak memory (mb)", 0)
        info = step.get("info")
        if isinstance(info, dict):
            info.pop("runtime_operation", None)
        clear_step_qor(Path(directory) / f"{name}_{tool}")
        recovered.append(
            {
                "step": name,
                "tool": tool,
                "operationId": marker_operation_id or f"legacy-interrupted-{name}-{tool}",
                "logFile": str(Path(directory).resolve() / f"{name}_{tool}" / "log" / "log.txt"),
            }
        )

    if recovered:
        engine.save()
    return {"recovered": recovered}


def _runtime_marker(step: dict[str, Any]) -> dict[str, Any] | None:
    info = step.get("info")
    if not isinstance(info, dict):
        return None
    marker = info.get("runtime_operation")
    if not isinstance(marker, dict):
        return None
    if (
        marker.get("schema") != 1
        or not isinstance(marker.get("operation_id"), str)
        or not marker["operation_id"]
        or not isinstance(marker.get("runtime_instance_id"), str)
        or not marker["runtime_instance_id"]
        or not isinstance(marker.get("started_at"), (int, float))
    ):
        return None
    return marker
