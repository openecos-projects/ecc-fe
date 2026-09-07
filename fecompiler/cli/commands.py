from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from fecompiler.application.workspace_service import workspace_application
from fecompiler.catalog import check_catalog_contracts
from fecompiler.cli.core.inputs import (
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
from fecompiler.cli.core.records import disclosure_cmd, error_record, normalize_state
from fecompiler.cli.core.types import CommandContext, CommandResult
from fecompiler.cli.project.config import (
    ProjectConfigError,
    apply_project_overrides,
    create_project_config,
    load_project_config,
    pending_project_overrides,
    project_config_path,
)
from fecompiler.cli.project.params import parameter_records
from fecompiler.cli.resource_manager import ResourceManager, ResourceManagerError
from fecompiler.cli.workspace_access import (
    load_existing_workspace,
    read_json_object,
    workspace_markers,
)
from fecompiler.resources import (
    activate_managed_resources,
    installed_tool_entries,
    read_resource_manifest,
    resource_manifest_path,
    tool_entry_health,
)

_FLOW_STEPS = ("prepare", "review", "elab", "lint", "sim")
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
    custom_cpu_input = command_input.cpu_filelist or command_input.rtl
    core_id = command_input.core_id or (
        "custom-filelist" if custom_cpu_input else "picorv32"
    )
    request: dict[str, object] = {
        "directory": context.workspace_dir,
        "core_id": core_id,
        "parameters": {
            "Design": command_input.design or Path(context.workspace_dir).name,
            "Top module": command_input.top,
        },
    }
    if command_input.rtl:
        request["cpu_rtl_files"] = [command_input.rtl]
        request["cpu_top_module"] = command_input.top
    for key, value in (
        ("cpu_filelist", command_input.cpu_filelist),
        ("soc_filelist", command_input.soc_filelist),
        ("soc_harness_id", command_input.soc_harness_id),
    ):
        if value:
            request[key] = value
    result = workspace_application.execute_payload(
        "create", request, base_dir=Path.cwd()
    )
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
        "data": result.data,
        "messages": result.message,
        "config": config_path,
        "next_cmd": disclosure_cmd("ecc-fe doctor", context.workspace_dir),
    }
    return (
        CommandResult.ok([record])
        if result.response == "success"
        else CommandResult.err([record])
    )


def run_flow(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, RunInput)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [error_record(f"Unknown flow step: {command_input.step}")]
        )
    try:
        workspace = load_existing_workspace(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context.workspace_dir)
    try:
        project_config, pending = pending_project_overrides(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [
                error_record(
                    "invalid_project_config",
                    workspace=context.workspace_dir,
                    reason=str(error),
                )
            ]
        )
    if pending:
        reset = workspace_application.execute_payload(
            "reset-flow",
            {"directory": context.workspace_dir},
            base_dir=Path.cwd(),
        )
        if reset.response != "success":
            return CommandResult.err(
                [
                    error_record(
                        "config_sync_failed",
                        workspace=context.workspace_dir,
                        changed=pending,
                        messages=reset.message,
                    )
                ]
            )
    try:
        project_config, changed = apply_project_overrides(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [
                error_record(
                    "config_sync_failed",
                    workspace=context.workspace_dir,
                    reason=str(error),
                )
            ]
        )
    command = "run-step" if command_input.step else "run-flow"
    payload: dict[str, object] = {
        "directory": context.workspace_dir,
        "rerun": command_input.rerun,
    }
    if command_input.step:
        payload["step"] = command_input.step
    result = workspace_application.execute_payload(
        command, payload, base_dir=Path.cwd()
    )
    record = {
        "kind": "run",
        "status": result.response,
        "workspace": context.workspace_dir,
        "step": command_input.step,
        "data": result.data,
        "messages": result.message,
        "config": str(project_config.path) if project_config else None,
        "config_changes": changed,
        "status_cmd": disclosure_cmd("ecc-fe status", context.workspace_dir),
        "log_cmd": disclosure_cmd(
            f"ecc-fe log --step {command_input.step}"
            if command_input.step
            else "ecc-fe log",
            context.workspace_dir,
        ),
    }
    return (
        CommandResult.ok([record])
        if result.response == "success"
        else CommandResult.err([record])
    )


def doctor(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, DoctorInput)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [
                error_record(
                    f"Unknown flow step: {command_input.step}",
                    expected=list(_FLOW_STEPS),
                )
            ]
        )

    records: list[dict[str, object]] = []
    relevant = _doctor_requirements(command_input.step)
    for check_id, label, candidates, required in relevant:
        path = _find_executable(candidates)
        version = _tool_version(path) if path else None
        remediation_cmd, remediation = _doctor_remediation(check_id, path)
        records.append(
            {
                "kind": "doctor_check",
                "check": check_id,
                "label": label,
                "status": "pass" if path else "fail" if required else "attention",
                "required": required,
                "path": path,
                "version": version,
                "remediation_cmd": remediation_cmd,
                "remediation": remediation,
            }
        )

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
            else "ecc-fe workspace catalog-check --json",
        }
    )

    workspace_expected = command_input.workspace is not None
    workspace_error: str | None = None
    try:
        workspace = load_existing_workspace(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        workspace = None
        workspace_error = str(error)
    if workspace_expected or any(
        path.exists() for path in workspace_markers(context.workspace_dir)
    ):
        records.append(
            {
                "kind": "doctor_check",
                "check": "workspace",
                "label": "Frontend workspace",
                "status": "pass" if workspace else "fail",
                "required": workspace_expected,
                "path": context.workspace_dir,
                "error": workspace_error,
                "remediation_cmd": None
                if workspace
                else disclosure_cmd("ecc-fe init", context.workspace_dir),
            }
        )
        config_status = "missing"
        config_error: str | None = None
        try:
            config_status = (
                "pass" if load_project_config(context.workspace_dir) else "missing"
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
                "path": str(project_config_path(context.workspace_dir)),
                "error": config_error,
                "remediation_cmd": None
                if config_status == "pass"
                else disclosure_cmd("ecc-fe param list --all", context.workspace_dir),
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
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context.workspace_dir)
    try:
        flow = read_json_object(Path(workspace["flow_path"]))
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    records: list[dict[str, object]] = [
        {
            "kind": "workspace",
            "workspace": context.workspace_dir,
            "design": workspace.get("design", ""),
            "top_module": workspace.get("top_module", ""),
        }
    ]
    steps = flow.get("steps", []) if isinstance(flow, dict) else []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        records.append(
            {
                "kind": "step",
                "index": index,
                "step": str(step.get("name", "")),
                "tool": str(step.get("tool", "")),
                "state": normalize_state(step.get("state")),
                "runtime": step.get("runtime", ""),
                "peak_memory_mb": step.get("peak memory (mb)", 0),
            }
        )
    failed = any(record.get("state") in {"incomplete", "invalid"} for record in records)
    return CommandResult.err(records) if failed else CommandResult.ok(records)


def log(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, LogInput)
    try:
        workspace = load_existing_workspace(context.workspace_dir)
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context.workspace_dir)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [error_record(f"Unknown flow step: {command_input.step}")]
        )
    log_path = _step_log_path(Path(context.workspace_dir), command_input.step)
    if not log_path.is_file():
        return CommandResult.err(
            [
                error_record(
                    "Log file was not found",
                    path=str(log_path),
                    remediation_cmd=disclosure_cmd(
                        f"ecc-fe workspace run-step --step {command_input.step}"
                        if command_input.step
                        else "ecc-fe workspace run-flow",
                        context.workspace_dir,
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
            [error_record(str(error), workspace=context.workspace_dir)]
        )
    if workspace is None:
        return _workspace_not_found(context.workspace_dir)
    if command_input.step and command_input.step not in _FLOW_STEPS:
        return CommandResult.err(
            [error_record(f"Unknown flow step: {command_input.step}")]
        )
    try:
        parameters = read_json_object(Path(workspace["parameters_path"]))
    except (OSError, TypeError, ValueError) as error:
        return CommandResult.err(
            [error_record(str(error), workspace=context.workspace_dir)]
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
        project_config = load_project_config(context.workspace_dir)
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
    records[0]["source"] = (
        str(project_config.path)
        if project_config
        else str(workspace["parameters_path"])
    )
    records[0]["overrides"] = project_config.overrides if project_config else {}
    if command_input.resolved:
        records.extend(
            parameter_records(
                parameters,
                defaults=project_config.defaults if project_config else None,
                overrides=project_config.overrides if project_config else None,
                step=command_input.step,
            )
        )
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


def resource_list(
    command_input: CommandInput, context: CommandContext
) -> CommandResult:
    assert isinstance(command_input, ResourceListInput)
    try:
        records = ResourceManager().list_resources()
    except ResourceManagerError as error:
        return CommandResult.err([error_record(str(error))])
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
            [error_record(str(error), path=str(resource_manifest_path()))]
        )
    try:
        required_resources = _manifest_required_resources(manifest)
    except TypeError as error:
        return CommandResult.err(
            [error_record(str(error), path=str(resource_manifest_path()))]
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
                "remediation_cmd": f"ecc-fe resource install {shlex.quote(name)}",
            }
        )
    if not records:
        records.append(
            {
                "kind": "resource_status",
                "status": "empty",
                "manifest": str(resource_manifest_path()),
                "remediation_cmd": "ecc-fe resource install --required",
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
            [error_record("Specify exactly one resource id or --required")], exit_code=2
        )
    if command_input.required and command_input.version:
        return CommandResult.err(
            [error_record("--version cannot be combined with --required")], exit_code=2
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
            [error_record(str(error), resource_id=command_input.resource_id)]
        )
    activate_managed_resources()
    return CommandResult.ok(records)


def resource_env(command_input: CommandInput, context: CommandContext) -> CommandResult:
    assert isinstance(command_input, ResourceEnvInput)
    env = dict(os.environ)
    activate_managed_resources(env)
    selected = {key: env[key] for key in _RESOURCE_ENV_KEYS if env.get(key)}
    if command_input.shell not in {"sh", "bash", "zsh", "fish"}:
        return CommandResult.err(
            [error_record(f"Unsupported shell: {command_input.shell}")], exit_code=2
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


def _workspace_not_found(directory: str) -> CommandResult:
    return CommandResult.err(
        [
            error_record(
                "Frontend workspace was not found",
                workspace=directory,
                remediation_cmd=disclosure_cmd("ecc-fe init", directory),
            )
        ]
    )


def _doctor_requirements(
    step: str | None,
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
    if step in {"prepare", "review"}:
        names: tuple[str, ...] = ()
    elif step == "elab":
        names = ("slang",)
    elif step == "lint":
        names = ("verilator",)
    elif step == "sim":
        names = ("verilator", "cxx", "make", "riscv")
    else:
        names = ("slang", "verilator", "cxx", "make", "riscv", "yosys")
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
        "slang": "ecc-fe resource install slang",
        "verilator": "ecc-fe resource install verilator",
        "riscv": "ecc-fe resource install riscv-toolchain",
        "yosys": "ecc-fe resource install yosys",
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
    tool = {
        "prepare": "fe",
        "review": "fe",
        "elab": "slang",
        "lint": "verilator",
        "sim": "verilator",
    }[step]
    return workspace / f"{step}_{tool}"


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
