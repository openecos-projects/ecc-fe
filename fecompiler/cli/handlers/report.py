from __future__ import annotations

import json
import shlex
from pathlib import Path

from fecompiler.cli.core.inputs import CommandInput, ReportFilesInput, ReportQorInput
from fecompiler.cli.core.records import error_record, normalize_state
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.workspace_access import load_existing_workspace, read_json_object

_FLOW_STEPS = ("prepare", "review", "elab", "lint", "sim")
_QOR_ARTIFACTS = {
    "metrics": ("qor_metrics.json", 3),
    "summary": ("qor_summary.json", 4),
    "hotspots": ("qor_hotspots.json", 3),
}


def qor(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, ReportQorInput)
    loaded = _load_workspace(context.workspace_dir)
    if isinstance(loaded, CommandResult):
        return loaded
    workspace, flow = loaded
    steps: dict[str, str] = {}
    summaries: dict[str, object] = {}
    details: dict[str, str] = {}
    qor_steps: dict[str, object] = {}
    malformed: list[dict[str, object]] = []
    for raw_step in flow.get("steps", []):
        if not isinstance(raw_step, dict):
            continue
        name = str(raw_step.get("name", ""))
        if name not in _FLOW_STEPS:
            continue
        steps[name] = normalize_state(raw_step.get("state"))
        detail_path = (
            _report_directory(Path(context.workspace_dir), name)
            / "frontend_detail.json"
        )
        if not detail_path.is_file():
            detail = None
        else:
            try:
                detail = read_json_object(detail_path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                malformed.append(
                    error_record(
                        "invalid_report",
                        step=name,
                        path=str(detail_path),
                        reason=str(error),
                    )
                )
                detail = None
        if detail is not None:
            summary = detail.get("summary", {})
            summaries[name] = summary if isinstance(summary, dict) else {}
            details[name] = str(detail_path)
        qor, qor_error = _read_qor_artifacts(Path(context.workspace_dir), name)
        if qor_error is not None:
            malformed.append(qor_error)
        elif qor is not None:
            qor_steps[name] = qor
    states = list(steps.values())
    if any(state in {"incomplete", "invalid"} for state in states):
        status = "failed"
    elif states and all(state == "success" for state in states):
        status = "complete"
    else:
        status = "partial"
    record: dict[str, object] = {
        "kind": "frontend_qor",
        "report": "qor",
        "status": status,
        "workspace": context.workspace_dir,
        "design": workspace.get("design", ""),
        "top_module": workspace.get("top_module", ""),
        "steps": steps,
        "summaries": summaries,
        "detail_paths": details,
        "generated_steps": len(details),
        "qor": qor_steps,
        "qor_generated_steps": len(qor_steps),
        "total_steps": len(steps),
    }
    if command_input.output_path:
        destination = Path(command_input.output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        destination.write_text(content, encoding="utf-8")
        record["path"] = str(destination)
        record["bytes"] = len(content.encode("utf-8"))
        record["view_cmd"] = f"cat {shlex.quote(str(destination))}"
    records = [record, *malformed]
    return (
        CommandResult.err(records)
        if malformed or status == "failed"
        else CommandResult.ok(records)
    )


def files(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, ReportFilesInput)
    loaded = _load_workspace(context.workspace_dir)
    if isinstance(loaded, CommandResult):
        return loaded
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [
                error_record(
                    "unknown_step", step=command_input.step, expected=list(_FLOW_STEPS)
                )
            ],
            exit_code=2,
        )
    selected = [command_input.step] if command_input.step else list(_FLOW_STEPS)
    records: list[dict[str, object]] = []
    for step in selected:
        for section, directory in _result_directories(
            Path(context.workspace_dir), step
        ):
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                records.append(
                    {
                        "kind": "report_file",
                        "step": step,
                        "section": section,
                        "path": str(path.resolve()),
                        "relative_path": str(path.relative_to(context.workspace_dir)),
                        "bytes": path.stat().st_size,
                        "format": path.suffix.removeprefix(".") or "text",
                        "view_cmd": f"cat {shlex.quote(str(path.resolve()))}",
                    }
                )
    if records:
        return CommandResult.ok(records)
    return CommandResult.err(
        [
            error_record(
                "report_files_not_found",
                workspace=context.workspace_dir,
                step=command_input.step,
                remediation_cmd="ecc-fe run",
            )
        ]
    )


def _load_workspace(
    directory: str,
) -> tuple[dict[str, object], dict[str, object]] | CommandResult:
    try:
        workspace = load_existing_workspace(directory)
        if workspace is None:
            return CommandResult.err(
                [error_record("Frontend workspace was not found", workspace=directory)]
            )
        flow = read_json_object(Path(workspace["flow_path"]))
        return workspace, flow
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return CommandResult.err([error_record(str(error), workspace=directory)])


def _report_directory(workspace: Path, step: str) -> Path:
    return _step_directory(workspace, step) / "report"


def _result_directories(workspace: Path, step: str) -> tuple[tuple[str, Path], ...]:
    root = _step_directory(workspace, step)
    return (("report", root / "report"), ("analysis", root / "analysis"))


def _step_directory(workspace: Path, step: str) -> Path:
    tool = {
        "prepare": "fe",
        "review": "fe",
        "elab": "slang",
        "lint": "verilator",
        "sim": "verilator",
    }[step]
    return workspace / f"{step}_{tool}"


def _read_qor_artifacts(
    workspace: Path, step: str
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    analysis_dir = _step_directory(workspace, step) / "analysis"
    paths = {
        name: analysis_dir / filename for name, (filename, _) in _QOR_ARTIFACTS.items()
    }
    present = {name for name, path in paths.items() if path.is_file()}
    if not present:
        return None, None
    if present != set(paths):
        return None, error_record(
            "incomplete_qor_artifacts",
            step=step,
            path=str(analysis_dir),
            missing=sorted(set(paths) - present),
        )
    payloads: dict[str, dict[str, object]] = {}
    try:
        for name, path in paths.items():
            payloads[name] = read_json_object(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return None, error_record(
            "invalid_qor_artifact",
            step=step,
            path=str(analysis_dir),
            reason=str(error),
        )
    for name, (_, expected_schema) in _QOR_ARTIFACTS.items():
        if payloads[name].get("schema_version") != expected_schema:
            return None, error_record(
                "invalid_qor_schema",
                step=step,
                artifact=name,
                expected=expected_schema,
                actual=payloads[name].get("schema_version"),
            )
    generations = {str(payload.get("generation", "")) for payload in payloads.values()}
    if len(generations) != 1 or not next(iter(generations)):
        return None, error_record(
            "qor_generation_mismatch",
            step=step,
            path=str(analysis_dir),
        )
    return {
        **payloads,
        "paths": {name: str(path) for name, path in paths.items()},
    }, None
