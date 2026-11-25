"""``flow list`` and ``flow show``: what the flow of a design is made of.

Read-only views of the step registry (``allflow/steps.py``) and of the flow
profile the design's parameters select (``allflow/profile.py``). What a step
has done is the business of ``status``; these commands describe the flow
before anything runs.
"""

from __future__ import annotations

from fecompiler.allflow.profile import (
    GENERIC_RTL,
    FrontendFlowProfile,
    flow_profile,
    parameter_groups_outside,
)
from fecompiler.allflow.steps import (
    StepDefinition,
    find_step_definition,
    input_preparation_step,
    step_definitions,
    step_names,
)
from fecompiler.cli.core.inputs import CommandInput, FlowShowInput
from fecompiler.cli.core.records import disclosure_cmd, error_record
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.handlers.param import load_parameters
from fecompiler.cli.project.params import PARAMETERS


def list_flow(command_input: CommandInput, context: CommandContext) -> CommandResult:
    loaded = _profile(command_input, context)
    if isinstance(loaded, CommandResult):
        return loaded
    profile, _ = loaded
    in_flow = [name for name, _ in profile.steps]
    summary: dict[str, object] = {
        "kind": "flow",
        "design_kind": profile.design_kind,
        "describes": _described(command_input, context),
        "project": None if context.workspace_mode else context.project_dir,
        "run": _run_name(command_input, context),
        "workspace": context.workspace_dir if context.workspace_mode else None,
        "steps": in_flow,
        "capabilities": sorted(profile.capabilities),
    }
    records: list[dict[str, object]] = [summary]
    for definition in step_definitions():
        record: dict[str, object] = {
            "kind": "flow_step",
            "index": in_flow.index(definition.name) if definition.name in in_flow else None,
            "step": definition.name,
            "tool": definition.tool,
            "directory": definition.directory_name,
            "in_flow": definition.name in in_flow,
        }
        if definition.name not in in_flow:
            record["reason"] = _outside_reason(definition, profile)
        records.append(record)
    return CommandResult.ok(records)


def show_step(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, FlowShowInput)
    definition = find_step_definition(command_input.step)
    if definition is None:
        return CommandResult.err(
            [
                error_record(
                    "unknown_step", step=command_input.step, expected=list(step_names())
                )
            ],
            exit_code=2,
        )
    loaded = _profile(command_input, context)
    if isinstance(loaded, CommandResult):
        return loaded
    profile, parameters = loaded
    in_flow = [name for name, _ in profile.steps]
    included = definition.name in in_flow
    position = in_flow.index(definition.name) if included else None
    hidden_groups = parameter_groups_outside(parameters)
    target = _target(command_input, context)
    record: dict[str, object] = {
        "kind": "flow_step",
        "step": definition.name,
        "tool": definition.tool,
        "directory": definition.directory_name,
        "in_flow": included,
        "reason": None if included else _outside_reason(definition, profile),
        "index": position,
        "after": in_flow[position - 1] if position else None,
        "before": (
            in_flow[position + 1]
            if position is not None and position + 1 < len(in_flow)
            else None
        ),
        # Every step but the first reads the inputs that step prepared.
        "inputs_from": (
            None if definition.prepares_inputs else input_preparation_step().name
        ),
        "programs": list(definition.executables),
        "environment_checks": list(definition.doctor_checks),
        "parameters": [
            schema.param
            for schema in PARAMETERS
            if definition.name in schema.applies.split(",")
            and schema.param.partition(".")[0] not in hidden_groups
        ],
        "qor_report": f"{definition.directory_name}/report/{definition.qor_report}",
        "handler": definition.handler,
        # A successful result is out of date once any of these changes.
        "stale_when": {
            "inputs": "the files the step reads",
            "parameters": _named(_tracked(definition), hidden_groups),
            # Parameters that change how the step runs, not what it produces.
            "parameters_not_tracked": _named(definition.config_untracked, hidden_groups),
            # Values the design or the catalog entry fixes, not set by `param set`.
            "design_settings": [
                key for key in definition.config_fields if key not in _PARAMETER_NAMES
            ],
            "programs": list(definition.executables),
            "sources": [f"fecompiler/{path}" for path in definition.source_files],
        },
    }
    if included:
        record["run_cmd"] = disclosure_cmd(
            f"ecc-fe run --only {definition.name}", **target
        )
        record["params_cmd"] = disclosure_cmd(
            f"ecc-fe param list --step {definition.name}", **target
        )
        record["doctor_cmd"] = disclosure_cmd(
            f"ecc-fe doctor --step {definition.name}", **target
        )
    return CommandResult.ok([record])


def _profile(
    command_input: CommandInput, context: CommandContext
) -> tuple[FrontendFlowProfile, dict[str, object]] | CommandResult:
    loaded = load_parameters(context, of_run=_of_run(command_input, context))
    if isinstance(loaded, CommandResult):
        return loaded
    _, parameters, _ = loaded
    try:
        return flow_profile(parameters), parameters
    except ValueError as error:
        return CommandResult.err(
            [error_record("unsupported_design_kind", reason=str(error))]
        )


def _of_run(command_input: CommandInput, context: CommandContext) -> bool:
    return not context.workspace_mode and command_input.run_id is not None


def _described(command_input: CommandInput, context: CommandContext) -> str:
    if context.workspace_mode:
        return "workspace"
    return "run" if _of_run(command_input, context) else "next_run"


def _run_name(command_input: CommandInput, context: CommandContext) -> str | None:
    return command_input.run_id if _of_run(command_input, context) else None


def _target(command_input: CommandInput, context: CommandContext) -> dict[str, str | None]:
    if context.workspace_mode:
        return {"workspace": command_input.workspace}
    return {"project": context.project, "run_id": _run_name(command_input, context)}


def _outside_reason(definition: StepDefinition, profile: FrontendFlowProfile) -> str:
    if definition.name == "sim" and profile.design_kind == GENERIC_RTL:
        return "an RTL design is simulated when it names a testbench (testbench.top)"
    return f"not part of the flow of a {profile.design_kind} design"


_PARAMETER_NAMES = {schema.parameter_key: schema.param for schema in PARAMETERS}


def _tracked(definition: StepDefinition) -> list[str]:
    """The stored parameter keys whose change makes the step's result stale."""
    keys = dict.fromkeys((*definition.config_fields, *definition.config_files))
    keys.update(
        dict.fromkeys(
            key
            for key in _PARAMETER_NAMES
            if any(map(key.startswith, definition.config_prefixes))
        )
    )
    return [key for key in keys if key not in definition.config_untracked]


def _named(keys: tuple[str, ...] | list[str], hidden_groups: frozenset[str]) -> list[str]:
    """The names users set those parameters by, for the ones this design has."""
    names = dict.fromkeys(_PARAMETER_NAMES[key] for key in keys if key in _PARAMETER_NAMES)
    return [name for name in names if name.partition(".")[0] not in hidden_groups]
