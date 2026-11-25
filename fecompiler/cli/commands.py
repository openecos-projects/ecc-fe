from __future__ import annotations

import codecs
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from fecompiler.allflow.profile import flow_steps as profile_flow_steps
from fecompiler.allflow.steps import find_step_definition, step_directory, step_names
from fecompiler.application.events import workspace_event_sink
from fecompiler.application.workspace_service import workspace_application
from fecompiler.catalog import check_catalog_contracts
from fecompiler.cli.core.inputs import (
    CheckInput,
    CommandInput,
    ConfigInput,
    DoctorInput,
    InitInput,
    LogInput,
    ResourceEnvInput,
    ResourceInstallInput,
    ResourceListInput,
    RunInput,
    StatusInput,
)
from fecompiler.cli.core.locking import with_workspace_lock
from fecompiler.cli.core.records import (
    FAILED_STATES,
    disclosure_cmd,
    error_record,
    failure_record,
    flow_state,
    normalize_state,
)
from fecompiler.cli.core.types import CommandContext, CommandResult, OutputMode
from fecompiler.cli.project.config import (
    ProjectConfig,
    ProjectConfigError,
    apply_project_overrides,
    next_run_parameters,
    create_project_config,
    load_project_config,
    load_run_parameter_overrides,
    project_config_path,
)
from fecompiler.cli.project.layout import (
    clone_workspace_template,
    is_protected_run_target,
    is_safe_run_directory,
    publish_workspace_replacement,
    resolves_as_spelled,
    stage_workspace_replacement,
    template_dir,
)
from fecompiler.cli.project.relocate import record_project_location
from fecompiler.cli.project.params import (
    parameter_records,
    parse_cli_overrides,
    run_parameter_records,
)
from fecompiler.cli.rendering.progress import RunProgress
from fecompiler.cli.resource_manager import ResourceManager, ResourceManagerError
from fecompiler.tools.common.build_policy import clean_build
from fecompiler.tools.common.filelist import parse_filelist
from fecompiler.tools.common.sv_module import macro_names, module_definitions
from fecompiler.cli.workspace_access import (
    load_existing_workspace,
    read_json_object,
    workspace_markers,
)
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import (
    UnsupportedWorkspaceSchemaError,
    workspace_schema_version,
)
from fecompiler.engine.flow import stale_steps
from fecompiler.resources import (
    activate_managed_resources,
    installed_tool_entries,
    read_resource_manifest,
    resource_manifest_path,
    tool_entry_health,
)
from fecompiler.utility.atomic_write import atomic_write_json
from fecompiler.utility.program import invoked
from fecompiler.utility.workspace_lock import workspace_lock_owner

_FLOW_STEPS = step_names()
_LOG_TAIL_MAX_BYTES = 4 * 1024 * 1024
_RESOURCE_ENV_KEYS = (
    "ECOS_FE_CLI",
    "ECOS_FE_COMPILER_ROOT",
    "ECOS_FE_RESOURCE_ROOTS",
    "ECOS_FE_SOC_ROOT",
    "ECOS_SLANG",
    "ECOS_VERILATOR",
    "VERILATOR_ROOT",
    "RISCV",
    "RISCV_PREFIX",
    "RISCV_TOOLCHAIN",
    "CHIPCOMPILER_OSS_CAD_DIR",
    "ECOS_ELECTRON_OSS_CAD_DIR",
    "ECOS_SURFER_ASSETS_PATH",
    "PATH",
)


def init(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, InitInput)
    if command_input.name is not None:
        return _init_project(command_input)
    return _init_workspace(command_input, context)


def _init_workspace(
    command_input: InitInput, context: CommandContext
) -> CommandResult:
    if project_config_path(context.workspace_dir).exists() or any(
        path.exists() for path in workspace_markers(context.workspace_dir)
    ):
        return CommandResult.err(
            [
                error_record(
                    "workspace_already_exists",
                    workspace=context.workspace_dir,
                    inspect_cmd=disclosure_cmd("ecc-fe status", context.workspace_dir),
                )
            ]
        )
    request = _init_request(
        command_input,
        context.workspace_dir,
        default_design=Path(context.workspace_dir).name,
    )
    if isinstance(request, CommandResult):
        return request
    created = _missing_ancestors(Path(context.workspace_dir))
    result = workspace_application.execute_payload(
        "create", request, base_dir=Path.cwd()
    )
    if result.response != "success":
        _remove_created(created, [Path(context.workspace_dir)])
        return _init_failure(result, workspace=context.workspace_dir)
    config_path: str | None = None
    if result.response == "success":
        try:
            workspace = load_existing_workspace(context.workspace_dir)
            if workspace is None:
                raise ProjectConfigError("Created workspace cannot be loaded")
            parameters = read_json_object(Path(workspace["parameters_path"]))
            config_path = str(
                create_project_config(context.workspace_dir, parameters).path
            )
        except (OSError, TypeError, ValueError, ProjectConfigError) as error:
            return CommandResult.err(
                [
                    error_record(
                        "project_config_failed",
                        workspace=context.workspace_dir,
                        reason=str(error),
                    )
                ]
            )
    record = {
        "kind": "workspace_init",
        "status": result.response,
        "workspace": result.data.get("directory", context.workspace_dir),
        "messages": result.message,
        "config": config_path,
        "next_cmd": disclosure_cmd("ecc-fe doctor", context.workspace_dir),
    }
    return (
        CommandResult.ok([record])
        if result.response == "success"
        else CommandResult.err([record])
    )


def _init_project(command_input: InitInput) -> CommandResult:
    if command_input.workspace is not None:
        return CommandResult.err(
            [error_record("project_workspace_conflict")], exit_code=2
        )
    name = command_input.name or ""
    if not name.strip():
        return CommandResult.err(
            [error_record("project_name_required")], exit_code=2
        )
    project_dir = Path(name).expanduser().resolve()
    if project_dir.is_file():
        return CommandResult.err(
            [error_record("path_is_file", path=str(project_dir))]
        )
    config_path = project_config_path(str(project_dir))
    seed_dir = Path(template_dir(str(project_dir)))
    if config_path.exists() or any(
        path.exists() for path in workspace_markers(str(seed_dir))
    ):
        return CommandResult.err(
            [error_record("project_already_exists", project=str(project_dir))]
        )
    request = _init_request(
        command_input,
        str(seed_dir),
        default_design=project_dir.name,
    )
    if isinstance(request, CommandResult):
        return request
    # Everything this command creates is removed again if it fails, so a
    # failed init can simply be repeated.
    created = _missing_ancestors(project_dir / "runs")
    scratch = [project_dir / "runs", seed_dir, seed_dir.parent]
    (project_dir / "runs").mkdir(parents=True, exist_ok=True)
    try:
        result = workspace_application.execute_payload(
            "create", request, base_dir=Path.cwd()
        )
    except BaseException:
        _remove_created(created, scratch)
        raise
    if result.response != "success":
        _remove_created(created, scratch)
        return _init_failure(
            result,
            project=str(project_dir),
            hint=(
                "for a design that is not a CPU, add --kind rtl"
                if command_input.rtl and command_input.kind is None
                else None
            ),
        )
    try:
        workspace = load_existing_workspace(str(seed_dir))
        if workspace is None:
            raise ProjectConfigError("Created project template cannot be loaded")
        parameters = read_json_object(Path(workspace["parameters_path"]))
        config = create_project_config(
            str(project_dir), parameters, project_layout=True
        )
    except (OSError, TypeError, ValueError, ProjectConfigError) as error:
        _remove_created(created, scratch)
        return CommandResult.err(
            [
                error_record(
                    "project_config_failed",
                    project=str(project_dir),
                    reason=str(error),
                )
            ]
        )
    record_project_location(str(project_dir))
    project_arg = name
    return CommandResult.ok(
        [
            {
                "kind": "project_init",
                "status": "created",
                "project": str(project_dir),
                "design_kind": _init_kind(command_input),
                "config": str(config.path),
                "run_dir": "runs/default",
                "check_cmd": disclosure_cmd("ecc-fe check", project=project_arg),
                "run_cmd": disclosure_cmd("ecc-fe run", project=project_arg),
            }
        ]
    )


def _missing_ancestors(path: Path) -> list[Path]:
    """Return the directories that do not exist yet on the way to *path*."""
    missing: list[Path] = []
    current = path
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    return missing


def _remove_created(created: list[Path], scratch: list[Path]) -> None:
    """Undo a failed init.

    *scratch* directories hold only what init wrote: the half-built template
    is removed with its content, the others only when they are empty.
    *created* directories did not exist before and are removed when empty, so
    nothing the user had is ever deleted.
    """
    for path in scratch:
        if path.name == "template" and path.parent.name == ".ecc-fe":
            shutil.rmtree(path, ignore_errors=True)
    for path in [*scratch, *created]:
        try:
            path.rmdir()
        except OSError:
            pass


def _init_failure(result: object, **target: object) -> CommandResult:
    """Report why the workspace could not be created, one record per reason."""
    data = getattr(result, "data", None)
    messages = [str(item) for item in getattr(result, "message", None) or []]
    validation = data.get("validation") if isinstance(data, dict) else None
    issues = validation.get("issues") if isinstance(validation, dict) else None
    records: list[dict[str, object]] = [
        error_record(
            "init_failed",
            **{key: value for key, value in target.items() if key != "hint"},
            message=" ".join(messages) or None,
            hint=target.get("hint"),
            error_code=data.get("error") if isinstance(data, dict) else None,
            fields=data.get("unsupported_fields") if isinstance(data, dict) else None,
        )
    ]
    for issue in issues if isinstance(issues, list) else []:
        if isinstance(issue, dict):
            records.append({"kind": "init_issue", **issue})
    return CommandResult.err(records)


_INIT_KINDS = ("cpu", "rtl")
_CPU_ONLY_INIT_OPTIONS = (
    ("--cpu-filelist", "cpu_filelist"),
    ("--cpu-top-module", "cpu_top_module"),
    ("--soc-filelist", "soc_filelist"),
    ("--core-id", "core_id"),
    ("--soc-harness-id", "soc_harness_id"),
)


def _init_kind(command_input: InitInput) -> str:
    """Return "cpu" or "rtl"; a filelist of a plain design implies "rtl"."""
    if command_input.kind:
        return command_input.kind.strip().lower()
    return "rtl" if command_input.filelist else "cpu"


def _init_request(
    command_input: InitInput, destination: str, *, default_design: str
) -> dict[str, object] | CommandResult:
    """Build the workspace create request, or the usage error that prevents it."""
    kind = _init_kind(command_input)
    if kind not in _INIT_KINDS:
        return CommandResult.err(
            [error_record("invalid_kind", kind=kind, expected=list(_INIT_KINDS))],
            exit_code=2,
        )
    if kind == "rtl":
        return _generic_init_request(command_input, destination, default_design)
    if command_input.testbench or command_input.testbench_top:
        return CommandResult.err(
            [
                error_record(
                    "init_option_conflict",
                    options=["--testbench", "--testbench-top"],
                    reason="a testbench belongs to an RTL design; "
                    "a CPU is simulated on its SoC harness",
                )
            ],
            exit_code=2,
        )
    if command_input.filelist:
        return CommandResult.err(
            [
                error_record(
                    "init_option_conflict",
                    options=["--filelist"],
                    reason="--filelist describes an RTL design; a CPU takes --cpu-filelist",
                )
            ],
            exit_code=2,
        )
    if len(command_input.rtl) > 1:
        return CommandResult.err(
            [
                error_record(
                    "init_option_conflict",
                    options=["--rtl"],
                    reason="a custom CPU takes one --rtl file; list more in --cpu-filelist",
                )
            ],
            exit_code=2,
        )
    rtl = command_input.rtl[0] if command_input.rtl else None
    custom_cpu_input = command_input.cpu_filelist or rtl
    core_id = command_input.core_id or (
        "custom-filelist" if custom_cpu_input else "picorv32"
    )
    request: dict[str, object] = {
        "directory": destination,
        "core_id": core_id,
        "parameters": {
            "Design": command_input.design or default_design,
            "Top module": command_input.top,
        },
    }
    if rtl:
        request["cpu_rtl_files"] = [rtl]
        request["cpu_top_module"] = command_input.top
    if command_input.cpu_top_module:
        request["cpu_top_module"] = command_input.cpu_top_module
    for key, value in (
        ("cpu_filelist", command_input.cpu_filelist),
        ("soc_filelist", command_input.soc_filelist),
        ("soc_harness_id", command_input.soc_harness_id),
    ):
        if value:
            request[key] = value
    return request


def _generic_init_request(
    command_input: InitInput, destination: str, default_design: str
) -> dict[str, object] | CommandResult:
    cpu_options = [
        option
        for option, field in _CPU_ONLY_INIT_OPTIONS
        if getattr(command_input, field)
    ]
    if cpu_options:
        return CommandResult.err(
            [
                error_record(
                    "init_option_conflict",
                    options=cpu_options,
                    reason="these options describe a CPU; drop them or use --kind cpu",
                )
            ],
            exit_code=2,
        )
    if bool(command_input.rtl) == bool(command_input.filelist):
        return CommandResult.err(
            [
                error_record(
                    "init_input_required",
                    reason="an RTL design needs either --rtl <file>... or --filelist <file>",
                )
            ],
            exit_code=2,
        )
    if bool(command_input.testbench) != bool(command_input.testbench_top):
        return CommandResult.err(
            [
                error_record(
                    "init_input_required",
                    reason="a testbench needs both --testbench <file>... "
                    "and --testbench-top <module>",
                )
            ],
            exit_code=2,
        )
    parameters: dict[str, object] = {
        "Design": command_input.design or default_design,
        "Top module": command_input.top,
    }
    if command_input.testbench_top:
        # Kept where they are, like the design sources.
        parameters["testbench_top"] = command_input.testbench_top
        parameters["testbench_files"] = [
            str(Path(path).expanduser().resolve()) for path in command_input.testbench
        ]
    request: dict[str, object] = {
        "directory": destination,
        "frontend_design_kind": "generic_rtl",
        "top_module": command_input.top,
        "parameters": parameters,
    }
    # Sources are referenced where they are, not copied: the next run sees
    # every edit, and include paths keep working.
    if command_input.filelist:
        request["filelist"] = command_input.filelist
    else:
        request["rtl_list"] = list(command_input.rtl)
    issues = _generic_init_issues(command_input) or _testbench_init_issues(command_input)
    if issues:
        return CommandResult.err(
            [
                error_record(
                    "init_failed",
                    message="The RTL design cannot be used as given.",
                ),
                *({"kind": "init_issue", **issue} for issue in issues),
            ]
        )
    return request


def _testbench_init_issues(command_input: InitInput) -> list[dict[str, object]]:
    """Check the testbench of an RTL design before anything is created."""

    def issue(code: str, message: str, field: str) -> dict[str, object]:
        return {"severity": "error", "code": code, "message": message, "field": field}

    missing = [path for path in command_input.testbench if not Path(path).is_file()]
    if missing:
        return [
            issue("testbench_file_not_found", f"Testbench file not found: {path}", "testbench")
            for path in missing
        ]
    top = command_input.testbench_top
    if top and not module_definitions(list(command_input.testbench), top):
        return [
            issue(
                "testbench_top_not_found",
                f"Module {top} is not defined in the testbench files; "
                "name it with --testbench-top.",
                "testbench_top",
            )
        ]
    return []


def _generic_init_issues(command_input: InitInput) -> list[dict[str, object]]:
    """Check the inputs of an RTL design before anything is created."""

    def issue(code: str, message: str, field: str) -> dict[str, object]:
        return {"severity": "error", "code": code, "message": message, "field": field}

    top = command_input.top
    defines: tuple[str, ...] = ()
    if command_input.filelist:
        parsed = parse_filelist(command_input.filelist)
        if not parsed.filelists:
            return [issue("filelist_not_found", f"Filelist not found: {parsed.path}", "filelist")]
        if parsed.missing:
            return [
                issue("rtl_file_not_found", f"Listed file not found: {path}", "filelist")
                for path in parsed.missing
            ]
        sources = [str(path) for path in parsed.sources]
        defines = parsed.defines
        field = "filelist"
    else:
        missing = [path for path in command_input.rtl if not Path(path).is_file()]
        if missing:
            return [
                issue("rtl_file_not_found", f"RTL file not found: {path}", "rtl")
                for path in missing
            ]
        sources = list(command_input.rtl)
        field = "rtl"
    if not sources:
        return [issue("no_rtl_sources", "No .v or .sv source file is listed.", field)]
    found = module_definitions(
        sources, top, defined_macros=macro_names(defines)
    ) or module_definitions(sources, top)
    if not found:
        return [
            issue(
                "top_module_not_found",
                f"Top module {top} is not defined in the given RTL; name it with --top.",
                "top",
            )
        ]
    if len(found) > 1:
        return [
            issue(
                "top_module_count_mismatch",
                f"Top module {top} is defined in {len(found)} files: "
                + ", ".join(str(path) for path in found),
                "top",
            )
        ]
    return []


def run_flow(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, RunInput)
    run = _run_workspace if context.workspace_mode else _run_project
    return with_workspace_lock(
        context.workspace_dir, lambda: run(command_input, context)
    )


def _run_workspace(
    command_input: RunInput, context: CommandContext
) -> CommandResult:
    if command_input.run_id is not None:
        return CommandResult.err(
            [error_record("project_workspace_conflict")], exit_code=2
        )
    if command_input.overwrite:
        return CommandResult.err(
            [error_record("overwrite_requires_project")], exit_code=2
        )
    if command_input.param_set:
        return CommandResult.err(
            [error_record("set_requires_project")], exit_code=2
        )
    selection = _run_selection(command_input)
    if isinstance(selection, CommandResult):
        return selection
    outside = _step_outside_flow(selection, context)
    if outside is not None:
        return outside
    try:
        workspace = load_existing_workspace(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context)
    # A direct workspace is run as it is: home/parameters.json is its only
    # source of parameter values, whoever wrote them.
    try:
        project_config = load_project_config(context.config_dir)
    except (OSError, TypeError, ValueError):
        project_config = None
    changed: list[str] = []
    only = selection.only
    result = _execute_selection(selection, context.workspace_dir, context)
    record = {
        "kind": "run",
        "status": result.response,
        "workspace": context.workspace_dir,
        "step": only,
        "from_step": selection.from_step,
        "to_step": selection.to_step,
        "error": _result_error(result.data),
        "messages": result.message,
        "config": str(project_config.path) if project_config else None,
        "config_changes": changed,
        "status_cmd": _target_cmd("ecc-fe status", context),
        "log_cmd": disclosure_cmd(
            f"ecc-fe log {only}"
            if only
            else "ecc-fe log",
            context.workspace_dir if context.workspace_mode else None,
            project=context.project,
            run_id=context.run_id,
        ),
    }
    return _run_result(record, result, context)


def _run_result(
    record: dict[str, object], result: object, context: CommandContext
) -> CommandResult:
    """Return one `step` record per executed step, then the `run` record.

    The `run` record is always the last one, after any streamed progress.
    """
    steps = _step_result_records(getattr(result, "data", None))
    if getattr(result, "response", None) == "success":
        return CommandResult.ok([*steps, record])
    if any(str(step.get("state", "")).lower() in FAILED_STATES for step in steps):
        # A step ran and failed: say where the reason is summarised.
        record["failure_cmd"] = _target_cmd("ecc-fe report failure", context)
    return CommandResult.err([*steps, record])


def _result_error(data: object) -> object:
    return data.get("error") if isinstance(data, dict) else None


def _step_result_records(data: object) -> list[dict[str, object]]:
    if not isinstance(data, dict):
        return []
    reports = data.get("reports")
    entries = reports if isinstance(reports, list) else [data]
    records: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("step"):
            continue
        records.append(
            {
                "kind": "step",
                "step": entry["step"],
                "tool": entry.get("tool"),
                "state": normalize_state(entry.get("state")),
                "log": entry.get("log_file"),
                "report": entry.get("report_file"),
            }
        )
    return records


@dataclass(frozen=True, slots=True)
class _RunSelection:
    """Which steps one `run` invocation executes."""

    only: str | None = None
    from_step: str | None = None
    to_step: str | None = None
    resume: bool = False
    # Re-execute steps that already succeeded.
    rerun: bool = False
    # Build everything again instead of reusing what earlier runs built.
    clean: bool = False

    @property
    def in_place(self) -> bool:
        """True when the selection continues an existing run instead of a new one."""
        return bool(self.only or self.from_step or self.resume or self.rerun)


def _run_selection(command_input: RunInput) -> _RunSelection | CommandResult:
    def usage(error: str, **fields: object) -> CommandResult:
        return CommandResult.err([error_record(error, **fields)], exit_code=2)

    if command_input.step and command_input.only:
        return usage("selector_conflict", selectors=["--step", "--only"])
    only = command_input.only or command_input.step
    selectors = sum(
        (
            command_input.resume,
            command_input.from_step is not None,
            only is not None,
        )
    )
    if selectors > 1:
        return usage("selector_conflict")
    if only is not None and command_input.to_step is not None:
        return usage("selector_conflict", selectors=["--only", "--to"])
    if command_input.force and only is None:
        return usage("force_requires_only")
    if command_input.rerun and (
        command_input.resume or command_input.from_step is not None
    ):
        return usage("rerun_selector_conflict")
    if command_input.clean and not (
        only is not None or command_input.from_step is not None or command_input.rerun
    ):
        # Steps that are skipped as up to date would not be rebuilt at all.
        return usage(
            "clean_requires_rerun",
            reason="--clean rebuilds the steps that are run again",
            expected=["--only <step>", "--from <step>", "--rerun"],
        )
    for step in (only, command_input.from_step, command_input.to_step):
        if step and step not in _FLOW_STEPS:
            return usage("unknown_step", step=step, expected=list(_FLOW_STEPS))
    if (
        command_input.from_step
        and command_input.to_step
        and _FLOW_STEPS.index(command_input.to_step)
        < _FLOW_STEPS.index(command_input.from_step)
    ):
        return usage(
            "invalid_step_range",
            from_step=command_input.from_step,
            to_step=command_input.to_step,
            expected=list(_FLOW_STEPS),
        )
    return _RunSelection(
        only=only,
        from_step=command_input.from_step,
        to_step=command_input.to_step,
        resume=command_input.resume,
        rerun=(
            command_input.rerun
            if only is None
            else command_input.force or command_input.rerun or command_input.clean
        ),
        clean=command_input.clean,
    )


def _execute_selection(
    selection: _RunSelection, directory: str, context: CommandContext
):
    payload: dict[str, object] = {"directory": directory, "rerun": selection.rerun}
    if selection.only:
        payload["step"] = selection.only
    else:
        if selection.from_step:
            payload["from_step"] = selection.from_step
        if selection.to_step:
            payload["to_step"] = selection.to_step
    # Steps are reported as they start and finish, not only at the end.
    with (
        workspace_event_sink(RunProgress(context.output_mode)),
        clean_build(selection.clean),
    ):
        return workspace_application.execute_payload(
            "run-step" if selection.only else "run-flow", payload, base_dir=Path.cwd()
        )


def _continue_project_run(
    command_input: RunInput,
    context: CommandContext,
    selection: _RunSelection,
    config: ProjectConfig,
) -> CommandResult:
    """Apply a selector to an existing project run, in place.

    A run keeps the parameters it was created with: project defaults changed
    since then are not applied, so its earlier results stay comparable.
    """
    run_dir = context.workspace_dir
    run_name = context.run_id or "default"
    if command_input.overwrite:
        return CommandResult.err(
            [error_record("overwrite_selector_conflict")], exit_code=2
        )
    if command_input.param_set:
        return CommandResult.err(
            [
                error_record(
                    "set_requires_new_run",
                    reason="parameters are fixed when a run is created",
                    new_run_cmd=invoked("ecc-fe run --run-id <new-run> --set key=value"),
                )
            ],
            exit_code=2,
        )
    if not os.path.lexists(run_dir):
        return CommandResult.err(
            [
                error_record(
                    "run_not_found",
                    run=run_name,
                    workspace=run_dir,
                    create_cmd=_target_cmd("ecc-fe run", context),
                )
            ]
        )
    if not resolves_as_spelled(run_dir, context.project_dir):
        return CommandResult.err(
            [
                error_record(
                    "run_path_refused",
                    run=run_name,
                    workspace=run_dir,
                    reason="run path is redirected by a symlink",
                )
            ]
        )
    try:
        workspace = load_existing_workspace(run_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err([failure_record("workspace_unreadable", error, workspace=run_dir)])
    if workspace is None:
        return _workspace_not_found(context)
    result = _execute_selection(selection, run_dir, context)
    record = {
        "kind": "run",
        "run": run_name,
        "status": result.response,
        "workspace": run_dir,
        "step": selection.only,
        "from_step": selection.from_step,
        "to_step": selection.to_step,
        "error": _result_error(result.data),
        "messages": result.message,
        "config": str(config.path),
        "status_cmd": _target_cmd("ecc-fe status", context),
        "log_cmd": _target_cmd(
            f"ecc-fe log {selection.only}" if selection.only else "ecc-fe log",
            context,
        ),
    }
    return _run_result(record, result, context)


def _run_project(command_input: RunInput, context: CommandContext) -> CommandResult:
    selection = _run_selection(command_input)
    if isinstance(selection, CommandResult):
        return selection
    outside = _step_outside_flow(selection, context)
    if outside is not None:
        return outside
    try:
        config = load_project_config(context.config_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [error_record("invalid_project_config", reason=str(error))]
        )
    if config is None or not config.uses_project_layout:
        return CommandResult.err(
            [
                error_record(
                    "missing_config",
                    path=str(project_config_path(context.config_dir)),
                )
            ]
        )
    if selection.in_place:
        return _continue_project_run(command_input, context, selection, config)
    overrides, override_errors = parse_cli_overrides(command_input.param_set)
    if override_errors:
        return CommandResult.err(
            [
                error_record("invalid_parameter", reason=error)
                for error in override_errors
            ],
            exit_code=2,
        )
    run_dir = context.workspace_dir
    run_name = context.run_id or "default"
    if is_protected_run_target(run_dir, context.project_dir):
        return CommandResult.err(
            [
                error_record(
                    "invalid_run_id",
                    run=run_name,
                    workspace=run_dir,
                    reason=(
                        "run id must not resolve to the project, runs container, "
                        "or template"
                    ),
                )
            ]
        )
    replacing = os.path.lexists(run_dir)
    if replacing:
        if not command_input.overwrite:
            return CommandResult.err(
                [
                    error_record(
                        "run_exists",
                        run=run_name,
                        workspace=run_dir,
                        resume=_target_cmd("ecc-fe run --resume", context),
                        overwrite=_target_cmd("ecc-fe run --overwrite", context),
                    )
                ]
            )
        if not resolves_as_spelled(
            run_dir, context.project_dir
        ) or not is_safe_run_directory(run_dir):
            return CommandResult.err(
                [
                    error_record(
                        "overwrite_refused",
                        run=run_name,
                        workspace=run_dir,
                        reason="target is not an ECC-FE run directory",
                    )
                ]
            )
        try:
            workspace_schema_version(run_dir)
        except UnsupportedWorkspaceSchemaError as error:
            return CommandResult.err(
                [
                    error_record(
                        "overwrite_refused",
                        run=run_name,
                        workspace=run_dir,
                        reason=str(error),
                    )
                ]
            )
    elif not resolves_as_spelled(run_dir, context.project_dir):
        return CommandResult.err(
            [
                error_record(
                    "run_path_refused",
                    run=run_name,
                    workspace=run_dir,
                    reason="run path is redirected by a symlink",
                )
            ]
        )
    # A replacement is built beside the run it replaces and swapped in only
    # once complete, so a failure here leaves the previous run untouched.
    build_dir = run_dir
    owns_run = False
    try:
        if replacing:
            build_dir = stage_workspace_replacement(
                str(context.template_dir), run_dir
            )
        else:
            clone_workspace_template(str(context.template_dir), run_dir)
        owns_run = True
        _, changed = apply_project_overrides(
            build_dir,
            config_directory=context.config_dir,
            extra_overrides=overrides,
        )
        if overrides:
            provenance = Path(build_dir) / "home" / "cli-param-overrides.json"
            atomic_write_json(provenance, overrides)
        if replacing:
            publish_workspace_replacement(build_dir, run_dir)
    except FileExistsError as error:
        if owns_run:
            shutil.rmtree(build_dir, ignore_errors=True)
            return CommandResult.err(
                [
                    error_record(
                        "workspace_failed",
                        run=run_name,
                        workspace=run_dir,
                        reason=str(error),
                    )
                ]
            )
        return CommandResult.err(
            [
                error_record(
                    "run_exists",
                    run=run_name,
                    workspace=run_dir,
                    overwrite=_target_cmd("ecc-fe run --overwrite", context),
                )
            ]
        )
    except (OSError, TypeError, ValueError) as error:
        if owns_run:
            shutil.rmtree(build_dir, ignore_errors=True)
        return CommandResult.err(
            [
                error_record(
                    "workspace_failed",
                    run=run_name,
                    workspace=run_dir,
                    reason=str(error),
                )
            ]
        )
    result = _execute_selection(selection, run_dir, context)
    record = {
        "kind": "run",
        "run": run_name,
        "status": result.response,
        "workspace": run_dir,
        "to_step": selection.to_step,
        "error": _result_error(result.data),
        "messages": result.message,
        "config": str(config.path),
        "config_changes": changed,
        "parameter_overrides": overrides,
        "status_cmd": _target_cmd("ecc-fe status", context),
        "log_cmd": _target_cmd("ecc-fe log", context),
    }
    return _run_result(record, result, context)


def check(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, CheckInput)
    result = doctor(
        DoctorInput(
            workspace=command_input.workspace,
            output_mode=command_input.output_mode,
            project=command_input.project,
            run_id=command_input.run_id,
            step=command_input.step,
        ),
        context,
    )
    if any(record.get("kind") == "error" for record in result.records):
        return result
    records: list[dict[str, object]] = []
    for record in result.records:
        if record.get("kind") == "doctor_summary":
            continue
        mapped = dict(record)
        if mapped.get("kind") == "doctor_check":
            mapped["kind"] = "check"
        records.append(mapped)
    if not any(record.get("check") == "workspace" for record in records):
        records.append(
            {
                "kind": "check",
                "check": "project",
                "label": "ECC-FE project or workspace",
                "status": "fail",
                "required": True,
                "path": context.project_dir,
                "error": "project_not_found",
                "remediation_cmd": invoked("ecc-fe init <name>"),
            }
        )
    failed = any(
        record.get("status") == "fail" and record.get("required")
        for record in records
    )
    records.append(
        {
            "kind": "check_summary",
            "status": "failed" if failed else "ready",
            "passed": sum(record.get("status") == "pass" for record in records),
            "failed": sum(record.get("status") == "fail" for record in records),
            "attention": sum(
                record.get("status") == "attention" for record in records
            ),
        }
    )
    return CommandResult.err(records) if failed else CommandResult.ok(records)


def doctor(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, DoctorInput)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [
                error_record(
                    "unknown_step",
                    step=command_input.step,
                    expected=list(_FLOW_STEPS),
                )
            ],
            exit_code=2,
        )

    records: list[dict[str, object]] = []
    relevant = _doctor_requirements(command_input.step, _flow_step_names(context))
    if _is_rtl_design(context):
        # Only a CPU compiles programs to run on it.
        relevant = tuple(item for item in relevant if item[0] != "riscv")
    for check_id, label, candidates, required in relevant:
        path = _find_executable(candidates)
        version = _tool_version(path) if path else None
        remediation_cmd, remediation = _doctor_remediation(check_id, path)
        minimum = _MINIMUM_TOOL_VERSIONS.get(check_id)
        too_old = bool(
            path and minimum and (found := _version_tuple(version)) and found < minimum
        )
        if too_old:
            remediation_cmd, remediation = _doctor_remediation(check_id, None)
        record: dict[str, object] = {
            "kind": "doctor_check",
            "check": check_id,
            "label": label,
            "status": "pass"
            if path and not too_old
            else "fail"
            if required
            else "attention",
            "required": required,
            "path": path,
            "version": version,
            "remediation_cmd": remediation_cmd,
            "remediation": remediation,
        }
        if minimum:
            record["minimum_version"] = ".".join(str(part) for part in minimum)
        if too_old:
            record["error"] = "tool_too_old"
        records.append(record)

    try:
        manifest = read_resource_manifest(strict=True)
        records.append(
            {
                "kind": "doctor_check",
                "check": "resource_manifest",
                "label": "Resource manifest",
                "status": "pass",
                "required": False,
                "path": str(resource_manifest_path()),
                "installed": len(installed_tool_entries(manifest)),
            }
        )
    except (OSError, TypeError, ValueError) as error:
        records.append(
            {
                "kind": "doctor_check",
                "check": "resource_manifest",
                "label": "Resource manifest",
                "status": "attention",
                "required": False,
                "path": str(resource_manifest_path()),
                "error": str(error),
            }
        )

    contract = check_catalog_contracts()
    records.append(
        {
            "kind": "doctor_check",
            "check": "frontend_catalog",
            "label": "Frontend catalog contracts",
            "status": "pass" if contract.ok else "fail",
            "required": True,
            "summary": contract.summary,
            "counts": contract.counts,
            "remediation_cmd": None
            if contract.ok
            else invoked("ecc-fe workspace catalog-check --json"),
        }
    )

    diagnostic_workspace = context.workspace_dir
    if not context.workspace_mode and not all(
        path.is_file() for path in workspace_markers(diagnostic_workspace)
    ):
        diagnostic_workspace = str(context.template_dir)
    workspace_expected = (
        command_input.workspace is not None or not context.workspace_mode
    )
    workspace_error: str | None = None
    try:
        workspace = load_existing_workspace(diagnostic_workspace)
    except (OSError, TypeError, ValueError) as error:
        workspace = None
        workspace_error = str(error)
    if workspace_expected or any(
        path.exists() for path in workspace_markers(diagnostic_workspace)
    ):
        records.append(
            {
                "kind": "doctor_check",
                "check": "workspace",
                "label": "Frontend workspace",
                "status": "pass" if workspace else "fail",
                "required": workspace_expected,
                "path": diagnostic_workspace,
                "error": workspace_error,
                "remediation_cmd": None
                if workspace
                else _init_target_cmd(context),
            }
        )
        config_status = "missing"
        config_error: str | None = None
        try:
            config_status = (
                "pass" if load_project_config(context.config_dir) else "missing"
            )
        except (OSError, ValueError) as error:
            config_status = "invalid"
            config_error = str(error)
        records.append(
            {
                "kind": "doctor_check",
                "check": "project_config",
                "label": "ECC-FE project config",
                "status": (
                    "pass"
                    if config_status == "pass"
                    else "fail"
                    if config_status == "invalid"
                    else "attention"
                ),
                "required": config_status == "invalid",
                "path": str(project_config_path(context.config_dir)),
                "error": config_error,
                "remediation_cmd": None
                if config_status == "pass"
                else _target_cmd("ecc-fe param list --all", context),
            }
        )

    failed = any(
        record.get("status") == "fail" and record.get("required") for record in records
    )
    summary = {
        "kind": "doctor_summary",
        "status": "failed" if failed else "ready",
        "passed": sum(record.get("status") == "pass" for record in records),
        "failed": sum(record.get("status") == "fail" for record in records),
        "attention": sum(record.get("status") == "attention" for record in records),
    }
    result_records = [*records, summary]
    return (
        CommandResult.err(result_records)
        if failed
        else CommandResult.ok(result_records)
    )


def status(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, StatusInput)
    try:
        workspace = load_existing_workspace(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context)
    try:
        flow = read_json_object(Path(workspace["flow_path"]))
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, workspace=context.workspace_dir)]
        )
    # A step stays "ongoing" on disk when its command is killed. The workspace
    # lock tells a flow that is really running from one that died.
    owner = workspace_lock_owner(context.workspace_dir)
    summary: dict[str, object] = {
        "kind": "workspace",
        "project": context.project_dir if not context.workspace_mode else None,
        "run": None if context.workspace_mode else (context.run_id or "default"),
        "workspace": context.workspace_dir,
        "design": workspace.get("design", ""),
        "top_module": workspace.get("top_module", ""),
        "running": owner is not None,
    }
    if owner is not None:
        summary["owner_pid"] = owner.get("pid")
    records: list[dict[str, object]] = [summary]
    steps = flow.get("steps", []) if isinstance(flow, dict) else []
    stale = _stale_step_reasons(workspace, steps) if owner is None else {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        name = str(step.get("name", ""))
        state = normalize_state(step.get("state"))
        if state == "ongoing" and owner is None:
            state = "interrupted"
            summary["resume_cmd"] = disclosure_cmd(
                "ecc-fe run --resume", context.workspace_dir
            )
        record: dict[str, object] = {
            "kind": "step",
            "index": index,
            "step": name,
            "tool": str(step.get("tool", "")),
            "state": state,
            "runtime": step.get("runtime", ""),
            "runtime_s": step.get("runtime_s"),
            "peak_memory_mb": step.get("peak memory (mb)", 0),
        }
        if name in stale:
            record["stale"] = True
            record["stale_reason"] = stale[name]
        records.append(record)
    if stale:
        # The stored results are kept until the next run re-executes them.
        summary["stale_steps"] = list(stale)
        summary.setdefault("resume_cmd", _target_cmd("ecc-fe run --resume", context))
    states = [str(record.get("state")) for record in records[1:]]
    summary["state"] = flow_state(states, running=owner is not None)
    summary["failed_step"] = next(
        (
            record["step"]
            for record in records[1:]
            if record.get("state") in FAILED_STATES
        ),
        None,
    )
    if summary["failed_step"]:
        summary["failure_cmd"] = _target_cmd("ecc-fe report failure", context)
    # Reading the state succeeded, so the command did: a failed or interrupted
    # flow is reported in the records, not through the exit status.
    return CommandResult.ok(records)


def _stale_step_reasons(
    workspace: dict[str, object], steps: object
) -> dict[str, str]:
    """Name the steps the next run will re-execute, with the reason for each.

    Covers results that no longer match the current inputs, configuration, or
    tools, and steps a previous run already reset for that reason. Read-only.
    """
    entries = [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
    reasons: dict[str, str] = {}
    for step in entries:
        info = step.get("info")
        if isinstance(info, dict) and info.get("stale") is True:
            reasons[str(step.get("name", ""))] = str(info.get("stale_reason", "stale"))
    try:
        for step, reason, _ in stale_steps(workspace, entries):
            if step.get("state") == StateEnum.Success.value:
                reasons[str(step.get("name", ""))] = reason
    except (OSError, ValueError, TypeError, KeyError):
        # Inspection must not fail because staleness cannot be evaluated.
        pass
    ordered = [str(step.get("name", "")) for step in entries]
    return {name: reasons[name] for name in ordered if name in reasons}


def log(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, LogInput)
    try:
        workspace = load_existing_workspace(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [
                error_record(
                    "unknown_step",
                    step=command_input.step,
                    expected=list(_FLOW_STEPS),
                )
            ],
            exit_code=2,
        )
    log_path = _step_log_path(Path(context.workspace_dir), command_input.step)
    if command_input.follow:
        if context.output_mode in {OutputMode.JSON, OutputMode.JSONL}:
            return CommandResult.err(
                [
                    error_record(
                        "follow_requires_text_output",
                        reason="--follow prints the log itself, not records",
                    )
                ],
                exit_code=2,
            )
        live = _live_log_paths(Path(context.workspace_dir), command_input.step)
        if (
            any(path.is_file() for path in live)
            or workspace_lock_owner(context.workspace_dir) is not None
        ):
            _follow_logs(live, command_input.lines, context.workspace_dir)
            return CommandResult(rendered=True)
    if not log_path.is_file():
        return CommandResult.err(
            [
                error_record(
                    "log_not_found",
                    path=str(log_path),
                    remediation_cmd=_target_cmd(
                        f"ecc-fe run --only {command_input.step}"
                        if command_input.step
                        else "ecc-fe run",
                        context,
                    ),
                )
            ]
        )
    lines = _read_log_tail(log_path, command_input.lines)
    content = "\n".join(lines[-command_input.lines :])
    return CommandResult.ok(
        [
            {
                "kind": "log",
                "workspace": context.workspace_dir,
                "step": command_input.step,
                "path": str(log_path),
                "lines": min(command_input.lines, len(lines)),
                "content": content,
            }
        ]
    )


def _live_log_paths(workspace: Path, step: str | None) -> list[Path]:
    """Return the files a running flow or step writes its output to.

    Lint and elaboration stream into the step's report log. Simulation streams
    its build into the step's own log and writes the report log at the end.
    """
    primary = _step_log_path(workspace, step)
    if step is None:
        return [primary]
    return [primary, _step_directory(workspace, step) / "log" / "log.txt"]


def _follow_logs(
    paths: list[Path],
    line_count: int,
    workspace_dir: str,
    *,
    poll_seconds: float = 0.2,
) -> None:
    """Print the end of the newest log, then whatever is appended to any of them.

    Returns once no command holds the workspace and every file has been read
    to its end, so following a finished run prints its tail and stops. A log
    that is rewritten, as when a step starts over, is read from its beginning.
    A header names the file whenever the output switches to another one.
    """

    def size_of(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return -1.0

    decoders = {
        path: codecs.getincrementaldecoder("utf-8")(errors="replace") for path in paths
    }
    positions = {path: size_of(path) for path in paths}
    current = max(paths, key=modified)
    if current.is_file():
        tail = _read_log_tail(current, line_count)
        if tail:
            sys.stdout.write("\n".join(tail) + "\n")
            sys.stdout.flush()
    try:
        while True:
            running = workspace_lock_owner(workspace_dir) is not None
            progressed = False
            for path in paths:
                size = size_of(path)
                if size < positions[path]:
                    positions[path] = 0
                if size <= positions[path]:
                    continue
                with path.open("rb") as stream:
                    stream.seek(positions[path])
                    chunk = stream.read(size - positions[path])
                positions[path] += len(chunk)
                if path != current:
                    current = path
                    sys.stdout.write(f"==> {path} <==\n")
                sys.stdout.write(decoders[path].decode(chunk))
                sys.stdout.flush()
                progressed = True
            if progressed:
                continue
            if not running:
                return
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        return


def _read_log_tail(path: Path, line_count: int) -> list[str]:
    size = path.stat().st_size
    start = max(0, size - _LOG_TAIL_MAX_BYTES)
    with path.open("rb") as stream:
        stream.seek(start)
        content = stream.read(_LOG_TAIL_MAX_BYTES)
    if start:
        _, separator, content = content.partition(b"\n")
        if not separator:
            return []
    return content.decode("utf-8", errors="replace").splitlines()[-line_count:]


def config(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, ConfigInput)
    try:
        workspace = load_existing_workspace(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [
                error_record(
                    "unknown_step",
                    step=command_input.step,
                    expected=list(_FLOW_STEPS),
                )
            ],
            exit_code=2,
        )
    try:
        parameters = read_json_object(Path(workspace["parameters_path"]))
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [failure_record("workspace_unreadable", error, workspace=context.workspace_dir)]
        )
    if command_input.resolved:
        parameters = {
            **parameters,
            **{
                key: value
                for key, value in workspace.items()
                if key not in {"parameters_path", "flow_path", "home_path"}
            },
        }
    records: list[dict[str, object]] = [
        {
            "kind": "config",
            "workspace": context.workspace_dir,
            "step": command_input.step,
            "resolved": command_input.resolved,
            "values": dict(sorted(parameters.items())),
        }
    ]
    try:
        project_config = load_project_config(context.config_dir)
    except (OSError, ValueError) as error:
        return CommandResult.err(
            [
                error_record(
                    "invalid_project_config",
                    workspace=context.workspace_dir,
                    reason=str(error),
                )
            ]
        )
    try:
        run_overrides = _load_run_parameter_overrides(context)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [
                error_record(
                    "invalid_cli_parameter_provenance",
                    workspace=context.workspace_dir,
                    reason=str(error),
                )
            ]
        )
    # The values shown are the ones stored in this workspace or run. A project's
    # current [params] describes future runs, not this one.
    defaults = project_config.defaults if project_config else None
    if context.workspace_mode:
        parameter_items = parameter_records(
            parameters, defaults=defaults, overrides=None, step=command_input.step
        )
    else:
        parameter_items = run_parameter_records(
            parameters,
            defaults=defaults,
            cli_overrides=run_overrides,
            step=command_input.step,
        )
    records[0]["source"] = str(workspace["parameters_path"])
    records[0]["overrides"] = {
        str(item["param"]): item["value"]
        for item in parameter_items
        if item["value"] != item["default"]
    }
    if run_overrides:
        records[0]["run_overrides"] = run_overrides
    if command_input.resolved:
        records.extend(parameter_items)
    if command_input.step:
        records.append(
            {
                "kind": "step_config",
                "step": command_input.step,
                "directory": str(
                    _step_directory(Path(context.workspace_dir), command_input.step)
                ),
            }
        )
    return CommandResult.ok(records)


def _load_run_parameter_overrides(context: CommandContext) -> dict[str, object]:
    if context.workspace_mode:
        return {}
    return load_run_parameter_overrides(context.workspace_dir)


def resource_list(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ResourceListInput)
    try:
        records = ResourceManager().list_resources()
    except ResourceManagerError as error:
        return CommandResult.err([failure_record("resource_failed", error)])
    if command_input.installed_only:
        records = [record for record in records if record.get("installed_version")]
    return CommandResult.ok(records)


def resource_status(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    try:
        manifest = read_resource_manifest(strict=True)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return CommandResult.err(
            [
                failure_record(
                    "invalid_resource_manifest",
                    error,
                    path=str(resource_manifest_path()),
                )
            ]
        )
    try:
        required_resources = _manifest_required_resources(manifest)
    except TypeError as error:
        return CommandResult.err(
            [
                failure_record(
                    "invalid_resource_manifest",
                    error,
                    path=str(resource_manifest_path()),
                )
            ]
        )
    required_set = set(required_resources)
    installed = installed_tool_entries(manifest)
    records: list[dict[str, object]] = []
    failed = False
    for name, entry in installed.items():
        health = tool_entry_health(entry)
        active = entry.get("active", True) is not False
        status = health["status"] if active else "inactive"
        failed |= status != "ok"
        resource_id = f"tool:{name}"
        records.append(
            {
                "kind": "resource_status",
                "resource_id": resource_id,
                "status": status,
                "required": resource_id in required_set,
                "version": entry.get("version"),
                "active": active,
                "managed": entry.get("managed", True),
                "path": entry.get("path"),
                "missing_markers": health["missing_markers"],
            }
        )
    for resource_id in required_resources:
        name = resource_id.removeprefix("tool:")
        if name in installed:
            continue
        failed = True
        records.append(
            {
                "kind": "resource_status",
                "resource_id": resource_id,
                "status": "missing",
                "required": True,
                "version": None,
                "active": False,
                "managed": True,
                "path": None,
                "missing_markers": [],
                "remediation_cmd": invoked("ecc-fe resource install ") + shlex.quote(name),
            }
        )
    if not records:
        records.append(
            {
                "kind": "resource_status",
                "status": "empty",
                "manifest": str(resource_manifest_path()),
                "remediation_cmd": invoked("ecc-fe resource install --required"),
            }
        )
    return CommandResult.err(records) if failed else CommandResult.ok(records)


def _manifest_required_resources(manifest: dict[str, object]) -> tuple[str, ...]:
    raw = manifest.get("required_resources", [])
    if not isinstance(raw, list):
        raise TypeError("resource manifest required_resources field must be an array")
    resources: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not value:
            continue
        normalized = value if value.startswith("tool:") else f"tool:{value}"
        if normalized not in resources:
            resources.append(normalized)
    return tuple(resources)


def resource_install(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ResourceInstallInput)
    if command_input.required == bool(command_input.resource_id):
        return CommandResult.err(
            [
                error_record(
                    "invalid_resource_selection",
                    reason="specify exactly one resource id or --required",
                )
            ],
            exit_code=2,
        )
    if command_input.required and command_input.version:
        return CommandResult.err(
            [
                error_record(
                    "invalid_resource_selection",
                    reason="--version cannot be combined with --required",
                )
            ],
            exit_code=2,
        )
    manager = ResourceManager()
    try:
        records = (
            manager.install_required(progress=_print_resource_progress)
            if command_input.required
            else manager.install(
                str(command_input.resource_id),
                version=command_input.version,
                progress=_print_resource_progress,
            )
        )
    except ResourceManagerError as error:
        return CommandResult.err(
            [
                failure_record(
                    "resource_install_failed",
                    error,
                    resource_id=command_input.resource_id,
                )
            ]
        )
    activate_managed_resources()
    return CommandResult.ok(records)


def resource_env(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, ResourceEnvInput)
    env = dict(os.environ)
    activate_managed_resources(env)
    selected = {key: env[key] for key in _RESOURCE_ENV_KEYS if env.get(key)}
    if command_input.shell not in _SHELLS:
        return CommandResult.err(
            [
                error_record(
                    "unsupported_shell",
                    shell=command_input.shell,
                    expected=list(_SHELLS),
                )
            ],
            exit_code=2,
        )
    if command_input.output_mode.value == "text":
        content = "\n".join(
            _shell_assignment(key, value, command_input.shell)
            for key, value in selected.items()
        )
        return CommandResult.ok(
            [{"kind": "resource_env", "shell": command_input.shell, "content": content}]
        )
    return CommandResult.ok(
        [{"kind": "resource_env", "shell": command_input.shell, "values": selected}]
    )


_SHELLS = ("sh", "bash", "zsh", "fish")


def _target_cmd(command: str, context: CommandContext) -> str:
    return disclosure_cmd(
        command,
        context.workspace_dir if context.workspace_mode else None,
        project=context.project,
        run_id=context.run_id,
    )


def _init_target_cmd(context: CommandContext) -> str:
    if context.workspace_mode:
        return disclosure_cmd("ecc-fe init", context.workspace_dir)
    target = context.project or context.project_dir
    return invoked("ecc-fe init ") + shlex.quote(target)


def _workspace_not_found(context: CommandContext) -> CommandResult:
    directory = context.workspace_dir
    return CommandResult.err(
        [
            error_record(
                "workspace_not_found",
                workspace=directory,
                project=context.project_dir if not context.workspace_mode else None,
                run=None
                if context.workspace_mode
                else (context.run_id or "default"),
                remediation_cmd=(
                    _target_cmd("ecc-fe run", context)
                    if not context.workspace_mode
                    else disclosure_cmd("ecc-fe init", directory)
                ),
            )
        ]
    )


def _flow_step_names(context: CommandContext) -> tuple[str, ...]:
    """Return the steps of the selected flow: `sim` needs a CPU or a testbench.

    Reads the run, or the project template when the run does not exist yet;
    with neither, every known step.
    """
    for directory in (context.workspace_dir, context.template_dir):
        if not directory:
            continue
        try:
            flow = read_json_object(Path(directory) / "home" / "flow.json")
        except (OSError, TypeError, ValueError):
            continue
        names = tuple(
            str(step.get("name"))
            for step in flow.get("steps", [])
            if isinstance(step, dict) and step.get("name") in _FLOW_STEPS
        )
        if names:
            return names
    return tuple(_FLOW_STEPS)


def _is_rtl_design(context: CommandContext) -> bool:
    for directory in (context.workspace_dir, context.template_dir):
        if not directory:
            continue
        try:
            parameters = read_json_object(Path(directory) / "home" / "parameters.json")
        except (OSError, TypeError, ValueError):
            continue
        return str(parameters.get("frontend_design_kind", "")).strip() == "generic_rtl"
    return False


def _step_outside_flow(
    selection: "_RunSelection", context: CommandContext
) -> CommandResult | None:
    """Refuse a selector that names a step this design's flow does not have."""
    flow_steps = _flow_step_names(context)
    for step in (selection.only, selection.from_step, selection.to_step):
        if step and step not in flow_steps:
            return CommandResult.err(
                [
                    error_record(
                        "step_not_in_flow",
                        step=step,
                        expected=list(flow_steps),
                        **_step_outside_reason(step, context),
                    )
                ],
                exit_code=2,
            )
    return None


def _step_outside_reason(step: str, context: CommandContext) -> dict[str, object]:
    """Say why a run lacks a step, and what to do when a new run would have it."""
    fields: dict[str, object] = {"reason": "this design's flow does not include the step"}
    if context.workspace_mode or not context.template_dir:
        return fields
    try:
        if not (Path(context.workspace_dir) / "home" / "flow.json").is_file():
            return fields
        template = read_json_object(Path(context.template_dir) / "home" / "parameters.json")
        config = load_project_config(context.config_dir)
        steps = [name for name, _ in profile_flow_steps(next_run_parameters(template, config))]
    except (OSError, TypeError, ValueError):
        return fields
    if step in steps:
        # A run keeps the flow it was created with; the project has moved on.
        fields["reason"] = (
            f"run {context.run_id or 'default'} was created before the project's "
            f"settings added the {step} step; a new run has it"
        )
        fields["new_run_cmd"] = disclosure_cmd(
            "ecc-fe run --run-id <new-run>", project=context.project
        )
    return fields


# Verilator 5 introduced --timing, which the simulation build relies on.
_MINIMUM_TOOL_VERSIONS: dict[str, tuple[int, ...]] = {"verilator": (5, 0)}


def _version_tuple(version: str | None) -> tuple[int, ...] | None:
    """Return the first dotted number in a tool's version line."""
    match = re.search(r"(\d+)\.(\d+)", version or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _doctor_requirements(
    step: str | None,
    flow_steps: tuple[str, ...] | None = None,
) -> tuple[tuple[str, str, tuple[str, ...], bool], ...]:
    checks = {
        "slang": (
            "slang",
            "Slang",
            tuple(filter(None, (os.getenv("ECOS_SLANG"), "slang"))),
            True,
        ),
        "verilator": (
            "verilator",
            "Verilator",
            tuple(filter(None, (os.getenv("ECOS_VERILATOR"), "verilator"))),
            True,
        ),
        "cxx": ("cxx", "Host C++ compiler", ("c++", "g++", "clang++"), True),
        "make": ("make", "Make", ("make",), True),
        "riscv": (
            "riscv",
            "RISC-V GCC",
            tuple(
                filter(
                    None,
                    (
                        f"{os.getenv('RISCV_PREFIX', '')}gcc"
                        if os.getenv("RISCV_PREFIX")
                        else "",
                        "riscv64-unknown-elf-gcc",
                    ),
                )
            ),
            True,
        ),
        "yosys": ("yosys", "Yosys", ("yosys",), False),
    }
    definition = find_step_definition(step)
    if definition is not None:
        names: tuple[str, ...] = definition.doctor_checks
    else:
        # Only what the steps of this flow need; Yosys is always optional.
        selected = [
            find_step_definition(name) for name in (flow_steps or tuple(_FLOW_STEPS))
        ]
        needed = {
            check
            for item in selected
            if item is not None
            for check in item.doctor_checks
        }
        names = tuple(
            name
            for name in ("slang", "verilator", "cxx", "make", "riscv")
            if name in needed
        ) + ("yosys",)
    return tuple(checks[name] for name in names)


def _find_executable(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.parent != Path(".") and path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
        found = shutil.which(candidate)
        if found:
            return str(Path(found).resolve())
    return None


def _doctor_remediation(
    check_id: str, executable: str | None
) -> tuple[str | None, str | None]:
    if executable:
        return None, None
    resource_commands = {
        "slang": invoked("ecc-fe resource install slang"),
        "verilator": invoked("ecc-fe resource install verilator"),
        "riscv": invoked("ecc-fe resource install riscv-toolchain"),
        "yosys": invoked("ecc-fe resource install yosys"),
    }
    system_hints = {
        "cxx": "Install a host C++ compiler and ensure c++ is on PATH.",
        "make": "Install GNU Make and ensure make is on PATH.",
    }
    return resource_commands.get(check_id), system_hints.get(check_id)


def _tool_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0][:240] if output else None


def _step_directory(workspace: Path, step: str) -> Path:
    return step_directory(workspace, step)


def _step_log_path(workspace: Path, step: str | None) -> Path:
    if step is None:
        return workspace / "log" / "log.txt"
    return _step_directory(workspace, step) / "report" / "log.txt"


def _shell_assignment(key: str, value: str, shell: str) -> str:
    quoted = shlex.quote(value)
    if shell == "fish":
        return f"set -gx {key} {quoted};"
    return f"export {key}={quoted}"


def _print_resource_progress(record: dict[str, object]) -> None:
    resource_id = str(record.get("resource_id", "resource"))
    phase = str(record.get("phase", "working"))
    progress = record.get("progress")
    if isinstance(progress, (int, float)) and phase == "downloading":
        label = f" {round(progress * 100):d}%"
    else:
        label = ""
    print(f"{resource_id}: {phase}{label}", file=sys.stderr, flush=True)
