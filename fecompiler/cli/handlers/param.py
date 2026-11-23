from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from pathlib import Path

from fecompiler.allflow.steps import step_names
from fecompiler.application.workspace_service import workspace_application
from fecompiler.cli.core.inputs import (
    CommandInput,
    ParamDiffInput,
    ParamListInput,
    ParamSetInput,
    ParamShowInput,
    ParamUnsetInput,
)
from fecompiler.cli.core.locking import with_workspace_lock
from fecompiler.cli.core.records import disclosure_cmd, error_record
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.project.config import (
    ProjectConfig,
    ProjectConfigError,
    ensure_project_config,
    load_project_config,
    set_workspace_parameter,
    write_parameter_override,
)
from fecompiler.cli.project.layout import enclosing_project
from fecompiler.cli.project.params import (
    ParameterSchema,
    lookup_parameter,
    parameter_records,
    parse_cli_value,
)
from fecompiler.cli.workspace_access import load_existing_workspace, read_json_object

_FLOW_STEPS = set(step_names())


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
        overrides=_file_overrides(context, config),
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
        overrides=_file_overrides(context, config),
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
    return _mutate(context, lambda: _set_parameter(command_input, context))


def unset_parameter(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    return _mutate(context, lambda: _unset_parameter(command_input, context))


def _mutate(
    context: CommandContext, action: Callable[[], CommandResult]
) -> CommandResult:
    # Project mode edits only ecc-fe.toml; a direct workspace is rewritten.
    if not context.workspace_mode:
        return action()
    return with_workspace_lock(context.workspace_dir, action)


def _set_parameter(
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
    refused = _fixed_run_parameters(context, schema)
    if refused is not None:
        return refused
    loaded = _load(context)
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, config = loaded
    if context.workspace_mode:
        return _write_workspace_parameter(
            context, schema, parameters, config, value, status="set"
        )
    try:
        config = config or ensure_project_config(context.config_dir, parameters)
        config = write_parameter_override(config, schema.param, value)
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
                "flow_reset": False,
                "parameters_changed": False,
            }
        ]
    )


def _fixed_run_parameters(
    context: CommandContext, schema: ParameterSchema
) -> CommandResult | None:
    """Refuse to edit a project run that is addressed as a direct workspace."""
    project = (
        enclosing_project(context.workspace_dir) if context.workspace_mode else None
    )
    if project is None:
        return None
    return CommandResult.err(
        [
            error_record(
                "run_parameters_fixed",
                workspace=context.workspace_dir,
                project=project,
                reason="a project run keeps the parameters it was created with",
                new_run_cmd=(
                    f"ecc-fe run --project {shlex.quote(project)} "
                    f"--run-id <new-run> --set {schema.param}=<value>"
                ),
            )
        ],
        exit_code=2,
    )


def _write_workspace_parameter(
    context: CommandContext,
    schema: ParameterSchema,
    parameters: dict[str, object],
    config: ProjectConfig | None,
    value: object,
    *,
    status: str,
) -> CommandResult:
    """Change one parameter of a direct workspace.

    home/parameters.json is the only source of a direct workspace's values.
    ecc-fe.toml beside it only remembers the baseline that `param unset`
    restores; it never overrides the workspace.
    """
    try:
        # Capture the baseline before the first change made through the CLI.
        config = config or ensure_project_config(context.config_dir, parameters)
        changed = set_workspace_parameter(context.workspace_dir, schema.param, value)
        if schema.param in config.overrides:
            # Left by an older ecc-fe that mirrored values into [params].
            config = write_parameter_override(config, schema.param, None, unset=True)
    except (OSError, TypeError, ValueError, ProjectConfigError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    if changed:
        # The value is stored first: if the reset fails, the next run still
        # sees the change and marks the affected results stale.
        failure = _reset_flow(context.workspace_dir)
        if failure is not None:
            return failure
    return CommandResult.ok(
        [
            {
                "kind": "param_update",
                "param": schema.param,
                "value": value,
                "status": status if changed or status == "set" else "no_override",
                "source": "home/parameters.json" if status == "set" else "default",
                "flow_reset": changed,
                "parameters_changed": changed,
            }
        ]
    )


def _unset_parameter(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ParamUnsetInput)
    schema = lookup_parameter(command_input.key)
    if schema is None:
        return CommandResult.err(
            [error_record("unknown_parameter", param=command_input.key)]
        )
    refused = _fixed_run_parameters(context, schema)
    if refused is not None:
        return refused
    loaded = _load(context)
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, config = loaded
    if context.workspace_mode and config is not None:
        return _write_workspace_parameter(
            context,
            schema,
            parameters,
            config,
            config.defaults.get(schema.param, schema.default),
            status="unset",
        )
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
    try:
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
                "flow_reset": False,
                "parameters_changed": False,
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
    overrides = _file_overrides(context, config)
    if config is None or (not context.workspace_mode and not overrides):
        return CommandResult.ok([{"kind": "param_diff", "diff_status": "clean"}])
    records = parameter_records(
        parameters,
        defaults=config.defaults,
        overrides=overrides,
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
        if (context.workspace_mode or record["param"] in config.overrides)
        and record["value"] != record["default"]
    ]
    return (
        CommandResult.ok(changed)
        if changed
        else CommandResult.ok([{"kind": "param_diff", "diff_status": "clean"}])
    )


def _file_overrides(
    context: CommandContext, config: ProjectConfig | None
) -> dict[str, object] | None:
    """Return the [params] that apply: a project's, never a direct workspace's."""
    if config is None or context.workspace_mode:
        return None
    return config.overrides


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
