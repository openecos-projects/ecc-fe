"""`ecc-fe runs`: list and delete the runs of a project."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fecompiler.cli.core.inputs import CommandInput, RunsDeleteInput
from fecompiler.cli.core.records import (
    FAILED_STATES,
    disclosure_cmd,
    error_record,
    failure_record,
    flow_state,
    normalize_state,
)
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.project.config import (
    ProjectConfigError,
    load_run_parameter_overrides,
    project_config_path,
)
from fecompiler.cli.project.layout import (
    RUN_ID_RULE,
    is_safe_run_directory,
    is_valid_run_id,
    resolves_as_spelled,
    template_dir,
)
from fecompiler.cli.workspace_access import load_existing_workspace, read_json_object
from fecompiler.utility.workspace_lock import (
    WorkspaceBusyError,
    discard_workspace_lock,
    workspace_lock,
    workspace_lock_owner,
    workspace_lock_path,
)

_RETIRED_MARK = ".deleted-"


def list_runs(command_input: CommandInput, context: CommandContext) -> CommandResult:
    refused = _require_project(context)
    if refused is not None:
        return refused
    selected = context.run_id or "default"
    runs = [_run_record(path, context, selected) for path in _run_directories(context)]
    summary: dict[str, object] = {
        "kind": "run_list",
        "project": context.project_dir,
        "count": len(runs),
        "selected": selected,
    }
    if not runs:
        summary["next_cmd"] = disclosure_cmd("ecc-fe run", project=context.project)
    return CommandResult.ok([summary, *runs])


def delete_runs(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, RunsDeleteInput)
    refused = _require_project(context)
    if refused is not None:
        return refused
    run_ids = list(dict.fromkeys(command_input.run_ids))
    invalid = [run_id for run_id in run_ids if not is_valid_run_id(run_id)]
    if invalid or not run_ids:
        return CommandResult.err(
            [
                error_record("invalid_run_id", run=run_id, reason=RUN_ID_RULE)
                for run_id in invalid
            ]
            or [error_record("run_id_required")],
            exit_code=2,
        )
    records = [_delete_run(run_id, context) for run_id in run_ids]
    _discard_orphan_locks(Path(context.project_dir) / "runs")
    failed = any(record.get("kind") == "error" for record in records)
    return CommandResult.err(records) if failed else CommandResult.ok(records)


def _require_project(context: CommandContext) -> CommandResult | None:
    project = Path(context.project_dir)
    if (
        not context.workspace_mode
        and project_config_path(context.project_dir).is_file()
        and Path(template_dir(context.project_dir)).is_dir()
    ):
        return None
    return CommandResult.err(
        [
            error_record(
                "project_not_found",
                project=str(project),
                reason="runs belong to a project created by `ecc-fe init <name>`",
            )
        ]
    )


def _run_directories(context: CommandContext) -> list[Path]:
    runs = Path(context.project_dir) / "runs"
    if not runs.is_dir():
        return []
    return sorted(
        path
        for path in runs.iterdir()
        if is_valid_run_id(path.name) and path.is_dir() and not path.is_symlink()
    )


def _run_record(path: Path, context: CommandContext, selected: str) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": "run",
        "run": path.name,
        "selected": path.name == selected,
        "workspace": str(path),
    }
    try:
        workspace = load_existing_workspace(str(path))
        flow = read_json_object(Path(workspace["flow_path"])) if workspace else None
    except (OSError, TypeError, ValueError) as error:
        return {**record, "state": "unreadable", "message": str(error)}
    if flow is None:
        return {**record, "state": "unreadable", "message": "not a frontend workspace"}
    owner = workspace_lock_owner(str(path))
    steps = [step for step in flow.get("steps", []) if isinstance(step, dict)]
    states = [normalize_state(step.get("state")) for step in steps]
    if owner is None:
        states = ["interrupted" if state == "ongoing" else state for state in states]
    record["state"] = flow_state(states, running=owner is not None)
    record["failed_step"] = next(
        (
            str(step.get("name", ""))
            for step, state in zip(steps, states)
            if state in FAILED_STATES
        ),
        None,
    )
    record["steps_done"] = sum(state == "success" for state in states)
    record["steps_total"] = len(states)
    record["modified"] = _modified(Path(workspace["flow_path"]))
    try:
        record["overrides"] = load_run_parameter_overrides(str(path))
    except (OSError, TypeError, ValueError, ProjectConfigError):
        record["overrides"] = {}
    record["status_cmd"] = disclosure_cmd(
        "ecc-fe status", project=context.project, run_id=path.name
    )
    return record


def _modified(path: Path) -> str | None:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    return (
        datetime.fromtimestamp(stamp, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _delete_run(run_id: str, context: CommandContext) -> dict[str, object]:
    run_dir = Path(context.project_dir) / "runs" / run_id
    if not run_dir.exists() and not run_dir.is_symlink():
        return error_record("run_not_found", run=run_id, workspace=str(run_dir))
    # Only a directory that is a run, reached without a symlink, is removed.
    if not is_safe_run_directory(str(run_dir)) or not resolves_as_spelled(
        str(run_dir), context.project_dir
    ):
        return error_record(
            "run_path_refused",
            run=run_id,
            workspace=str(run_dir),
            reason="not a plain run directory of this project",
        )
    try:
        with workspace_lock(run_dir):
            # Renaming first makes the run disappear at once, so an
            # interrupted delete never leaves half a run under its own name.
            retired = run_dir.with_name(
                f".{run_dir.name}{_RETIRED_MARK}{uuid.uuid4().hex[:8]}"
            )
            os.rename(run_dir, retired)
            shutil.rmtree(retired, ignore_errors=True)
            workspace_lock_path(run_dir).unlink(missing_ok=True)
    except WorkspaceBusyError as error:
        return error_record(
            "workspace_busy",
            run=run_id,
            workspace=str(run_dir),
            owner_pid=error.owner.get("pid"),
        )
    except OSError as error:
        return failure_record(
            "run_delete_failed", error, run=run_id, workspace=str(run_dir)
        )
    return {"kind": "run_deleted", "run": run_id, "workspace": str(run_dir)}


def _discard_orphan_locks(runs: Path) -> None:
    """Remove lock files and delete leftovers whose run no longer exists."""
    if not runs.is_dir():
        return
    for path in runs.iterdir():
        name = path.name
        if name.startswith(".") and name.endswith(".lock") and path.is_file():
            discard_workspace_lock(runs / name[1 : -len(".lock")])
        elif name.startswith(".") and _RETIRED_MARK in name and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
