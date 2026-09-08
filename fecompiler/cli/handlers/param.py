from __future__ import annotations

import json
import shlex
from pathlib import Path

from fecompiler.application.workspace_service import workspace_application
from fecompiler.cli.core.inputs import (
    CommandInput,
    ParamDiffInput,
    ParamListInput,
    ParamSetInput,
    ParamShowInput,
    ParamUnsetInput,
)
from fecompiler.cli.core.records import disclosure_cmd, error_record
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.project.config import (
    ProjectConfig,
    ProjectConfigError,
    apply_project_overrides,
    ensure_project_config,
    load_project_config,
    restore_parameter_default,
    write_parameter_override,
)
from fecompiler.cli.project.params import (
    lookup_parameter,
    normalize_stored_value,
    parameter_records,
    parse_cli_value,
)
from fecompiler.cli.workspace_access import load_existing_workspace, read_json_object

_FLOW_STEPS = {"prepare", "review", "elab", "lint", "sim"}


def list_parameters(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ParamListInput)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [
                error_record(
                    "unknown_step",
                    step=command_input.step,
                    expected=sorted(_FLOW_STEPS),
                )
            ],
            exit_code=2,
        )
    loaded = _load(context)
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, config = loaded
    records = parameter_records(
        parameters,
        defaults=config.defaults if config else None,
        overrides=config.overrides if config else None,
        step=command_input.step,
    )
    if not command_input.all_parameters:
        records = [record for record in records if not record["advanced"]]
    return CommandResult.ok(records)


def show_parameter(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ParamShowInput)
    schema = lookup_parameter(command_input.key)
    if schema is None:
        return CommandResult.err(
            [error_record("unknown_parameter", param=command_input.key)]
        )
    loaded = _load(context)
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, config = loaded
    record = parameter_records(
        parameters,
        defaults=config.defaults if config else None,
        overrides=config.overrides if config else None,
    )
    selected = next(item for item in record if item["param"] == schema.param)
    selected.update(
        {
            "inspect_cmd": _target_cmd(
                f"ecc-fe param show {schema.param}", context
            ),
            "set_cmd": _target_cmd(
                f"ecc-fe param set {schema.param} <value>", context
            ),
        }
    )
    return CommandResult.ok([selected])


def set_parameter(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ParamSetInput)
    schema = lookup_parameter(command_input.key)
    if schema is None:
        return CommandResult.err(
            [error_record("unknown_parameter", param=command_input.key)]
        )
    try:
        value = parse_cli_value(command_input.value, schema)
    except ValueError as error:
        return CommandResult.err(
            [error_record("invalid_value", param=schema.param, reason=str(error))]
        )
    loaded = _load(context)
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, config = loaded
    previous = config.overrides.get(schema.param) if config else None
    current = parameters.get(schema.parameter_key)
    try:
        normalized_current = normalize_stored_value(current, schema)
    except (TypeError, ValueError):
        normalized_current = current
    reset = context.workspace_mode and bool(
        normalized_current != value or previous != value
    )
    if reset:
        failure = _reset_flow(context.workspace_dir)
        if failure is not None:
            return failure
    try:
        config = config or ensure_project_config(context.config_dir, parameters)
        config = write_parameter_override(config, schema.param, value)
        if context.workspace_mode:
            _, changed = apply_project_overrides(
                context.workspace_dir, config_directory=context.config_dir
            )
        else:
            changed = []
    except (OSError, TypeError, ValueError, ProjectConfigError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    return CommandResult.ok(
        [
            {
                "kind": "param_update",
                "param": schema.param,
                "value": value,
                "status": "set",
                "source": config.path.name,
                "flow_reset": reset,
                "parameters_changed": bool(changed),
            }
        ]
    )


def unset_parameter(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ParamUnsetInput)
    schema = lookup_parameter(command_input.key)
    if schema is None:
        return CommandResult.err(
            [error_record("unknown_parameter", param=command_input.key)]
        )
    loaded = _load(context)
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, config = loaded
    if config is None or schema.param not in config.overrides:
        return CommandResult.ok(
            [
                {
                    "kind": "param_update",
                    "param": schema.param,
                    "status": "no_override",
                    "source": "default",
                    "flow_reset": False,
                }
            ]
        )
    default = config.defaults.get(schema.param, schema.default)
    previous = config.overrides[schema.param]
    current = parameters.get(schema.parameter_key)
    try:
        normalized_current = normalize_stored_value(current, schema)
    except (TypeError, ValueError):
        normalized_current = current
    reset = context.workspace_mode and bool(
        previous != default or normalized_current != default
    )
    if reset:
        failure = _reset_flow(context.workspace_dir)
        if failure is not None:
            return failure
    try:
        changed = (
            restore_parameter_default(context.workspace_dir, config, schema.param)
            if context.workspace_mode
            else False
        )
        updated = write_parameter_override(config, schema.param, None, unset=True)
    except (OSError, TypeError, ValueError, ProjectConfigError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    return CommandResult.ok(
        [
            {
                "kind": "param_update",
                "param": schema.param,
                "value": updated.defaults.get(schema.param, schema.default),
                "status": "unset",
                "source": "default",
                "flow_reset": reset,
                "parameters_changed": changed,
            }
        ]
    )


def diff_parameters(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ParamDiffInput)
    loaded = _load(context)
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, config = loaded
    if config is None or not config.overrides:
        return CommandResult.ok([{"kind": "param_diff", "diff_status": "clean"}])
    records = parameter_records(
        parameters,
        defaults=config.defaults,
        overrides=config.overrides,
    )
    changed = [
        {
            "kind": "param_diff",
            "param": record["param"],
            "value": record["value"],
            "default": record["default"],
            "source": record["source"],
        }
        for record in records
        if record["param"] in config.overrides and record["value"] != record["default"]
    ]
    return (
        CommandResult.ok(changed)
        if changed
        else CommandResult.ok([{"kind": "param_diff", "diff_status": "clean"}])
    )


def _load(
    context: CommandContext,
) -> tuple[dict[str, object], dict[str, object], ProjectConfig | None] | CommandResult:
    try:
        workspace_dir = (
            context.workspace_dir
            if context.workspace_mode
            else str(context.template_dir)
        )
        workspace = load_existing_workspace(workspace_dir)
        if workspace is None:
            return CommandResult.err(
                [
                    error_record(
                        "Frontend workspace was not found",
                        workspace=workspace_dir,
                        remediation_cmd=_init_target_cmd(context),
                    )
                ]
            )
        parameters = read_json_object(Path(workspace["parameters_path"]))
        config = load_project_config(context.config_dir)
        return workspace, parameters, config
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
        )


def _reset_flow(directory: str) -> CommandResult | None:
    result = workspace_application.execute_payload(
        "reset-flow", {"directory": directory}, base_dir=Path.cwd()
    )
    if result.response == "success":
        return None
    return CommandResult.err(
        [
            error_record(
                "Unable to reset frontend flow after parameter change",
                workspace=directory,
                messages=result.message,
            )
        ]
    )


def _target_cmd(command: str, context: CommandContext) -> str:
    return disclosure_cmd(
        command,
        context.workspace_dir if context.workspace_mode else None,
        project=context.project,
    )


def _init_target_cmd(context: CommandContext) -> str:
    if context.workspace_mode:
        return disclosure_cmd("ecc-fe init", context.workspace_dir)
    target = context.project or context.project_dir
    return f"ecc-fe init {shlex.quote(target)}"
