from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from fecompiler.allflow.steps import step_directory, step_names
from fecompiler.analysis.ci_export import exported_diagnostics, junit_xml, sarif_log
from fecompiler.cli.core.inputs import (
    CommandInput,
    ReportCompareInput,
    ReportExportInput,
    ReportFilesInput,
    ReportLintBaselineInput,
    ReportQorInput,
)
from fecompiler.tools.verilator.lint import BASELINE_SCHEMA_VERSION, lint_baseline_entries
from fecompiler.cli.project.layout import RUN_ID_RULE, is_valid_run_id, resolve_run_dir
from fecompiler.cli.core.records import error_record, failure_record, normalize_state
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.workspace_access import load_existing_workspace, read_json_object
from fecompiler.utility.program import invoked

_FLOW_STEPS = step_names()
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
                remediation_cmd=invoked("ecc-fe run"),
            )
        ]
    )


def compare(command_input: CommandInput, context: CommandContext) -> CommandResult:
    """Compare the QoR of two runs of one project, step by step."""
    assert isinstance(command_input, ReportCompareInput)
    if context.workspace_mode:
        return CommandResult.err(
            [
                error_record(
                    "project_not_found",
                    project=context.project_dir,
                    reason="runs are compared inside a project",
                )
            ]
        )
    names = (command_input.base, command_input.candidate)
    invalid = [name for name in names if not is_valid_run_id(name)]
    if invalid:
        return CommandResult.err(
            [error_record("invalid_run_id", run=name, reason=RUN_ID_RULE) for name in invalid],
            exit_code=2,
        )
    sides: list[_RunQor] = []
    for name in names:
        loaded = _load_run_qor(context.project_dir, name)
        if isinstance(loaded, CommandResult):
            return loaded
        sides.append(loaded)
    base, candidate = sides
    step_records: list[dict[str, object]] = []
    metric_records: list[dict[str, object]] = []
    for step in _FLOW_STEPS:
        if step not in base.states and step not in candidate.states:
            continue
        base_qor, run_qor = base.qor.get(step), candidate.qor.get(step)
        base_score, run_score = _score(base_qor), _score(run_qor)
        base_inputs, run_inputs = _comparison_key(base_qor), _comparison_key(run_qor)
        step_records.append(
            {
                "kind": "qor_step",
                "step": step,
                "base_state": base.states.get(step),
                "run_state": candidate.states.get(step),
                "base_quality": _quality(base_qor),
                "run_quality": _quality(run_qor),
                "base_score": base_score,
                "run_score": run_score,
                # Scores of different scoring versions are on different scales.
                "score_delta": (
                    _delta(base_score, run_score)
                    if _scoring_version(base_qor) == _scoring_version(run_qor)
                    else None
                ),
                # False when the two results were produced from different
                # inputs or configuration, so their numbers measure different things.
                "same_inputs": (
                    base_inputs == run_inputs
                    if base_inputs is not None and run_inputs is not None
                    else None
                ),
            }
        )
        metric_records.extend(
            _metric_records(step, base_qor, run_qor, command_input.all_metrics)
        )
    verdicts = [str(record["verdict"]) for record in metric_records]
    regressed = verdicts.count("regressed") + sum(
        _state_rank(record["run_state"]) < _state_rank(record["base_state"])
        for record in step_records
    )
    improved = verdicts.count("improved")
    changed = sum(verdict != "same" for verdict in verdicts)
    summary: dict[str, object] = {
        "kind": "qor_compare",
        "project": context.project_dir,
        "base": base.name,
        "run": candidate.name,
        "status": "regressed" if regressed else "improved" if improved else "changed" if changed else "same",
        "regressed": regressed,
        "improved": improved,
        "changed_metrics": changed,
        "parameter_changes": _parameter_changes(base.parameters, candidate.parameters),
    }
    records = [summary, *step_records, *metric_records, *base.errors, *candidate.errors]
    failed = bool(base.errors or candidate.errors) or (
        command_input.fail_on_regression and regressed > 0
    )
    return CommandResult.err(records) if failed else CommandResult.ok(records)


@dataclass(frozen=True, slots=True)
class _RunQor:
    name: str
    states: dict[str, str]
    qor: dict[str, dict[str, object]]
    parameters: dict[str, object]
    errors: list[dict[str, object]]


def _load_run_qor(project_dir: str, name: str) -> _RunQor | CommandResult:
    directory, _ = resolve_run_dir(project_dir, name)
    try:
        workspace = load_existing_workspace(directory)
        if workspace is None:
            return CommandResult.err(
                [error_record("run_not_found", run=name, workspace=directory)]
            )
        flow = read_json_object(Path(workspace["flow_path"]))
        parameters = read_json_object(Path(workspace["parameters_path"]))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, run=name, workspace=directory)]
        )
    states: dict[str, str] = {}
    qor: dict[str, dict[str, object]] = {}
    errors: list[dict[str, object]] = []
    for raw_step in flow.get("steps", []):
        if not isinstance(raw_step, dict) or raw_step.get("name") not in _FLOW_STEPS:
            continue
        step = str(raw_step["name"])
        states[step] = normalize_state(raw_step.get("state"))
        artifacts, error = _read_qor_artifacts(Path(directory), step)
        if error is not None:
            errors.append({**error, "run": name})
        elif artifacts is not None:
            qor[step] = artifacts
    return _RunQor(name, states, qor, parameters, errors)


def _summary(qor: dict[str, object] | None) -> dict[str, object]:
    summary = qor.get("summary") if isinstance(qor, dict) else None
    return summary if isinstance(summary, dict) else {}


def _quality(qor: dict[str, object] | None) -> object:
    return _summary(qor).get("quality_status")


def _score(qor: dict[str, object] | None) -> object:
    score = _summary(qor).get("score")
    return score.get("value") if isinstance(score, dict) else None


def _comparison_key(qor: dict[str, object] | None) -> object:
    context = _summary(qor).get("context")
    comparison = context.get("comparison") if isinstance(context, dict) else None
    return comparison.get("fingerprint") if isinstance(comparison, dict) else None


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _scoring_version(qor: dict[str, object] | None) -> object:
    score = _summary(qor).get("score")
    return score.get("scoring_version") if isinstance(score, dict) else None


def _delta(base: object, run: object) -> object:
    if _is_number(base) and _is_number(run):
        return round(run - base, 6)  # type: ignore[operator]
    return None


_STATE_RANK = {"success": 2, "unstart": 1}


def _state_rank(state: object) -> int:
    # A lower rank than in the base run is a step regression.
    return _STATE_RANK.get(str(state), 0 if state in {"incomplete", "invalid"} else 1)


def _metrics(qor: dict[str, object] | None) -> dict[str, dict[str, object]]:
    payload = qor.get("metrics") if isinstance(qor, dict) else None
    entries = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}
    return {
        str(entry["id"]): entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    }


def _metric_records(
    step: str,
    base_qor: dict[str, object] | None,
    run_qor: dict[str, object] | None,
    all_metrics: bool,
) -> list[dict[str, object]]:
    base_metrics, run_metrics = _metrics(base_qor), _metrics(run_qor)
    records: list[dict[str, object]] = []
    for metric in dict.fromkeys([*base_metrics, *run_metrics]):
        base_entry = base_metrics.get(metric, {})
        run_entry = run_metrics.get(metric, {})
        base_value, run_value = base_entry.get("value"), run_entry.get("value")
        direction = str((run_entry or base_entry).get("direction", "trend_only"))
        verdict = _verdict(base_value, run_value, direction)
        if verdict == "same" and not all_metrics:
            continue
        records.append(
            {
                "kind": "qor_metric",
                "step": step,
                "metric": metric,
                "unit": (run_entry or base_entry).get("unit"),
                "direction": direction,
                "base": base_value,
                "run": run_value,
                "delta": _delta(base_value, run_value),
                "verdict": verdict,
            }
        )
    return records


def _verdict(base: object, run: object, direction: str) -> str:
    if base == run:
        return "same"
    if not (_is_number(base) and _is_number(run)):
        return "changed"
    higher = run > base  # type: ignore[operator]
    if direction == "higher_is_better":
        return "improved" if higher else "regressed"
    if direction == "lower_is_better":
        return "regressed" if higher else "improved"
    return "changed"


def _parameter_changes(
    base: dict[str, object], run: dict[str, object]
) -> dict[str, list[object]]:
    return {
        key: [base.get(key), run.get(key)]
        for key in sorted({*base, *run})
        if base.get(key) != run.get(key) and not _is_run_path(base.get(key), run.get(key))
    }


def _is_run_path(base: object, run: object) -> bool:
    # Each run records its own directory; that difference is not a setting.
    return (
        isinstance(base, str)
        and isinstance(run, str)
        and os.sep in base
        and os.sep in run
        and os.path.basename(base) == os.path.basename(run)
    )


def lint_baseline(command_input: CommandInput, context: CommandContext) -> CommandResult:
    """Write the lint warnings of a run as a baseline file."""
    assert isinstance(command_input, ReportLintBaselineInput)
    loaded = _load_workspace(context.workspace_dir)
    if isinstance(loaded, CommandResult):
        return loaded
    summary_path = (
        _report_directory(Path(context.workspace_dir), "lint") / "lint_summary.json"
    )
    try:
        summary = read_json_object(summary_path)
        diagnostics = summary["diagnostics"]
        if not isinstance(diagnostics, list):
            raise TypeError("diagnostics must be a list")
    except (OSError, TypeError, ValueError, KeyError) as error:
        return CommandResult.err(
            [
                failure_record(
                    "lint_result_not_found",
                    error,
                    workspace=context.workspace_dir,
                    remediation_cmd=invoked("ecc-fe run --to lint"),
                )
            ]
        )
    entries = lint_baseline_entries(
        [item for item in diagnostics if isinstance(item, dict)]
    )
    base = Path(context.project_dir if not context.workspace_mode else context.workspace_dir)
    destination = Path(command_input.output_path or "lint-baseline.json").expanduser()
    if not destination.is_absolute():
        destination = (Path.cwd() / destination).resolve()
    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "tool": "verilator",
        "top_module": summary.get("top_module", ""),
        "entries": entries,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    destination.write_text(content, encoding="utf-8")
    # Inside the project the setting is written relative, so it moves with it.
    try:
        setting = str(destination.relative_to(base.resolve()))
    except ValueError:
        setting = str(destination)
    return CommandResult.ok(
        [
            {
                "kind": "lint_baseline",
                "path": str(destination),
                "warnings": sum(int(entry["count"]) for entry in entries),
                "entries": len(entries),
                "set_cmd": invoked("ecc-fe param set lint.baseline ") + shlex.quote(setting),
            }
        ]
    )


# What each export format is made from, and the file it is written to.
EXPORT_FORMATS = {
    "sarif": ("lint", "lint.sarif"),
    "junit": ("sim", "sim.junit.xml"),
}


def export(command_input: CommandInput, context: CommandContext) -> CommandResult:
    """Write lint diagnostics as SARIF, or simulation cases as JUnit XML."""
    assert isinstance(command_input, ReportExportInput)
    selected = EXPORT_FORMATS.get(command_input.export_format)
    if selected is None:
        return CommandResult.err(
            [
                error_record(
                    "unknown_export_format",
                    format=command_input.export_format,
                    expected=list(EXPORT_FORMATS),
                )
            ],
            exit_code=2,
        )
    step, default_name = selected
    loaded = _load_workspace(context.workspace_dir)
    if isinstance(loaded, CommandResult):
        return loaded
    workspace, flow = loaded
    report_dir = _report_directory(Path(context.workspace_dir), step)
    properties = {
        "design": workspace.get("design", ""),
        "top_module": workspace.get("top_module", ""),
        "run": None if context.workspace_mode else (context.run_id or "default"),
    }
    record: dict[str, object] = {
        "kind": "report_export",
        "format": command_input.export_format,
        "step": step,
        "state": _step_state(flow, step),
    }
    try:
        if step == "lint":
            summary = read_json_object(report_dir / "lint_summary.json")
            diagnostics = summary["diagnostics"]
            if not isinstance(diagnostics, list):
                raise TypeError("diagnostics must be a list")
            listed = [item for item in diagnostics if isinstance(item, dict)]
            carried = exported_diagnostics(listed, everything=command_input.everything)
            base_dir = Path(command_input.base_dir or Path.cwd()).expanduser()
            content = (
                json.dumps(
                    sarif_log(carried, base_dir=base_dir, properties=properties),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
            record.update(
                {
                    "results": len(carried),
                    "errors": sum(item.get("severity") == "error" for item in carried),
                    "warnings": sum(item.get("severity") == "warning" for item in carried),
                    # Warnings in sources the user does not own; --all carries them.
                    "left_out": len(listed) - len(carried),
                    "base_dir": os.path.abspath(base_dir),
                }
            )
        else:
            payload = read_json_object(report_dir / "cases.json")
            cases = payload.get("cases")
            if not isinstance(cases, list):
                raise TypeError("cases must be a list")
            content, counts = junit_xml(
                [case for case in cases if isinstance(case, dict)],
                suite=str(payload.get("suite") or "sim"),
                design=str(workspace.get("design") or ""),
                properties=properties,
                step_error=_step_error(record["state"], step),
            )
            record.update(counts)
    except (OSError, TypeError, ValueError, KeyError) as error:
        return CommandResult.err(
            [
                failure_record(
                    f"{step}_result_not_found",
                    error,
                    workspace=context.workspace_dir,
                    remediation_cmd=invoked(f"ecc-fe run --to {step}"),
                )
            ]
        )
    destination = Path(command_input.output_path or default_name).expanduser()
    if not destination.is_absolute():
        destination = (Path.cwd() / destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    record["path"] = str(destination)
    record["bytes"] = len(content.encode("utf-8"))
    return CommandResult.ok([record])


def _step_state(flow: dict[str, object], step: str) -> str:
    steps = flow.get("steps")
    for raw in steps if isinstance(steps, list) else []:
        if isinstance(raw, dict) and raw.get("name") == step:
            return normalize_state(raw.get("state"))
    return "unknown"


def _step_error(state: object, step: str) -> str | None:
    """Why a report without a failing case is still not a pass."""
    if state in {"incomplete", "invalid"}:
        return f"the {step} step failed; see `{invoked(f'ecc-fe log --step {step}')}`"
    return None


def _load_workspace(
    directory: str,
) -> tuple[dict[str, object], dict[str, object]] | CommandResult:
    try:
        workspace = load_existing_workspace(directory)
        if workspace is None:
            return CommandResult.err(
                [error_record("workspace_not_found", workspace=directory)]
            )
        flow = read_json_object(Path(workspace["flow_path"]))
        return workspace, flow
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return CommandResult.err([failure_record("workspace_unreadable", error, workspace=directory)])


def _report_directory(workspace: Path, step: str) -> Path:
    return _step_directory(workspace, step) / "report"


def _result_directories(workspace: Path, step: str) -> tuple[tuple[str, Path], ...]:
    root = _step_directory(workspace, step)
    return (("report", root / "report"), ("analysis", root / "analysis"))


def _step_directory(workspace: Path, step: str) -> Path:
    return step_directory(workspace, step)


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
