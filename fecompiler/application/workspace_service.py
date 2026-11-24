"""Workspace application service and ECOS Studio CLI compatibility layer.

The application operations in this module are shared by command-line and
runtime transports. The command shape remains compatible with ECOS Studio:

    ecc-fe workspace create --input-json request.json --json
    ecc-fe workspace load --directory <workspace> --json
    ecc-fe workspace run-flow --directory <workspace> --json
    ecc-fe workspace run-step --directory <workspace> --step sim --json
    ecc-fe workspace get-info --directory <workspace> --step sim --id subflow --json
    ecc-fe workspace get-home --directory <workspace> --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from fecompiler.allflow.profile import CPU_CORE, GENERIC_RTL, normalize_frontend_design_kind
from fecompiler.allflow.steps import input_preparation_step
from fecompiler.application.create_request import (
    _apply_catalog_defaults,
    _apply_core_sim_defaults_to,
    _catalog_config_from_args,
    _create_request_from_args,
    _frontend_catalog_config_from_create_request,
    _materialized_cpu_filelist,
    _normalize_catalog_config_paths,
    _normalize_create_request,
    _normalize_parameters,
    _validate_create_request_paths,
    _validate_generic_create_request,
)
from fecompiler.application.events import (
    WorkspaceEventSink,
    _FrontendSubflowObserver,
    _emit_event,
    runtime_operation_marker,
    workspace_event_sink,
)
from fecompiler.application.result import CliResult, WorkspaceCliError, _command_to_cmd, _exit_code
from fecompiler.application.sim_config import (
    _apply_default_soc_runtime_options,
    _apply_workspace_create_test_suite_defaults,
    _prepare_step_run,
    _repair_workspace_sim_defaults,
)
from fecompiler.application.step_detail import (
    _build_step_info,
    _clear_frontend_step_details,
    _failure_messages,
    _failure_payload,
    _remove_frontend_step_detail,
    _step_report_payload,
    _write_frontend_step_detail,
)
from fecompiler.application.values import (
    _normalize_bool,
    _normalize_str_list,
    _optional_text,
    _path_is_relative_to,
    _resolve_path,
)
from fecompiler.catalog import catalog_payload, check_catalog_contracts, validate_frontend_config
from fecompiler.cli.workspace_typer import WorkspaceTyperHandlers, build_typer_app as build_workspace_typer_app
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import (
    CreateWorkspaceData,
    UnsupportedWorkspaceSchemaError,
    create_workspace,
    load_workspace,
)
from fecompiler.engine.flow import EngineFlow
from fecompiler.tools.common.rtl_inputs import prepared_inputs_current
from fecompiler.utility.program import program_name
from fecompiler.utility.workspace_lock import WorkspaceBusyError, workspace_lock

try:
    import click
    import typer
except ImportError:
    click = None
    typer = None


class WorkspaceApplicationService:
    """Execute workspace operations independently of their transport."""

    def call(self, command: str, callback: Callable[[], CliResult]) -> CliResult:
        try:
            return callback()
        except WorkspaceCliError as exc:
            return exc.result
        except Exception as exc:
            return CliResult(
                cmd=_command_to_cmd(command),
                response="error",
                data={},
                message=[str(exc)],
            )

    def execute_namespace(
        self,
        command: str,
        args: argparse.Namespace,
    ) -> CliResult:
        args.command = command
        return self.call(command, lambda: _dispatch(args))

    def execute_payload(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        base_dir: str | Path | None = None,
        event_sink: WorkspaceEventSink | None = None,
        runtime_operation: dict[str, Any] | None = None,
    ) -> CliResult:
        request = dict(payload or {})
        resolved_base_dir = Path(base_dir or Path.cwd()).expanduser().resolve()
        normalized_command = _normalize_application_command(command)
        # A caller may already have installed a sink around this call; only an
        # explicit one replaces it.
        sink_scope = (
            workspace_event_sink(event_sink) if event_sink is not None else nullcontext()
        )
        with sink_scope, runtime_operation_marker(runtime_operation):
            return self.call(
                normalized_command,
                lambda: _dispatch_payload(
                    normalized_command,
                    request,
                    resolved_base_dir,
                ),
            )


workspace_application = WorkspaceApplicationService()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{program_name()} workspace",
        description="Manage fecompiler workspaces with ECOS Studio CLI-compatible JSON responses.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a frontend workspace")
    _add_json_flag(create)
    create.add_argument("--input-json", default="", help="Workspace create request JSON path, or '-' for stdin")
    create.add_argument("--directory", default="", help="Workspace directory")
    create.add_argument("--design", default="", help="Design name")
    create.add_argument("--top", default="", help="Top module name")
    create.add_argument("--clock", default="", help="Clock port name")
    create.add_argument("--freq", type=float, default=None, help="Clock frequency in MHz")
    create.add_argument("--origin-verilog", default="")
    create.add_argument("--filelist", default="")
    create.add_argument(
        "--design-kind",
        default="",
        choices=(CPU_CORE, GENERIC_RTL),
        help="Frontend design kind",
    )
    create.add_argument("--cpu-filelist", default="")
    create.add_argument("--cpu-rtl", action="append", default=[], help="CPU RTL source path; repeatable")
    create.add_argument("--cpu-top-module", default="", help="User CPU top module name")
    create.add_argument("--soc-filelist", default="")
    create.add_argument("--testbench", default="")
    create.add_argument("--sim-cpp", action="append", default=[])
    create.add_argument("--sim-cflag", action="append", default=[])
    create.add_argument("--sim-ldflag", action="append", default=[])
    create.add_argument("--sim-arg", action="append", default=[])
    create.add_argument("--sim-image", action="append", default=[])
    create.add_argument("--sim-program", action="append", default=[])
    create.add_argument("--sim-program-source", action="append", default=[])
    create.add_argument("--sim-all-tests", action="store_true")
    create.add_argument("--sim-build-all-programs", action="store_true")
    create.add_argument("--sim-tests-dir", default="")
    create.add_argument("--sim-programs-dir", default="")
    create.add_argument("--sim-tests-out-dir", default="")
    create.add_argument("--sim-soc-root", default="")
    create.add_argument("--sim-build-test-script", default="")
    create.add_argument("--rtl", action="append", default=[], help="RTL source path; repeatable")
    create.add_argument("--core-id", default="")
    create.add_argument("--soc-variant", default="")
    create.add_argument("--soc-harness-id", default="")

    catalog = subparsers.add_parser("catalog-list", help="List frontend core/SoC/toolchain/test catalogs")
    _add_json_flag(catalog)

    catalog_check = subparsers.add_parser("catalog-check", help="Check frontend catalog adapter contracts")
    _add_json_flag(catalog_check)

    validate = subparsers.add_parser("validate-config", help="Validate a frontend catalog configuration")
    _add_json_flag(validate)
    validate.add_argument("--input-json", default="", help="Frontend config JSON path, or '-' for stdin")
    validate.add_argument("--core-id", default="")
    validate.add_argument("--soc-harness-id", default="")
    validate.add_argument("--toolchain-id", default="")
    validate.add_argument("--test-suite-id", default="")
    validate.add_argument("--cpu-filelist", default="")
    validate.add_argument("--cpu-rtl", action="append", default=[], help="CPU RTL source path; repeatable")
    validate.add_argument("--cpu-top-module", default="", help="User CPU top module name")

    load = subparsers.add_parser("load", help="Load an existing frontend workspace")
    _add_json_flag(load)
    load.add_argument("--directory", required=True)

    run_flow = subparsers.add_parser("run-flow", help="Run the full frontend flow")
    _add_json_flag(run_flow)
    run_flow.add_argument("--directory", required=True)
    run_flow.add_argument("--rerun", action="store_true")
    run_flow.add_argument(
        "--from",
        dest="from_step",
        default="",
        help="Re-execute a step and its suffix",
    )
    run_flow.add_argument(
        "--to",
        dest="to_step",
        default="",
        help="Stop after this step",
    )

    run_step = subparsers.add_parser("run-step", help="Run one frontend flow step")
    _add_json_flag(run_step)
    run_step.add_argument("--directory", required=True)
    run_step.add_argument("--step", required=True)
    run_step.add_argument("--rerun", action="store_true")
    run_step.add_argument("--sim-test-suite", default="")
    run_step.add_argument("--sim-cpu-test-mode", default="selected")
    run_step.add_argument("--sim-cpu-test-case", action="append", default=[])
    run_step.add_argument("--sim-compile-preset", default="")
    run_step.add_argument("--sim-compile-opt-level", default="")
    run_step.add_argument("--sim-compile-march", default="")
    run_step.add_argument("--sim-compile-mabi", default="")
    run_step.add_argument("--sim-compile-extra-cflag", action="append", default=[])
    run_step.add_argument("--sim-coremark-iterations", type=int, default=None)
    run_step.add_argument("--sim-coremark-total-data-size", type=int, default=None)
    run_step.add_argument("--sim-coremark-max-cycles", type=int, default=None)
    run_step.add_argument("--sim-coremark-has-float", default="")

    get_info = subparsers.add_parser("get-info", help="Get step information")
    _add_json_flag(get_info)
    get_info.add_argument("--directory", required=True)
    get_info.add_argument("--step", required=True)
    get_info.add_argument("--id", required=True)

    get_home = subparsers.add_parser("get-home", help="Get workspace home.json")
    _add_json_flag(get_home)
    get_home.add_argument("--directory", required=True)

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if _typer_available():
        return _run_typer(raw_argv)
    return _run_argparse(raw_argv)


def _run_argparse(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    return _run_command(
        str(args.command),
        bool(getattr(args, "json", False)),
        lambda: _dispatch(args),
    )


def _typer_available() -> bool:
    return click is not None and typer is not None


def _load_typer_modules() -> tuple[Any, Any]:
    if click is None or typer is None:
        raise ImportError("Typer is required for the structured workspace CLI")

    return click, typer


def _run_typer(argv: Sequence[str]) -> int:
    click, typer = _load_typer_modules()
    command = typer.main.get_command(build_typer_app(typer))
    try:
        result = command.main(
            args=list(argv),
            prog_name=f"{program_name()} workspace",
            standalone_mode=False,
        )
    except click.exceptions.Exit as exc:
        return int(exc.exit_code or 0)
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code or 1)
    return int(result or 0)


def _call_command(command: str, callback: Callable[[], CliResult]) -> CliResult:
    return workspace_application.call(command, callback)


def _run_command(command: str, json_output: bool, callback: Callable[[], CliResult]) -> int:
    result = _call_command(command, callback)
    _render_result(result, json_output=json_output)
    return _exit_code(result.response)


def build_typer_app(typer_module: Any | None = None) -> Any:
    """Build the Typer workspace command app without making Typer a hard import.

    Kept as a public compatibility wrapper for tests and external callers.
    The Typer command definitions live in ``workspace_typer.py``.
    """
    if typer_module is None:
        _, typer_module = _load_typer_modules()
    return build_workspace_typer_app(typer_module, _typer_handlers())


def _typer_handlers() -> WorkspaceTyperHandlers:
    return WorkspaceTyperHandlers(
        call_command=_call_command,
        render_result=_render_result,
        exit_code=_exit_code,
        create=_create,
        catalog_list=_catalog_list,
        catalog_check=_catalog_check,
        validate_config=_validate_config,
        load=_load,
        run_flow=_run_flow,
        run_step=_run_step,
        get_info=_get_info,
        get_home=_get_home,
    )


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def _dispatch(args: argparse.Namespace) -> CliResult:
    command = str(args.command)
    if command == "create":
        return _create(args)
    if command == "catalog-list":
        return _catalog_list()
    if command == "catalog-check":
        return _catalog_check()
    if command == "validate-config":
        return _validate_config(args)
    if command == "load":
        return _load(args)
    if command == "run-flow":
        return _exclusively(args.directory, "rtl2gds", lambda: _run_flow(args))
    if command == "run-step":
        return _exclusively(args.directory, "run_step", lambda: _run_step(args))
    if command == "get-info":
        return _get_info(args)
    if command == "get-home":
        return _get_home(args)
    raise WorkspaceCliError("workspace", "error", f"unknown workspace command: {command}")


def _exclusively(
    directory: Any, cmd: str, action: Callable[[], CliResult]
) -> CliResult:
    """Run a mutating workspace operation while holding its cross-process lock."""
    workspace_dir = _resolve_path(directory, Path.cwd())
    if not workspace_dir:
        return action()
    try:
        with workspace_lock(workspace_dir):
            return action()
    except WorkspaceBusyError as error:
        raise WorkspaceCliError(
            cmd,
            "failed",
            str(error),
            data={
                "directory": workspace_dir,
                "error": "workspace_busy",
                "owner_pid": error.owner.get("pid"),
            },
        ) from None


def _catalog_list() -> CliResult:
    return CliResult(
        cmd="catalog_list",
        response="success",
        data=catalog_payload(),
        message=["frontend catalog list loaded"],
    )


def _catalog_check() -> CliResult:
    result = check_catalog_contracts()
    return CliResult(
        cmd="catalog_check",
        response="success" if result.ok else "failed",
        data=result.to_dict(),
        message=[result.summary],
    )


def _validate_config(args: argparse.Namespace) -> CliResult:
    request, base_dir = _catalog_config_from_args(args)
    return _validate_config_request(request, base_dir)


def _validate_config_request(request: dict[str, Any], base_dir: Path) -> CliResult:
    normalized = _normalize_catalog_config_paths(request, base_dir)
    with _materialized_cpu_filelist(
        normalized,
        "validate_frontend_config",
    ) as (validation_input, cpu_rtl_files):
        result = validate_frontend_config(validation_input)
    if cpu_rtl_files:
        result.normalized["cpu_filelist"] = ""
        result.normalized["cpu_rtl_files"] = cpu_rtl_files
    response = "success" if result.ok else "failed"
    return CliResult(
        cmd="validate_frontend_config",
        response=response,
        data=result.to_dict(),
        message=[result.summary],
    )


def _create(args: argparse.Namespace) -> CliResult:
    request, base_dir = _create_request_from_args(args)
    return _create_request(request, base_dir)


def _create_request(request: dict[str, Any], base_dir: Path) -> CliResult:
    normalized = _normalize_create_request(request, base_dir)
    try:
        design_kind = normalize_frontend_design_kind(
            normalized.get("frontend_design_kind")
            or (
                normalized.get("parameters", {}).get("frontend_design_kind")
                if isinstance(normalized.get("parameters"), dict)
                else ""
            )
        )
    except ValueError as exc:
        raise WorkspaceCliError("create_workspace", "failed", str(exc)) from exc
    normalized["frontend_design_kind"] = design_kind

    if design_kind == GENERIC_RTL:
        _validate_generic_create_request(normalized)
        return _create_normalized_workspace(
            normalized,
            design_kind=design_kind,
            catalog_config={},
            cpu_rtl_files=[],
        )

    with _materialized_cpu_filelist(
        normalized,
        "create_workspace",
    ) as (validation_input, cpu_rtl_files):
        validation = validate_frontend_config(
            _frontend_catalog_config_from_create_request(validation_input),
        )
    if not validation.ok:
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            validation.summary,
            data={"validation": validation.to_dict()},
        )
    _apply_catalog_defaults(normalized, validation.normalized)
    if cpu_rtl_files:
        normalized.pop("cpu_filelist", None)
    _apply_default_soc_runtime_options(normalized)
    return _create_normalized_workspace(
        normalized,
        design_kind=design_kind,
        catalog_config=validation.normalized,
        cpu_rtl_files=cpu_rtl_files,
    )


def _create_normalized_workspace(
    normalized: dict[str, Any],
    *,
    design_kind: str,
    catalog_config: dict[str, Any],
    cpu_rtl_files: list[str],
) -> CliResult:
    directory = str(normalized.get("directory", "")).strip()
    if not directory:
        raise WorkspaceCliError("create_workspace", "failed", "missing required field: directory")
    return _exclusively(
        directory,
        "create_workspace",
        lambda: _create_locked_workspace(
            normalized,
            directory=directory,
            design_kind=design_kind,
            catalog_config=catalog_config,
            cpu_rtl_files=cpu_rtl_files,
        ),
    )


def _create_locked_workspace(
    normalized: dict[str, Any],
    *,
    directory: str,
    design_kind: str,
    catalog_config: dict[str, Any],
    cpu_rtl_files: list[str],
) -> CliResult:
    _validate_create_request_paths(normalized)

    parameters = _normalize_parameters(
        normalized.get("parameters", {}),
        default_clock="" if design_kind == GENERIC_RTL else "clk",
    )
    if normalized.get("top_module"):
        parameters["Top module"] = normalized["top_module"]
    parameters.setdefault("Design Tool", "frontend")
    parameters["frontend_design_kind"] = design_kind
    if design_kind == CPU_CORE:
        parameters["frontend_core_id"] = catalog_config["core_id"]
        parameters["cpu_wrapper_id"] = catalog_config["core_id"]
        parameters["cpu_wrapper_contract"] = catalog_config.get("cpu_wrapper_contract", "")
        parameters["cpu_socket_contract"] = catalog_config.get("cpu_socket_contract", "")
        parameters["cpu_wrapper_top"] = catalog_config.get("cpu_wrapper_top", "")
        parameters["required_cpu_top_module"] = catalog_config.get("required_cpu_top_module", "")
        parameters["required_cpu_top_ports"] = catalog_config.get("required_cpu_top_ports", [])
        parameters["required_cpu_top_port_contract"] = catalog_config.get(
            "required_cpu_top_port_contract",
            [],
        )
        parameters["cpu_reset_vector"] = catalog_config.get("required_cpu_reset_vector", "")
        parameters["cpu_supports_difftest"] = bool(catalog_config.get("cpu_supports_difftest", True))
        parameters["core_supported_test_suites"] = catalog_config.get("core_supported_test_suites", [])
        if catalog_config.get("core_sim_program_link_base"):
            parameters["sim_program_link_base"] = catalog_config["core_sim_program_link_base"]
        _apply_core_sim_defaults_to(parameters, catalog_config)
        parameters["soc_harness_id"] = catalog_config["soc_harness_id"]
        parameters["soc_wrapper_id"] = catalog_config["soc_harness_id"]
        parameters["soc_wrapper_contract"] = catalog_config.get("soc_wrapper_contract", "")
        parameters["soc_wrapper_top"] = catalog_config.get("soc_wrapper_top", "")
        parameters["soc_cpu_reset_vector"] = catalog_config.get("soc_cpu_reset_vector", "")
        parameters["soc_default_program_link_base"] = catalog_config.get("soc_default_program_link_base", "")
        parameters["soc_bootloader_payload_link_base"] = catalog_config.get("soc_bootloader_payload_link_base", "")
        parameters["soc_supports_difftest"] = bool(catalog_config.get("soc_supports_difftest", True))
        parameters["soc_supported_test_suites"] = catalog_config.get("soc_supported_test_suites", [])
        parameters["toolchain_id"] = catalog_config["toolchain_id"]
        parameters["test_suite_id"] = catalog_config["test_suite_id"]
        if normalized.get("soc_variant"):
            parameters["soc_variant"] = normalized["soc_variant"]

    spec = CreateWorkspaceData(
        directory=directory,
        frontend_design_kind=design_kind,
        parameters=parameters,
        origin_verilog=str(normalized.get("origin_verilog", "")),
        filelist=str(normalized.get("filelist", "")),
        cpu_filelist=str(normalized.get("cpu_filelist", "")),
        cpu_rtl_files=cpu_rtl_files,
        soc_filelist=str(normalized.get("soc_filelist", "")),
        testbench=str(normalized.get("testbench", "")),
        sim_cpp_sources=_normalize_str_list(normalized.get("sim_cpp_sources", [])),
        sim_cflags=_normalize_str_list(normalized.get("sim_cflags", [])),
        sim_ldflags=_normalize_str_list(normalized.get("sim_ldflags", [])),
        sim_run_args=_normalize_str_list(normalized.get("sim_run_args", [])),
        sim_images=_normalize_str_list(normalized.get("sim_images", [])),
        sim_all_tests=_normalize_bool(normalized.get("sim_all_tests", False)),
        sim_tests_dir=str(normalized.get("sim_tests_dir", "")),
        sim_build_all_programs=_normalize_bool(normalized.get("sim_build_all_programs", False)),
        cpu_supports_difftest=(
            _normalize_bool(normalized.get("cpu_supports_difftest", True))
            if design_kind == CPU_CORE
            else False
        ),
        soc_supports_difftest=(
            _normalize_bool(normalized.get("soc_supports_difftest", True))
            if design_kind == CPU_CORE
            else False
        ),
        core_supported_test_suites=_normalize_str_list(normalized.get("core_supported_test_suites", [])),
        soc_supported_test_suites=_normalize_str_list(normalized.get("soc_supported_test_suites", [])),
        sim_program_names=_normalize_str_list(normalized.get("sim_program_names", [])),
        sim_program_sources=_normalize_str_list(normalized.get("sim_program_sources", [])),
        sim_programs_dir=str(normalized.get("sim_programs_dir", "")),
        sim_tests_out_dir=str(normalized.get("sim_tests_out_dir", "")),
        sim_soc_root=str(normalized.get("sim_soc_root", "")),
        sim_build_test_script=str(normalized.get("sim_build_test_script", "")),
        sim_program_link_base=str(normalized.get("sim_program_link_base", "")),
        sim_compile_preset=str(normalized.get("sim_compile_preset", "")),
        sim_compile_opt_level=str(normalized.get("sim_compile_opt_level", "")),
        sim_compile_march=str(normalized.get("sim_compile_march", "")),
        sim_compile_mabi=str(normalized.get("sim_compile_mabi", "")),
        sim_compile_extra_cflags=_normalize_str_list(normalized.get("sim_compile_extra_cflags", [])),
        sim_coremark_iterations=str(normalized.get("sim_coremark_iterations", "")),
        sim_coremark_total_data_size=str(normalized.get("sim_coremark_total_data_size", "")),
        sim_coremark_max_cycles=str(normalized.get("sim_coremark_max_cycles", "")),
        sim_coremark_has_float=_normalize_bool(normalized.get("sim_coremark_has_float", False)),
        sim_coremark_use_difftest=_normalize_bool(normalized.get("sim_coremark_use_difftest", False)),
        rtl_list=_normalize_str_list(normalized.get("rtl_list", [])),
    )
    try:
        workspace = create_workspace(spec)
    except UnsupportedWorkspaceSchemaError as error:
        raise _unsupported_schema("create_workspace", directory, error) from error
    if workspace is None:
        raise WorkspaceCliError("create_workspace", "failed", f"create frontend workspace failed: {directory}")
    if design_kind == CPU_CORE:
        _repair_workspace_sim_defaults(workspace)
        _apply_workspace_create_test_suite_defaults(workspace, catalog_config["test_suite_id"])

    engine = _build_engine(workspace)
    engine.create_step_workspaces()
    return CliResult(
        cmd="create_workspace",
        response="success",
        data={"directory": workspace["directory"], "workspace_id": workspace["directory"]},
        message=[f"create frontend workspace success: {workspace['directory']}"],
    )


def _load(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(args.directory, cmd="load_workspace")
    repaired = (
        _repair_workspace_sim_defaults(workspace)
        if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) == CPU_CORE
        else False
    )
    recovered = (
        _recover_stale_ongoing(workspace, engine)
        if bool(getattr(args, "recover_stale_ongoing", True))
        else False
    )
    return CliResult(
        cmd="load_workspace",
        response="success",
        data={
            "directory": workspace["directory"],
            "workspace_id": workspace["directory"],
            "recovered_stale_ongoing": recovered,
            "repaired_sim_defaults": repaired,
        },
        message=[
            f"load frontend workspace success: {workspace['directory']}",
            *(
                ["recovered stale frontend running state from a previous interrupted run"]
                if recovered
                else []
            ),
            *(
                ["repaired frontend SoC simulation defaults"]
                if repaired
                else []
            ),
        ],
    )


def _recover_stale_ongoing(workspace: dict[str, Any], engine: EngineFlow) -> bool:
    """Reset steps left running by a dead command.

    A workspace whose lock is held has a live owner, so its running step is
    real and must be left alone.
    """
    try:
        with workspace_lock(workspace["directory"]):
            engine.load()
            return engine.clear_stale_ongoing_states()
    except WorkspaceBusyError:
        return False


def _run_flow(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(args.directory, cmd="rtl2gds")
    if normalize_frontend_design_kind(workspace.get("frontend_design_kind")) == CPU_CORE:
        _repair_workspace_sim_defaults(workspace)
    from_step = str(getattr(args, "from_step", "")).strip()
    step_names = [str(step.name) for step in engine.workspace_steps]
    if from_step and from_step not in step_names:
        raise WorkspaceCliError(
            "rtl2gds",
            "failed",
            f"unknown frontend flow step: {from_step}",
            data={
                "directory": workspace["directory"],
                "step": from_step,
                "valid_steps": step_names,
            },
        )
    to_step = str(getattr(args, "to_step", "") or "").strip()
    if to_step and to_step not in step_names:
        raise WorkspaceCliError(
            "rtl2gds",
            "failed",
            f"unknown frontend flow step: {to_step}",
            data={
                "directory": workspace["directory"],
                "step": to_step,
                "valid_steps": step_names,
            },
        )
    start_index = step_names.index(from_step) if from_step else 0
    end_index = step_names.index(to_step) + 1 if to_step else len(step_names)
    if end_index <= start_index:
        raise WorkspaceCliError(
            "rtl2gds",
            "failed",
            f"frontend flow step {to_step} comes before {from_step}",
            data={
                "directory": workspace["directory"],
                "from_step": from_step,
                "to_step": to_step,
                "valid_steps": step_names,
            },
        )
    if args.rerun:
        engine.clear_states()
        _clear_frontend_step_details(engine)

    json_output = bool(getattr(args, "json", False))
    observer = _FrontendSubflowObserver(
        cmd="rtl2gds",
        directory=workspace["directory"],
        json_output=json_output,
    )
    reports: list[dict[str, Any]] = []
    failed_step = ""
    failed_state = StateEnum.Incomplete
    prepare_refreshed = False
    selected_steps = engine.workspace_steps[start_index:end_index]
    if from_step:
        for workspace_step in selected_steps:
            _remove_frontend_step_detail(workspace_step)
    for workspace_step in selected_steps:
        if not prepare_refreshed and workspace_step.name != input_preparation_step().name:
            prepare_refreshed = _refresh_prepare_if_stale(
                workspace,
                engine,
                workspace_step.name,
                observer=observer,
            )

        _prepare_step_run(workspace, workspace_step.name)

        _emit_event(
            "rtl2gds",
            "started",
            {"directory": workspace["directory"], "step": workspace_step.name, "tool": workspace_step.tool},
            [f"start frontend step {workspace_step.name}: {workspace['directory']}"],
            json_output=json_output,
        )
        state = engine.run_step(
            workspace_step.name,
            rerun=bool(
                args.rerun
                or (from_step and workspace_step.name == from_step)
            ),
            observer=observer,
        )
        report = _step_report_payload(workspace, workspace_step, state)
        report["detail_path"] = _write_frontend_step_detail(
            workspace,
            engine,
            workspace_step,
            state,
        )
        reports.append(report)
        _emit_event(
            "rtl2gds",
            "completed" if state == StateEnum.Success else "failed",
            {"directory": workspace["directory"], **report},
            [f"frontend step {workspace_step.name} {state.value}: {workspace['directory']}"],
            json_output=json_output,
        )
        if state != StateEnum.Success:
            failed_step = workspace_step.name
            failed_state = state
            break

    data: dict[str, Any] = {
        "rerun": bool(args.rerun),
        "from_step": from_step or None,
        "reports": reports,
    }
    if to_step:
        data["to_step"] = to_step
    if failed_step:
        data["failed_step"] = failed_step
        failed_workspace_step = engine.get_workspace_step(failed_step)
        if failed_workspace_step is not None:
            data["failure"] = _failure_payload(workspace, failed_workspace_step, failed_step, failed_state)
        return CliResult(
            cmd="rtl2gds",
            response="failed",
            data=data,
            message=_failure_messages(
                f"run frontend flow failed in step: {failed_step}",
                data.get("failure"),
            ),
        )
    return CliResult(
        cmd="rtl2gds",
        response="success",
        data=data,
        message=[f"run frontend flow success: {workspace['directory']}"],
    )


def _run_step(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(args.directory, cmd="run_step")
    _repair_workspace_sim_defaults(workspace)
    step = str(args.step).strip()
    if not step:
        raise WorkspaceCliError("run_step", "failed", "missing required field: step")
    workspace_step = engine.get_workspace_step(step)
    if workspace_step is None:
        valid_steps = [str(candidate.name) for candidate in engine.workspace_steps]
        raise WorkspaceCliError(
            "run_step",
            "failed",
            f"unknown frontend flow step: {step}",
            data={
                "directory": workspace["directory"],
                "step": step,
                "valid_steps": valid_steps,
            },
        )

    json_output = bool(getattr(args, "json", False))
    observer = _FrontendSubflowObserver(
        cmd="run_step",
        directory=workspace["directory"],
        json_output=json_output,
    )
    _refresh_prepare_if_stale(workspace, engine, step, observer=observer)
    force_rerun = _prepare_step_run(workspace, step, args)

    _emit_event(
        "run_step",
        "started",
        {"directory": workspace["directory"], "step": step},
        [f"start frontend step {step}: {workspace['directory']}"],
        json_output=json_output,
    )
    if args.rerun or force_rerun:
        _remove_frontend_step_detail(workspace_step)
    state = engine.run_step(
        step,
        rerun=bool(args.rerun or force_rerun),
        observer=observer,
    )
    data: dict[str, Any] = {"step": step, "state": state.value, "directory": workspace["directory"]}
    if workspace_step is not None:
        data.update(_step_report_payload(workspace, workspace_step, state))
        data["detail_path"] = _write_frontend_step_detail(
            workspace,
            engine,
            workspace_step,
            state,
        )
    if state != StateEnum.Success:
        data["failure"] = _failure_payload(workspace, workspace_step, step, state)
    phase = "completed" if state == StateEnum.Success else "failed"
    response = "success" if state == StateEnum.Success else "failed"
    message = f"run frontend step {step} {response}: {workspace['directory']}"
    _emit_event("run_step", phase, data, [message], json_output=json_output)
    return CliResult(
        cmd="run_step",
        response=response,
        data=data,
        message=[message] if state == StateEnum.Success else _failure_messages(message, data.get("failure")),
    )


def _refresh_prepare_if_stale(
    workspace: dict[str, Any],
    engine: EngineFlow,
    target_step: str,
    *,
    observer: Any | None = None,
) -> bool:
    prepare_name = input_preparation_step().name
    if target_step == prepare_name or prepared_inputs_current(workspace):
        return False

    prepare_step = engine.get_workspace_step(prepare_name)
    if prepare_step is None:
        return False

    state = engine.run_step(prepare_name, rerun=True, observer=observer)
    if state != StateEnum.Success:
        raise WorkspaceCliError(
            "run_step",
            "failed",
            "prepare inputs are stale and automatic prepare refresh failed",
            data={
                "directory": workspace["directory"],
                "step": target_step,
                "prepare_state": state.value,
            },
        )
    return True


def _get_info(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(
        args.directory, cmd="get_info", create_step_workspaces=False
    )
    _repair_workspace_sim_defaults(workspace)
    step_name = str(args.step).strip()
    info_id = str(args.id).strip()
    response_data = {"step": step_name, "id": info_id, "info": {}}
    step = engine.get_workspace_step(step_name)
    if step is None:
        return CliResult(
            cmd="get_info",
            response="warning",
            data=response_data,
            message=[f"no frontend step found: {step_name}"],
        )

    info = _build_step_info(workspace, engine, step, info_id)
    if not info:
        return CliResult(
            cmd="get_info",
            response="warning",
            data=response_data,
            message=[f"no frontend information for step {step_name}: {workspace['directory']}"],
        )
    response_data["info"] = info
    return CliResult(
        cmd="get_info",
        response="success",
        data=response_data,
        message=[f"get frontend information success: {step_name} - {info_id}"],
    )


def _get_home(args: argparse.Namespace) -> CliResult:
    workspace, _ = _load_runtime(args.directory, cmd="home_page", create_step_workspaces=False)
    home_path = str(workspace.get("home_path", ""))
    if home_path and os.path.exists(home_path):
        return CliResult(
            cmd="home_page",
            response="success",
            data={"path": home_path},
            message=[f"get frontend home page success: {home_path}"],
        )
    return CliResult(
        cmd="home_page",
        response="failed",
        data={},
        message=[f"get frontend home page failed: {home_path}"],
    )


def _load_runtime(
    directory: str,
    *,
    cmd: str,
    create_step_workspaces: bool = True,
) -> tuple[dict[str, Any], EngineFlow]:
    workspace_dir = _resolve_path(directory, Path.cwd())
    try:
        workspace = load_workspace(workspace_dir)
    except UnsupportedWorkspaceSchemaError as error:
        raise _unsupported_schema(cmd, workspace_dir, error) from error
    if workspace is None:
        raise WorkspaceCliError(
            cmd,
            "failed",
            f"load frontend workspace failed: {workspace_dir}",
            data={"directory": workspace_dir},
        )
    engine = _build_engine(workspace)
    if create_step_workspaces:
        engine.create_step_workspaces()
    else:
        engine.load()
    return workspace, engine


def _unsupported_schema(
    cmd: str, directory: str, error: UnsupportedWorkspaceSchemaError
) -> WorkspaceCliError:
    return WorkspaceCliError(
        cmd,
        "failed",
        str(error),
        data={
            "directory": directory,
            "error": "unsupported_workspace_schema",
            "schema_version": error.found,
            "supported_schema_version": error.supported,
        },
    )


def _build_engine(workspace: dict[str, Any]) -> EngineFlow:
    engine = EngineFlow(workspace=workspace)
    if not engine.has_init():
        engine.init_default_steps()
        engine.load()
    return engine


def _normalize_application_command(command: str) -> str:
    normalized = command.strip().replace("_", "-")
    return {
        "catalog-list": "catalog-list",
        "catalog-check": "catalog-check",
        "validate-frontend-config": "validate-config",
        "validate-config": "validate-config",
        "create-workspace": "create",
        "create": "create",
        "load-workspace": "load",
        "load": "load",
        "rtl2gds": "run-flow",
        "run-flow": "run-flow",
        "run-step": "run-step",
        "refresh-config": "refresh-config",
        "sync-config": "sync-config",
        "reset-flow": "reset-flow",
        "get-info": "get-info",
        "home-page": "get-home",
        "get-home": "get-home",
    }.get(normalized, normalized)


def _dispatch_payload(
    command: str,
    payload: dict[str, Any],
    base_dir: Path,
) -> CliResult:
    if command == "catalog-list":
        return _catalog_list()
    if command == "catalog-check":
        return _catalog_check()
    if command == "validate-config":
        return _validate_config_request(payload, base_dir)
    if command == "create":
        return _create_request(payload, base_dir)
    if command == "refresh-config":
        return _exclusively(
            payload.get("directory"),
            "refresh_config",
            lambda: _refresh_config_payload(payload),
        )
    if command == "sync-config":
        return _sync_config_payload(payload)
    if command == "reset-flow":
        return _exclusively(
            payload.get("directory"),
            "reset_flow",
            lambda: _reset_flow_payload(payload),
        )

    defaults: dict[str, Any] = {
        "directory": "",
        "id": "",
        "json": False,
        "rerun": False,
        "from_step": "",
        "to_step": "",
        "recover_stale_ongoing": True,
        "sim_compile_extra_cflag": [],
        "sim_compile_mabi": "",
        "sim_compile_march": "",
        "sim_compile_opt_level": "",
        "sim_compile_preset": "",
        "sim_coremark_has_float": "",
        "sim_coremark_iterations": None,
        "sim_coremark_max_cycles": None,
        "sim_coremark_total_data_size": None,
        "sim_cpu_test_case": [],
        "sim_cpu_test_mode": "selected",
        "sim_test_suite": "",
        "step": "",
    }
    aliases = {
        "info_id": "id",
        "sim_compile_extra_cflags": "sim_compile_extra_cflag",
        "sim_cpu_test_cases": "sim_cpu_test_case",
    }
    for key, value in payload.items():
        target = aliases.get(key, key)
        if target in defaults:
            defaults[target] = value
    return _dispatch(argparse.Namespace(command=command, **defaults))


def _refresh_config_payload(payload: dict[str, Any]) -> CliResult:
    directory = _optional_text(payload.get("directory"))
    workspace, _ = _load_runtime(directory, cmd="refresh_config")
    repaired = _repair_workspace_sim_defaults(workspace)
    return CliResult(
        cmd="refresh_config",
        response="success",
        data={
            "directory": workspace["directory"],
            "refreshed": True,
            "repaired_sim_defaults": repaired,
        },
        message=[f"refresh frontend workspace success: {workspace['directory']}"],
    )


def _sync_config_payload(payload: dict[str, Any]) -> CliResult:
    directory = _optional_text(payload.get("directory"))
    config_path = Path(_optional_text(payload.get("config_path"))).expanduser().resolve()
    workspace_dir = Path(_resolve_path(directory, Path.cwd())).resolve()
    if not _path_is_relative_to(config_path, workspace_dir):
        raise WorkspaceCliError(
            "sync_config",
            "failed",
            f"config path outside frontend workspace: {config_path}",
        )
    workspace, _ = _load_runtime(str(workspace_dir), cmd="sync_config")
    return CliResult(
        cmd="sync_config",
        response="success",
        data={
            "config_path": str(config_path),
            "directory": workspace["directory"],
            "parameters_changed": False,
            "refreshed": True,
        },
        message=[f"sync frontend config success: {config_path}"],
    )


def _reset_flow_payload(payload: dict[str, Any]) -> CliResult:
    directory = _optional_text(payload.get("directory"))
    workspace, engine = _load_runtime(directory, cmd="reset_flow")
    engine.clear_states()
    _clear_frontend_step_details(engine)
    return CliResult(
        cmd="reset_flow",
        response="success",
        data={"directory": workspace["directory"]},
        message=[f"reset frontend flow success: {workspace['directory']}"],
    )


def _render_result(result: CliResult, *, json_output: bool) -> None:
    payload = {
        "type": "result",
        "cmd": result.cmd,
        "response": result.response,
        "data": result.data,
        "message": result.message,
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    for message in result.message:
        stream = sys.stderr if result.response in {"failed", "error"} else sys.stdout
        print(message, file=stream)



_SPLIT_MODULES = (
    "result",
    "events",
    "values",
    "create_request",
    "sim_config",
    "step_detail",
)


def __getattr__(name: str) -> Any:
    """Resolve helpers that moved to the sibling modules of this package.

    This module used to define every helper of the application layer. Code
    that still reaches them as ``workspace_service.<name>`` keeps working.
    """
    from importlib import import_module

    for module_name in _SPLIT_MODULES:
        module = import_module(f"fecompiler.application.{module_name}")
        if name in vars(module):
            return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
