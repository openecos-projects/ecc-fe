"""ECOS Studio workspace CLI for fecompiler.

This command shape mirrors the Electron CLI bridge used by ecos-studio main:

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
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fecompiler.catalog import catalog_payload, check_catalog_contracts, validate_frontend_config
from fecompiler.cli.workspace_typer import WorkspaceTyperHandlers
from fecompiler.cli.workspace_typer import build_typer_app as build_workspace_typer_app
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow
from fecompiler.soc import soc_runtime_options
from fecompiler.tools.common.rtl_inputs import prepared_inputs_current
from fecompiler.utility.json import json_read, json_write

try:
    import click
    import typer
except ImportError:
    click = None
    typer = None


DEFAULT_FRONTEND_SMOKE_TEST_CASES = ["add"]
DEFAULT_FRONTEND_COREMARK_CASES = ["coremark"]
DEFAULT_COREMARK_COMPILE_PRESET = "balanced"
DEFAULT_COREMARK_OPT_LEVEL = "-O2"
DEFAULT_COREMARK_MARCH = "rv32im_zicsr"
DEFAULT_COREMARK_MABI = "ilp32"
DEFAULT_COREMARK_ITERATIONS = 1
DEFAULT_COREMARK_TOTAL_DATA_SIZE = 2000
DEFAULT_COREMARK_MAX_CYCLES = 200000000
DEFAULT_COREMARK_HAS_FLOAT = True
CLI_LOG_TAIL_BYTES = 24 * 1024
DIFFTEST_SOURCE_NAME = "difftest.cpp"
DIFFTEST_STUB_SOURCE_NAME = "difftest_stub.cpp"

_PATH_FIELDS = {
    "directory",
    "origin_def",
    "origin_verilog",
    "filelist",
    "cpu_filelist",
    "cpu_adapter_filelist",
    "soc_filelist",
    "testbench",
    "sim_tests_dir",
    "sim_programs_dir",
    "sim_tests_out_dir",
    "sim_soc_root",
    "sim_build_test_script",
}
_PATH_LIST_FIELDS = {
    "rtl_list",
    "sim_cpp_sources",
    "sim_images",
    "sim_program_sources",
}


@dataclass(slots=True)
class CliResult:
    cmd: str
    response: str
    data: dict[str, Any]
    message: list[str]


class WorkspaceCliError(Exception):
    def __init__(self, cmd: str, response: str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = CliResult(cmd=cmd, response=response, data=data or {}, message=[message])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecc-fe workspace",
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
    create.add_argument("--origin-def", default="")
    create.add_argument("--origin-verilog", default="")
    create.add_argument("--filelist", default="")
    create.add_argument("--cpu-filelist", default="")
    create.add_argument("--cpu-adapter-filelist", default="")
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

    load = subparsers.add_parser("load", help="Load an existing frontend workspace")
    _add_json_flag(load)
    load.add_argument("--directory", required=True)

    run_flow = subparsers.add_parser("run-flow", help="Run the full frontend flow")
    _add_json_flag(run_flow)
    run_flow.add_argument("--directory", required=True)
    run_flow.add_argument("--rerun", action="store_true")

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
            prog_name="ecc-fe workspace",
            standalone_mode=False,
        )
    except click.exceptions.Exit as exc:
        return int(exc.exit_code or 0)
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code or 1)
    return int(result or 0)


def _call_command(command: str, callback: Callable[[], CliResult]) -> CliResult:
    try:
        return callback()
    except WorkspaceCliError as exc:
        return exc.result
    except Exception as exc:
        return CliResult(cmd=_command_to_cmd(command), response="error", data={}, message=[str(exc)])


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
        return _run_flow(args)
    if command == "run-step":
        return _run_step(args)
    if command == "get-info":
        return _get_info(args)
    if command == "get-home":
        return _get_home(args)
    raise WorkspaceCliError("workspace", "error", f"unknown workspace command: {command}")


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
    normalized = _normalize_catalog_config_paths(request, base_dir)
    result = validate_frontend_config(normalized)
    response = "success" if result.ok else "failed"
    return CliResult(
        cmd="validate_frontend_config",
        response=response,
        data=result.to_dict(),
        message=[result.summary],
    )


def _create(args: argparse.Namespace) -> CliResult:
    request, base_dir = _create_request_from_args(args)
    normalized = _normalize_create_request(request, base_dir)
    validation = validate_frontend_config(_frontend_catalog_config_from_create_request(normalized))
    if not validation.ok:
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            validation.summary,
            data={"validation": validation.to_dict()},
        )
    _apply_catalog_defaults(normalized, validation.normalized)
    _apply_default_soc_runtime_options(normalized)
    directory = str(normalized.get("directory", "")).strip()
    if not directory:
        raise WorkspaceCliError("create_workspace", "failed", "missing required field: directory")
    _validate_create_request_paths(normalized)

    parameters = _normalize_parameters(normalized.get("parameters", {}))
    if normalized.get("top_module"):
        parameters["Top module"] = normalized["top_module"]
    parameters.setdefault("Design Tool", "frontend")
    parameters["frontend_core_id"] = validation.normalized["core_id"]
    parameters["cpu_wrapper_id"] = validation.normalized["core_id"]
    parameters["cpu_wrapper_contract"] = validation.normalized.get("cpu_wrapper_contract", "")
    parameters["cpu_socket_contract"] = validation.normalized.get("cpu_socket_contract", "")
    parameters["cpu_wrapper_top"] = validation.normalized.get("cpu_wrapper_top", "")
    parameters["cpu_standard_top"] = validation.normalized.get("cpu_standard_top", "")
    parameters["cpu_wrapper_generation"] = validation.normalized.get("cpu_wrapper_generation", "")
    if validation.normalized.get("cpu_adapter_filelist"):
        parameters["cpu_adapter_filelist"] = validation.normalized["cpu_adapter_filelist"]
    parameters["cpu_supports_difftest"] = bool(validation.normalized.get("cpu_supports_difftest", True))
    parameters["core_supported_test_suites"] = validation.normalized.get("core_supported_test_suites", [])
    if validation.normalized.get("core_sim_program_link_base"):
        parameters["sim_program_link_base"] = validation.normalized["core_sim_program_link_base"]
    _apply_core_sim_defaults_to(parameters, validation.normalized)
    parameters["soc_harness_id"] = validation.normalized["soc_harness_id"]
    parameters["soc_wrapper_id"] = validation.normalized["soc_harness_id"]
    parameters["soc_wrapper_contract"] = validation.normalized.get("soc_wrapper_contract", "")
    parameters["soc_wrapper_top"] = validation.normalized.get("soc_wrapper_top", "")
    parameters["soc_supports_difftest"] = bool(validation.normalized.get("soc_supports_difftest", True))
    parameters["soc_supported_test_suites"] = validation.normalized.get("soc_supported_test_suites", [])
    parameters["toolchain_id"] = validation.normalized["toolchain_id"]
    parameters["test_suite_id"] = validation.normalized["test_suite_id"]
    if normalized.get("soc_variant"):
        parameters["soc_variant"] = normalized["soc_variant"]

    spec = CreateWorkspaceData(
        directory=directory,
        parameters=parameters,
        origin_def=str(normalized.get("origin_def", "")),
        origin_verilog=str(normalized.get("origin_verilog", "")),
        filelist=str(normalized.get("filelist", "")),
        cpu_filelist=str(normalized.get("cpu_filelist", "")),
        cpu_adapter_filelist=str(normalized.get("cpu_adapter_filelist", "")),
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
        cpu_supports_difftest=_normalize_bool(normalized.get("cpu_supports_difftest", True)),
        soc_supports_difftest=_normalize_bool(normalized.get("soc_supports_difftest", True)),
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
    workspace = create_workspace(spec)
    if workspace is None:
        raise WorkspaceCliError("create_workspace", "failed", f"create frontend workspace failed: {directory}")
    _repair_workspace_sim_defaults(workspace)
    _apply_workspace_create_test_suite_defaults(workspace, validation.normalized["test_suite_id"])

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
    repaired = _repair_workspace_sim_defaults(workspace)
    recovered = engine.clear_stale_ongoing_states()
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


def _run_flow(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(args.directory, cmd="rtl2gds")
    _repair_workspace_sim_defaults(workspace)
    if args.rerun:
        engine.clear_states()

    json_output = bool(getattr(args, "json", False))
    reports: list[dict[str, Any]] = []
    failed_step = ""
    failed_state = StateEnum.Incomplete
    prepare_refreshed = False
    for workspace_step in engine.workspace_steps:
        if not prepare_refreshed and workspace_step.name != "prepare":
            prepare_refreshed = _refresh_prepare_if_stale(workspace, engine, workspace_step.name)

        if workspace_step.name == "sim":
            _apply_default_sim_smoke_suite(workspace)

        _emit_event(
            "rtl2gds",
            "stdout",
            {"directory": workspace["directory"], "step": workspace_step.name, "tool": workspace_step.tool},
            [f"start frontend step {workspace_step.name}: {workspace['directory']}"],
            json_output=json_output,
        )
        state = engine.run_step(workspace_step.name, rerun=bool(args.rerun))
        report = _step_report_payload(workspace, workspace_step, state)
        reports.append(report)
        _emit_event(
            "rtl2gds",
            "stdout",
            {"directory": workspace["directory"], **report},
            [f"frontend step {workspace_step.name} {state.value}: {workspace['directory']}"],
            json_output=json_output,
        )
        if state != StateEnum.Success:
            failed_step = workspace_step.name
            failed_state = state
            break

    data: dict[str, Any] = {"rerun": bool(args.rerun), "reports": reports}
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

    force_rerun = False
    _refresh_prepare_if_stale(workspace, engine, step)
    if step == "sim":
        suite_name = str(args.sim_test_suite or "").strip()
        if suite_name and suite_name.lower() != "default":
            if suite_name.lower() == "coremark":
                _apply_coremark_compile_options(workspace, args)
            _apply_sim_test_suite(
                workspace,
                suite_name,
                args.sim_cpu_test_mode,
                args.sim_cpu_test_case,
            )
            force_rerun = True
        else:
            _apply_default_sim_smoke_suite(workspace)

    json_output = bool(getattr(args, "json", False))
    _emit_event(
        "run_step",
        "started",
        {"directory": workspace["directory"], "step": step},
        [f"start frontend step {step}: {workspace['directory']}"],
        json_output=json_output,
    )
    state = engine.run_step(step, rerun=bool(args.rerun or force_rerun))
    data: dict[str, Any] = {"step": step, "state": state.value, "directory": workspace["directory"]}
    if workspace_step is not None:
        data.update(_step_report_payload(workspace, workspace_step, state))
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


def _refresh_prepare_if_stale(workspace: dict[str, Any], engine: EngineFlow, target_step: str) -> bool:
    if target_step == "prepare" or prepared_inputs_current(workspace):
        return False

    prepare_step = engine.get_workspace_step("prepare")
    if prepare_step is None:
        return False

    state = engine.run_step("prepare", rerun=True)
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
    workspace, engine = _load_runtime(args.directory, cmd="get_info")
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
    workspace = load_workspace(workspace_dir)
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
    return workspace, engine


def _build_engine(workspace: dict[str, Any]) -> EngineFlow:
    engine = EngineFlow(workspace=workspace)
    if not engine.has_init():
        engine.init_default_steps()
        engine.load()
    return engine


def _create_request_from_args(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    base_dir = Path.cwd()
    request: dict[str, Any] = {}

    if args.input_json:
        request, base_dir = _load_input_json(args.input_json)

    direct: dict[str, Any] = {}
    for arg_name, field in (
        ("directory", "directory"),
        ("origin_def", "origin_def"),
        ("origin_verilog", "origin_verilog"),
        ("filelist", "filelist"),
        ("cpu_filelist", "cpu_filelist"),
        ("cpu_adapter_filelist", "cpu_adapter_filelist"),
        ("soc_filelist", "soc_filelist"),
        ("testbench", "testbench"),
        ("sim_tests_dir", "sim_tests_dir"),
        ("sim_programs_dir", "sim_programs_dir"),
        ("sim_tests_out_dir", "sim_tests_out_dir"),
        ("sim_soc_root", "sim_soc_root"),
        ("sim_build_test_script", "sim_build_test_script"),
        ("sim_compile_preset", "sim_compile_preset"),
        ("sim_compile_opt_level", "sim_compile_opt_level"),
        ("sim_compile_march", "sim_compile_march"),
        ("sim_compile_mabi", "sim_compile_mabi"),
        ("sim_coremark_iterations", "sim_coremark_iterations"),
        ("sim_coremark_total_data_size", "sim_coremark_total_data_size"),
        ("sim_coremark_max_cycles", "sim_coremark_max_cycles"),
        ("core_id", "core_id"),
        ("soc_variant", "soc_variant"),
        ("soc_harness_id", "soc_harness_id"),
    ):
        value = getattr(args, arg_name, "")
        if value:
            direct[field] = value

    list_fields = {
        "sim_cpp_sources": getattr(args, "sim_cpp", []),
        "sim_cflags": getattr(args, "sim_cflag", []),
        "sim_ldflags": getattr(args, "sim_ldflag", []),
        "sim_run_args": getattr(args, "sim_arg", []),
        "sim_images": getattr(args, "sim_image", []),
        "sim_program_names": getattr(args, "sim_program", []),
        "sim_program_sources": getattr(args, "sim_program_source", []),
        "sim_compile_extra_cflags": getattr(args, "sim_compile_extra_cflag", []),
        "rtl_list": getattr(args, "rtl", []),
    }
    for field, values in list_fields.items():
        cleaned = _normalize_str_list(values)
        if cleaned:
            direct[field] = cleaned

    if getattr(args, "sim_all_tests", False):
        direct["sim_all_tests"] = True
    if getattr(args, "sim_build_all_programs", False):
        direct["sim_build_all_programs"] = True

    parameters = dict(request.get("parameters", {}) if isinstance(request.get("parameters"), dict) else {})
    if args.design:
        parameters["Design"] = args.design
    if args.top:
        parameters["Top module"] = args.top
    if args.clock:
        parameters["Clock"] = args.clock
    if args.freq is not None:
        parameters["Frequency max [MHz]"] = args.freq
    if parameters:
        direct["parameters"] = parameters

    request.update(direct)
    return request, base_dir


def _catalog_config_from_args(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    base_dir = Path.cwd()
    request: dict[str, Any] = {}
    if args.input_json:
        request, base_dir = _load_input_json(args.input_json)

    for field in ("core_id", "soc_harness_id", "toolchain_id", "test_suite_id", "cpu_filelist"):
        value = str(getattr(args, field, "") or "").strip()
        if value:
            request[field] = value
    return request, base_dir


def _load_input_json(path_text: str) -> tuple[dict[str, Any], Path]:
    if path_text == "-":
        return json.load(sys.stdin), Path.cwd()
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise WorkspaceCliError("create_workspace", "failed", "input JSON must contain an object")
    return data, path.resolve().parent


def _normalize_create_request(request: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    normalized = dict(request)
    for field in _PATH_FIELDS:
        value = normalized.get(field, "")
        if value:
            normalized[field] = _resolve_path(value, base_dir)
    for field in _PATH_LIST_FIELDS:
        normalized[field] = [
            _resolve_path(item, base_dir)
            for item in _normalize_str_list(normalized.get(field, []))
        ]
    return normalized


def _normalize_catalog_config_paths(request: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    normalized = dict(request)
    value = _optional_text(normalized.get("cpu_filelist", ""))
    if value:
        normalized["cpu_filelist"] = _resolve_path(value, base_dir)
    else:
        normalized.pop("cpu_filelist", None)
    return normalized


def _frontend_catalog_config_from_create_request(request: dict[str, Any]) -> dict[str, Any]:
    parameters = request.get("parameters", {})
    params = parameters if isinstance(parameters, dict) else {}
    return {
        "core_id": _first_text(request.get("core_id"), params.get("frontend_core_id"), params.get("core_id")),
        "soc_harness_id": (
            _first_text(
                request.get("soc_harness_id"),
                params.get("soc_harness_id"),
                request.get("soc_variant"),
                params.get("soc_variant"),
            )
        ),
        "toolchain_id": _first_text(request.get("toolchain_id"), params.get("toolchain_id")),
        "test_suite_id": (
            _first_text(
                request.get("test_suite_id"),
                params.get("test_suite_id"),
                request.get("sim_test_suite"),
                params.get("sim_test_suite"),
            )
        ),
        "cpu_filelist": _first_text(request.get("cpu_filelist"), params.get("cpu_filelist")),
    }


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _optional_text(value)
        if text:
            return text
    return ""


def _apply_catalog_defaults(request: dict[str, Any], normalized: dict[str, Any]) -> None:
    request["core_id"] = normalized.get("core_id", "")
    request["soc_harness_id"] = normalized.get("soc_harness_id", "")
    request["toolchain_id"] = normalized.get("toolchain_id", "")
    request["test_suite_id"] = normalized.get("test_suite_id", "")
    if normalized.get("soc_wrapper_top"):
        request["top_module"] = normalized["soc_wrapper_top"]
    if normalized.get("cpu_filelist"):
        request["cpu_filelist"] = normalized["cpu_filelist"]
    if normalized.get("cpu_adapter_filelist"):
        request["cpu_adapter_filelist"] = normalized["cpu_adapter_filelist"]
    if normalized.get("cpu_standard_top"):
        request["cpu_standard_top"] = normalized["cpu_standard_top"]
    if normalized.get("cpu_wrapper_generation"):
        request["cpu_wrapper_generation"] = normalized["cpu_wrapper_generation"]
    request["cpu_supports_difftest"] = bool(normalized.get("cpu_supports_difftest", True))
    request["soc_supports_difftest"] = bool(normalized.get("soc_supports_difftest", True))
    request["core_supported_test_suites"] = normalized.get("core_supported_test_suites", [])
    request["soc_supported_test_suites"] = normalized.get("soc_supported_test_suites", [])
    if normalized.get("soc_variant"):
        request["soc_variant"] = normalized["soc_variant"]
    if normalized.get("core_sim_program_link_base"):
        request["sim_program_link_base"] = normalized["core_sim_program_link_base"]
    _apply_core_sim_defaults_to(request, normalized)


def _apply_core_sim_defaults_to(target: dict[str, Any], normalized: dict[str, Any]) -> None:
    for source, field in (
        ("core_sim_compile_preset", "sim_compile_preset"),
        ("core_sim_compile_opt_level", "sim_compile_opt_level"),
        ("core_sim_compile_march", "sim_compile_march"),
        ("core_sim_compile_mabi", "sim_compile_mabi"),
        ("core_sim_coremark_iterations", "sim_coremark_iterations"),
        ("core_sim_coremark_total_data_size", "sim_coremark_total_data_size"),
        ("core_sim_coremark_max_cycles", "sim_coremark_max_cycles"),
    ):
        value = normalized.get(source)
        if str(value or "").strip():
            target[field] = str(value).strip()

    extra = _normalize_str_list(normalized.get("core_sim_compile_extra_cflags", []))
    if extra:
        target["sim_compile_extra_cflags"] = extra

    for source, field in (
        ("core_sim_coremark_has_float", "sim_coremark_has_float"),
        ("core_sim_coremark_use_difftest", "sim_coremark_use_difftest"),
    ):
        value = normalized.get(source)
        if value != "":
            target[field] = _normalize_bool(value)


def _normalize_parameters(raw: Any) -> dict[str, Any]:
    parameters = dict(raw) if isinstance(raw, dict) else {}
    aliases = {
        "design": "Design",
        "top_module": "Top module",
        "clock": "Clock",
        "frequency_max": "Frequency max [MHz]",
    }
    for source, target in aliases.items():
        if source in parameters and target not in parameters:
            parameters[target] = parameters[source]
    parameters.setdefault("Design", "New_Chip_Design")
    parameters.setdefault("Top module", "top")
    parameters.setdefault("Clock", "clk")
    parameters.setdefault("Frequency max [MHz]", 100)
    return parameters


def _validate_create_request_paths(normalized: dict[str, Any]) -> None:
    missing: list[str] = []
    file_fields = (
        "cpu_filelist",
        "cpu_adapter_filelist",
        "soc_filelist",
        "filelist",
        "origin_verilog",
        "testbench",
        "sim_build_test_script",
    )
    directory_fields = (
        "sim_soc_root",
        "sim_programs_dir",
        "sim_tests_dir",
    )

    for field in file_fields:
        value = str(normalized.get(field, "")).strip()
        if value and not Path(value).is_file():
            missing.append(f"{field}: {value}")
    for field in directory_fields:
        value = str(normalized.get(field, "")).strip()
        if value and not Path(value).is_dir():
            missing.append(f"{field}: {value}")

    list_missing: list[str] = []
    for field in _PATH_LIST_FIELDS:
        for value in _normalize_str_list(normalized.get(field, [])):
            if not Path(value).exists():
                list_missing.append(f"{field}: {value}")

    if missing or list_missing:
        details = missing + list_missing
        raise WorkspaceCliError(
            "create_workspace",
            "failed",
            "frontend workspace input path not found: " + "; ".join(details[:8]),
            data={
                "missing_paths": details,
            },
        )


def _apply_default_soc_runtime_options(data: dict[str, Any]) -> bool:
    defaults = _default_soc_runtime_options(data)
    if not defaults:
        return False

    changed = False
    scalar_fields = (
        "soc_variant",
        "soc_wrapper_id",
        "soc_wrapper_contract",
        "top_module",
        "sim_soc_root",
        "soc_filelist",
        "testbench",
        "sim_build_test_script",
        "sim_programs_dir",
        "sim_tests_dir",
        "soc_supports_difftest",
    )
    for field in scalar_fields:
        value = defaults.get(field, "")
        if value:
            if str(data.get(field, "")).strip() and not _should_replace_soc_runtime_value(data, defaults, field):
                continue
            if data.get(field) == value:
                continue
            data[field] = value
            changed = True

    for field in ("sim_cpp_sources", "sim_cflags", "sim_ldflags"):
        values = _normalize_str_list(defaults.get(field, []))
        if values and (
            not _normalize_str_list(data.get(field, []))
            or _should_replace_soc_runtime_list(data, defaults, field)
        ):
            if _normalize_str_list(data.get(field, [])) == values:
                continue
            data[field] = values
            changed = True

    adapted_sources = _adapt_sim_cpp_sources_for_cpu(data, _normalize_str_list(data.get("sim_cpp_sources", [])))
    if adapted_sources != _normalize_str_list(data.get("sim_cpp_sources", [])):
        data["sim_cpp_sources"] = adapted_sources
        changed = True

    return changed


def _repair_workspace_sim_defaults(workspace: dict[str, Any]) -> bool:
    defaults = _default_soc_runtime_options(workspace)
    if not defaults:
        return False

    updates: dict[str, Any] = {}
    scalar_fields = (
        "soc_variant",
        "soc_wrapper_id",
        "soc_wrapper_contract",
        "top_module",
        "sim_soc_root",
        "soc_filelist",
        "testbench",
        "sim_build_test_script",
        "sim_programs_dir",
        "sim_tests_dir",
        "soc_supports_difftest",
    )
    for field in scalar_fields:
        value = defaults.get(field, "")
        if value:
            if str(workspace.get(field, "")).strip() and not _should_replace_soc_runtime_value(workspace, defaults, field):
                continue
            if workspace.get(field) == value:
                continue
            updates[field] = value

    for field in ("sim_cflags", "sim_ldflags"):
        values = _normalize_str_list(defaults.get(field, []))
        if values and (
            not _normalize_str_list(workspace.get(field, []))
            or _should_replace_soc_runtime_list(workspace, defaults, field)
        ):
            if _normalize_str_list(workspace.get(field, [])) == values:
                continue
            updates[field] = values

    existing_sources = _normalize_str_list(workspace.get("sim_cpp_sources", []))
    default_sources = _normalize_str_list(defaults.get("sim_cpp_sources", []))
    source_base = existing_sources or default_sources
    adapted_sources = _adapt_sim_cpp_sources_for_cpu(workspace, source_base)
    if (
        default_sources
        and _should_replace_soc_runtime_list(workspace, defaults, "sim_cpp_sources")
    ):
        adapted_sources = _adapt_sim_cpp_sources_for_cpu(workspace, default_sources)
    if adapted_sources and adapted_sources != existing_sources:
        updates["sim_cpp_sources"] = adapted_sources

    if not updates:
        return False
    _update_workspace_parameters(workspace, updates)
    return True


def _default_soc_runtime_options(data: dict[str, Any]) -> dict[str, Any]:
    if not _should_apply_soc_runtime_options(data):
        return {}

    defaults = soc_runtime_options(data)
    if defaults:
        return _with_cpu_runtime_options(data, defaults)

    # Backward-compatible fallback for workspaces created before soc_wrapper_id
    # was introduced: infer the wrapper from an explicit SoC root/filelist.
    root = _explicit_soc_root(data)
    if root is None:
        return {}
    inferred = {
        **data,
        "sim_soc_root": str(root),
        "soc_harness_id": _soc_wrapper_id_from_root(root),
    }
    return _with_cpu_runtime_options(data, soc_runtime_options(inferred))


def _with_cpu_runtime_options(data: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    if not defaults:
        return {}
    out = dict(defaults)
    out["sim_cpp_sources"] = _adapt_sim_cpp_sources_for_cpu(data, _normalize_str_list(out.get("sim_cpp_sources", [])))
    return out


def _should_replace_soc_runtime_value(data: dict[str, Any], defaults: dict[str, Any], field: str) -> bool:
    current = str(data.get(field, "")).strip()
    expected = str(defaults.get(field, "")).strip()
    if not current or not expected or current == expected:
        return False
    if field in {"soc_wrapper_id", "soc_wrapper_contract", "soc_variant", "top_module", "soc_supports_difftest"}:
        return True
    return _is_builtin_soc_runtime_path(current) and _is_builtin_soc_runtime_path(expected)


def _should_replace_soc_runtime_list(data: dict[str, Any], defaults: dict[str, Any], field: str) -> bool:
    current = _normalize_str_list(data.get(field, []))
    expected = _normalize_str_list(defaults.get(field, []))
    if not current or not expected or current == expected:
        return False
    if field in {"sim_cflags", "sim_ldflags"}:
        return _list_uses_builtin_soc_runtime_path(current) and _list_uses_builtin_soc_runtime_path(expected)
    return all(_is_builtin_soc_runtime_path(item) for item in current) and all(
        _is_builtin_soc_runtime_path(item) for item in expected
    )


def _list_uses_builtin_soc_runtime_path(values: list[str]) -> bool:
    return any(_is_builtin_soc_runtime_path(_strip_cflag_path(value)) for value in values)


def _strip_cflag_path(value: str) -> str:
    text = str(value).strip()
    if text.startswith("-I") and len(text) > 2:
        return text[2:]
    return text


def _is_builtin_soc_runtime_path(value: str) -> bool:
    text = _strip_cflag_path(value)
    if not text:
        return False
    try:
        path = Path(text).expanduser().resolve()
        return any(_path_is_relative_to(path, root) for root in _builtin_soc_runtime_roots())
    except (OSError, ValueError):
        return False


def _builtin_soc_runtime_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv("ECOS_FE_COMPILER_ROOT", "").strip()
    if env_root:
        roots.append(Path(env_root).expanduser().resolve() / "fecompiler" / "thirdparty")
    roots.append(Path(__file__).resolve().parents[2] / "fecompiler" / "thirdparty")
    return roots


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _adapt_sim_cpp_sources_for_cpu(data: dict[str, Any], sources: list[str]) -> list[str]:
    if _supports_difftest(data):
        return _replace_difftest_source(sources, DIFFTEST_STUB_SOURCE_NAME, DIFFTEST_SOURCE_NAME)
    return _replace_difftest_source(sources, DIFFTEST_SOURCE_NAME, DIFFTEST_STUB_SOURCE_NAME)


def _replace_difftest_source(sources: list[str], old_name: str, new_name: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        path = Path(source)
        candidate = str(path.with_name(new_name)) if path.name == old_name else source
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def _should_apply_soc_runtime_options(data: dict[str, Any]) -> bool:
    return any(
        str(data.get(field, "")).strip()
        for field in ("cpu_filelist", "soc_filelist", "sim_soc_root", "soc_variant", "soc_harness_id", "soc_wrapper_id")
    )


def _explicit_soc_root(data: dict[str, Any]) -> Path | None:
    for field in ("sim_soc_root", "soc_filelist"):
        value = str(data.get(field, "")).strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if field == "soc_filelist":
            path = path.parent
        if path.exists():
            return path
    return None


def _soc_wrapper_id_from_root(root: Path) -> str:
    name = root.name
    return "ysyx-am-soc"


def _normalize_workspace_soc_id(value: Any) -> str:
    text = str(value or "").strip()
    alias_map = {
        "": "",
        "SoC": "ysyx-am-soc",
        "SoC2": "ysyx-am-soc",
        "SoC3": "ysyx-am-soc",
        "soc1": "ysyx-am-soc",
        "soc2": "ysyx-am-soc",
        "soc3": "ysyx-am-soc",
        "ysyx-am-soc-alt": "ysyx-am-soc",
        "ysyx-am-soc-extended": "ysyx-am-soc",
    }
    return alias_map.get(text, text)


def _workspace_soc_id(workspace: dict[str, Any]) -> str:
    return _normalize_workspace_soc_id(workspace.get("soc_wrapper_id") or workspace.get("soc_harness_id") or "")


def _apply_sim_test_suite(
    workspace: dict[str, Any],
    suite: Any,
    cpu_test_mode: Any = "selected",
    cpu_test_cases: Any = None,
) -> None:
    suite_name = str(suite or "").strip()
    if not suite_name or suite_name == "default":
        return

    if suite_name in {"cpu_tests", "cpu-tests", "smoke"}:
        _validate_workspace_test_suite_supported(workspace, "cpu-tests")
        mode = str(cpu_test_mode or "selected").strip().lower()
        cases = _normalize_str_list(cpu_test_cases)
        if mode not in {"all", "selected"}:
            raise WorkspaceCliError("run_step", "failed", f"unknown CPU Tests mode: {mode}")
        if mode == "selected" and not cases:
            cases = _default_cpu_test_cases(workspace)
        benchmark_cases = [name for name in cases if name in DEFAULT_FRONTEND_COREMARK_CASES]
        if benchmark_cases:
            raise WorkspaceCliError(
                "run_step",
                "failed",
                f"use CoreMark suite for benchmark case: {', '.join(benchmark_cases)}",
                data={"test_suite_id": "coremark", "cases": benchmark_cases},
            )
        if mode == "selected":
            _validate_cpu_test_cases(workspace, cases)
        updates = {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": mode == "all",
            "sim_program_names": [] if mode == "all" else cases,
            "sim_run_args": _default_cpu_tests_run_args(workspace),
        }
    elif suite_name == "rtthread":
        _validate_workspace_test_suite_supported(workspace, "rtthread")
        updates = {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": False,
            "sim_program_names": ["rtthread"],
            "sim_run_args": _default_rtthread_run_args(workspace),
        }
    elif suite_name == "coremark":
        _validate_workspace_test_suite_supported(workspace, "coremark")
        _validate_cpu_test_cases(workspace, DEFAULT_FRONTEND_COREMARK_CASES)
        updates = {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": False,
            "sim_program_names": list(DEFAULT_FRONTEND_COREMARK_CASES),
            "sim_run_args": _default_coremark_run_args(workspace),
            **_default_coremark_compile_settings(workspace),
        }
    else:
        raise WorkspaceCliError("run_step", "failed", f"unknown frontend sim test suite: {suite_name}")

    _update_workspace_parameters(workspace, updates)


def _default_coremark_compile_settings(workspace: dict[str, Any] | None = None) -> dict[str, Any]:
    source = workspace or {}
    return {
        "sim_compile_preset": _first_text(source.get("sim_compile_preset"), DEFAULT_COREMARK_COMPILE_PRESET),
        "sim_compile_opt_level": _first_text(source.get("sim_compile_opt_level"), DEFAULT_COREMARK_OPT_LEVEL),
        "sim_compile_march": _first_text(source.get("sim_compile_march"), DEFAULT_COREMARK_MARCH),
        "sim_compile_mabi": _first_text(source.get("sim_compile_mabi"), DEFAULT_COREMARK_MABI),
        "sim_compile_extra_cflags": _normalize_str_list(source.get("sim_compile_extra_cflags", [])),
        "sim_coremark_iterations": _positive_int(source.get("sim_coremark_iterations"), DEFAULT_COREMARK_ITERATIONS),
        "sim_coremark_total_data_size": _positive_int(
            source.get("sim_coremark_total_data_size"),
            DEFAULT_COREMARK_TOTAL_DATA_SIZE,
        ),
        "sim_coremark_has_float": _normalize_bool(
            source.get("sim_coremark_has_float", DEFAULT_COREMARK_HAS_FLOAT),
        ),
        "sim_coremark_max_cycles": _positive_int(
            source.get("sim_coremark_max_cycles"),
            DEFAULT_COREMARK_MAX_CYCLES,
        ),
        "sim_coremark_use_difftest": _normalize_bool(source.get("sim_coremark_use_difftest", False)),
    }


def _apply_coremark_compile_options(workspace: dict[str, Any], args: argparse.Namespace) -> None:
    updates = _default_coremark_compile_settings(workspace)
    for attr, field in (
        ("sim_compile_preset", "sim_compile_preset"),
        ("sim_compile_opt_level", "sim_compile_opt_level"),
        ("sim_compile_march", "sim_compile_march"),
        ("sim_compile_mabi", "sim_compile_mabi"),
    ):
        value = str(getattr(args, attr, "") or "").strip()
        if value:
            updates[field] = value

    extra = _normalize_str_list(getattr(args, "sim_compile_extra_cflag", []))
    if extra:
        updates["sim_compile_extra_cflags"] = extra

    iterations = getattr(args, "sim_coremark_iterations", None)
    if iterations is not None:
        updates["sim_coremark_iterations"] = _positive_int(iterations, DEFAULT_COREMARK_ITERATIONS)

    data_size = getattr(args, "sim_coremark_total_data_size", None)
    if data_size is not None:
        updates["sim_coremark_total_data_size"] = _positive_int(data_size, DEFAULT_COREMARK_TOTAL_DATA_SIZE)

    max_cycles = getattr(args, "sim_coremark_max_cycles", None)
    if max_cycles is not None:
        updates["sim_coremark_max_cycles"] = _positive_int(max_cycles, DEFAULT_COREMARK_MAX_CYCLES)

    has_float = str(getattr(args, "sim_coremark_has_float", "") or "").strip()
    if has_float:
        updates["sim_coremark_has_float"] = _normalize_bool(has_float)

    _update_workspace_parameters(workspace, updates)


def _apply_workspace_create_test_suite_defaults(workspace: dict[str, Any], test_suite_id: Any) -> None:
    suite = str(test_suite_id or "").strip()
    if not suite or suite == "default":
        return
    if _normalize_str_list(workspace.get("sim_program_names", [])) or _normalize_str_list(workspace.get("sim_images", [])):
        return
    if suite in {"smoke", "cpu-tests", "cpu_tests"}:
        _apply_default_sim_smoke_suite(workspace)
        return
    if suite == "rtthread":
        _apply_sim_test_suite(workspace, "rtthread")
    if suite == "coremark":
        _apply_sim_test_suite(workspace, "coremark")


def _apply_default_sim_smoke_suite(workspace: dict[str, Any]) -> None:
    _validate_workspace_test_suite_supported(workspace, "cpu-tests")
    cases = _default_cpu_test_cases(workspace)
    if not cases:
        return
    _validate_cpu_test_cases(workspace, cases)
    _update_workspace_parameters(
        workspace,
        {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": False,
            "sim_program_names": cases,
            "sim_run_args": _default_cpu_tests_run_args(workspace),
        },
    )


def _default_cpu_test_cases(workspace: dict[str, Any]) -> list[str]:
    preferred = list(DEFAULT_FRONTEND_SMOKE_TEST_CASES)
    programs_dir = _resolve_optional_path(workspace.get("sim_programs_dir", ""))
    if not programs_dir:
        return preferred

    path = Path(programs_dir)
    if not path.is_dir():
        return preferred

    available = [
        source.stem
        for source in sorted(path.glob("*.c"))
        if source.stem not in DEFAULT_FRONTEND_COREMARK_CASES
    ]
    if not available:
        return []

    selected = [name for name in preferred if name in available]
    for name in available:
        if len(selected) >= len(DEFAULT_FRONTEND_SMOKE_TEST_CASES):
            break
        if name not in selected:
            selected.append(name)
    return selected[: len(DEFAULT_FRONTEND_SMOKE_TEST_CASES)]


def _validate_cpu_test_cases(workspace: dict[str, Any], cases: list[str]) -> None:
    invalid_names = [
        name for name in cases
        if not name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in name)
    ]
    if invalid_names:
        raise WorkspaceCliError("run_step", "failed", f"invalid CPU test case name: {', '.join(invalid_names)}")

    programs_dir = _resolve_optional_path(workspace.get("sim_programs_dir", ""))
    if not programs_dir:
        return

    missing = [
        name for name in cases
        if not (Path(programs_dir) / f"{name}.c").is_file()
    ]
    if missing:
        raise WorkspaceCliError("run_step", "failed", f"CPU test case not found: {', '.join(missing)}")


def _default_cpu_tests_run_args(workspace: dict[str, Any]) -> list[str]:
    if not _supports_difftest(workspace):
        return ["--max-cycles", "50000000"]
    soc_root = _workspace_soc_root(workspace)
    if not soc_root:
        return ["--max-cycles", "50000000"]
    return [
        "--max-cycles",
        "50000000",
        "--diff",
        "--ref",
        str(soc_root / "tools" / "riscv32-spike-so"),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
    ]


def _default_rtthread_run_args(workspace: dict[str, Any]) -> list[str]:
    if not _supports_difftest(workspace):
        raise WorkspaceCliError(
            "run_step",
            "failed",
            f"{_workspace_core_label(workspace)} with {_workspace_soc_label(workspace)} does not support RT-Thread in the current ECOS adapter.",
            data={"core_id": str(workspace.get("cpu_wrapper_id", "")).strip(), "test_suite_id": "rtthread"},
        )
    soc_root = _workspace_soc_root(workspace)
    args = ["--max-cycles", "10000000"]
    if not soc_root:
        return [*args, "--timeout-ok"]
    return [
        *args,
        "--diff",
        "--ref",
        str(soc_root / "tools" / "riscv32-spike-so"),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
        "--timeout-ok",
    ]


def _default_coremark_run_args(workspace: dict[str, Any]) -> list[str]:
    max_cycles = str(_positive_int(workspace.get("sim_coremark_max_cycles"), DEFAULT_COREMARK_MAX_CYCLES))
    args = ["--max-cycles", max_cycles]
    if not _normalize_bool(workspace.get("sim_coremark_use_difftest", False)):
        return args
    if not _supports_difftest(workspace):
        return args
    soc_root = _workspace_soc_root(workspace)
    if not soc_root:
        return args
    return [
        *args,
        "--diff",
        "--ref",
        str(soc_root / "tools" / "riscv32-spike-so"),
        "--diff-image-offset",
        "0x100",
        "--diff-reset-vector",
        "0x80000000",
    ]


def _validate_workspace_test_suite_supported(workspace: dict[str, Any], suite_id: str) -> None:
    known, supported = _workspace_test_suite_contract(workspace)
    if known and suite_id not in supported:
        raise WorkspaceCliError(
            "run_step",
            "failed",
            f"{_workspace_core_label(workspace)} on {_workspace_soc_label(workspace)} does not support {suite_id} in the current ECOS adapter.",
            data={
                "core_id": str(workspace.get("cpu_wrapper_id", "")).strip(),
                "soc_harness_id": str(workspace.get("soc_wrapper_id", "")).strip(),
                "test_suite_id": suite_id,
                "supported_test_suites": supported,
            },
        )


def _workspace_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    return _workspace_test_suite_contract(workspace)[1]


def _workspace_test_suite_contract(workspace: dict[str, Any]) -> tuple[bool, list[str]]:
    core_supported = _expand_frontend_supported_test_suites(
        _normalize_str_list(workspace.get("core_supported_test_suites", [])),
    )
    soc_supported = _expand_frontend_supported_test_suites(
        _normalize_str_list(workspace.get("soc_supported_test_suites", [])),
    )
    has_core_contract = "core_supported_test_suites" in workspace
    has_soc_contract = "soc_supported_test_suites" in workspace
    if core_supported and soc_supported:
        return True, [suite for suite in core_supported if suite in soc_supported]
    if has_core_contract or has_soc_contract:
        if has_core_contract and not core_supported:
            return True, []
        if has_soc_contract and not soc_supported:
            return True, []
        soc_known, soc_fallback = _fallback_soc_test_suite_contract(workspace)
        core_known, core_fallback = _fallback_core_test_suite_contract(workspace)
        if core_supported:
            return True, [suite for suite in core_supported if not soc_known or suite in soc_fallback]
        if soc_supported:
            return True, [suite for suite in soc_supported if not core_known or suite in core_fallback]

    core_known, core_fallback = _fallback_core_test_suite_contract(workspace)
    soc_known, soc_fallback = _fallback_soc_test_suite_contract(workspace)
    if core_fallback and soc_fallback:
        return True, [suite for suite in core_fallback if suite in soc_fallback]
    if core_fallback:
        return core_known, core_fallback
    if soc_fallback:
        if core_known:
            return True, []
        return soc_known, soc_fallback
    return core_known or soc_known, []


def _expand_frontend_supported_test_suites(suites: list[str]) -> list[str]:
    return list(suites)


def _fallback_core_test_suite_contract(workspace: dict[str, Any]) -> tuple[bool, list[str]]:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    if core_id == "darkriscv":
        return True, []
    return bool(core_id in {"picorv32", "scr1", "ibex", "cv32e40p", "cva6", "serv", "femtorv32", "vexriscv", "custom-filelist", "standard-cpu-filelist", "ysyx_00000000", ""}), _fallback_core_supported_test_suites(workspace)


def _fallback_soc_test_suite_contract(workspace: dict[str, Any]) -> tuple[bool, list[str]]:
    soc_id = _workspace_soc_id(workspace)
    return soc_id == "ysyx-am-soc", _fallback_soc_supported_test_suites(workspace)


def _fallback_core_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    if core_id in {"picorv32", "scr1", "ibex", "cv32e40p", "serv", "femtorv32", "vexriscv"}:
        return ["cpu-tests", "smoke", "coremark"]
    if core_id == "cva6":
        return ["cpu-tests", "smoke"]
    if core_id in {"custom-filelist", "ysyx_00000000", ""}:
        return ["smoke", "cpu-tests", "rtthread", "coremark"]
    if core_id == "standard-cpu-filelist":
        return ["smoke", "cpu-tests", "coremark"]
    return []


def _fallback_soc_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    soc_id = _workspace_soc_id(workspace)
    if soc_id == "ysyx-am-soc":
        return ["smoke", "cpu-tests", "rtthread", "coremark"]
    return []


def _cpu_supports_difftest(workspace: dict[str, Any]) -> bool:
    raw = workspace.get("cpu_supports_difftest")
    if raw is not None:
        return _normalize_bool(raw)
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    if core_id in {"picorv32", "scr1", "ibex", "cv32e40p", "cva6", "serv", "femtorv32", "vexriscv", "darkriscv", "standard-cpu-filelist"}:
        return False
    return True


def _soc_supports_difftest(workspace: dict[str, Any]) -> bool:
    raw = workspace.get("soc_supports_difftest")
    if raw is not None:
        return _normalize_bool(raw)
    return True


def _supports_difftest(workspace: dict[str, Any]) -> bool:
    return _cpu_supports_difftest(workspace) and _soc_supports_difftest(workspace)


def _workspace_core_label(workspace: dict[str, Any]) -> str:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "selected CPU").strip()
    if core_id == "picorv32":
        return "PicoRV32"
    return core_id or "selected CPU"


def _workspace_soc_label(workspace: dict[str, Any]) -> str:
    soc_id = _workspace_soc_id(workspace) or "selected SoC"
    if soc_id.startswith("ysyx-am-soc"):
        return "YSYX AM SoC"
    return soc_id or "selected SoC"


def _workspace_soc_root(workspace: dict[str, Any]) -> Path | None:
    explicit = _resolve_optional_path(workspace.get("sim_soc_root", ""))
    if explicit and Path(explicit).exists():
        return Path(explicit)
    soc_filelist = _resolve_optional_path(workspace.get("soc_filelist", ""))
    if soc_filelist and Path(soc_filelist).exists():
        return Path(soc_filelist).parent
    return None


def _update_workspace_parameters(workspace: dict[str, Any], updates: dict[str, Any]) -> None:
    params_path = str(workspace.get("parameters_path", "")).strip()
    if not params_path:
        workspace.update(updates)
        return
    parameters = json_read(params_path)
    parameters.update(updates)
    json_write(params_path, parameters)
    workspace.update(updates)


def _build_step_info(
    workspace: dict[str, Any],
    engine: EngineFlow,
    step: Any,
    info_id: str,
) -> dict[str, Any]:
    if info_id == "subflow":
        return {"path": _step_section(step, "subflow").get("path", "")}
    if info_id == "frontend_detail":
        flow_step = engine.get_step(step.name, step.tool)
        return _build_frontend_step_detail(workspace, step, flow_step)
    if info_id == "config":
        config_path = _step_section(step, "config").get("flow", "")
        return {"config": config_path} if config_path and os.path.exists(config_path) else {}
    if info_id == "checklist":
        path = _step_section(step, "checklist").get("path", "")
        return {"path": path} if path and os.path.exists(path) else {}
    if info_id in {"analysis", "metrics"}:
        analysis = _step_section(step, "analysis")
        info: dict[str, Any] = {}
        for key in ("metrics", "statis_csv"):
            path = analysis.get(key, "")
            if path and os.path.exists(path):
                info[key] = path
        return info
    return {}


def _step_report_payload(workspace: dict[str, Any], workspace_step: Any, state: StateEnum) -> dict[str, Any]:
    step_log = str(workspace_step.log.get("file", ""))
    report_path = str(workspace_step.report.get("step", ""))
    report_dir = _optional_path(workspace_step.report.get("dir", ""))
    report_log = str(report_dir / "log.txt") if report_dir else ""
    return {
        "step": workspace_step.name,
        "tool": workspace_step.tool,
        "state": state.value,
        "log_file": step_log,
        "report_file": report_path,
        "subflow_path": workspace_step.subflow.get("path", ""),
        "home_page": workspace.get("home_path", ""),
        "artifacts": _build_frontend_step_artifacts(workspace, workspace_step),
        "logs": _build_frontend_step_logs(step_log, report_log),
    }


def _failure_payload(
    workspace: dict[str, Any],
    workspace_step: Any,
    step_name: str,
    state: StateEnum,
) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(workspace_step, "report").get("dir", ""))
    candidate_logs = [
        _step_section(workspace_step, "log").get("file", ""),
        report_dir / "log.txt" if report_dir else "",
        report_dir / "build_programs.log.txt" if report_dir else "",
        report_dir / "cases.json" if report_dir else "",
    ]
    logs = [
        item for item in (
            _existing_path_item(path, label)
            for label, path in (
                ("Step log", candidate_logs[0]),
                ("Tool log", candidate_logs[1]),
                ("Build programs log", candidate_logs[2]),
                ("Simulation cases", candidate_logs[3]),
            )
        )
        if item
    ]
    return {
        "step": step_name,
        "state": state.value,
        "logs": logs,
        "artifacts": _build_frontend_step_artifacts(workspace, workspace_step),
        "log_tail": _failure_log_tail(logs),
    }


def _failure_messages(prefix: str, failure: Any) -> list[str]:
    messages = [prefix]
    if not isinstance(failure, dict):
        return messages
    logs = failure.get("logs")
    if isinstance(logs, list) and logs:
        preferred = _failure_log_item([item for item in logs if isinstance(item, dict)])
        if preferred.get("path"):
            messages.append(f"log: {preferred['path']}")
    tail = str(failure.get("log_tail", "")).strip()
    if tail:
        tail_lines = tail.splitlines()[-8:]
        messages.extend(tail_lines)
    return messages


def _failure_log_tail(logs: list[dict[str, str]]) -> str:
    preferred = _failure_log_item(logs)
    if preferred.get("path"):
        tail = _read_text_tail(preferred.get("path", ""), CLI_LOG_TAIL_BYTES)
        if tail.strip():
            return tail
    return ""


def _failure_log_item(logs: list[dict[str, str]]) -> dict[str, str]:
    preferred_labels = ("Build programs log", "Tool log", "Step log")
    for label in preferred_labels:
        for item in logs:
            if item.get("label") != label:
                continue
            if _read_text_tail(item.get("path", ""), CLI_LOG_TAIL_BYTES).strip():
                return item
    for item in logs:
        if str(item.get("path", "")).endswith(".json"):
            continue
        if _read_text_tail(item.get("path", ""), CLI_LOG_TAIL_BYTES).strip():
            return item
    return {}


def _build_frontend_step_detail(
    workspace: dict[str, Any],
    step: Any,
    flow_step: dict[str, Any] | None,
) -> dict[str, Any]:
    state = str((flow_step or {}).get("state", "Unstart"))
    runtime = str((flow_step or {}).get("runtime", ""))
    peak_memory = (flow_step or {}).get("peak memory (mb)", 0)
    step_name = str(step.name)
    step_log_path = str(_step_section(step, "log").get("file", ""))
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    report_log_path = str(report_dir / "log.txt") if report_dir else ""

    detail: dict[str, Any] = {
        "step": step_name,
        "tool": str(step.tool),
        "state": state,
        "runtime": runtime,
        "peak_memory_mb": peak_memory,
        "summary": _build_frontend_step_summary(step, state, runtime),
        "logs": _build_frontend_step_logs(step_log_path, report_log_path),
        "reports": _build_frontend_step_reports(step),
        "artifacts": _build_frontend_step_artifacts(workspace, step),
        "log": step_log_path,
        "report": str(_step_section(step, "report").get("step", "")),
        "subflow": str(_step_section(step, "subflow").get("path", "")),
        "home_page": str(workspace.get("home_path", "")),
    }

    if step_name == "sim":
        cases = _build_frontend_sim_cases(step)
        detail["cases"] = cases
        passed = len([case for case in cases if case.get("ok") is True])
        failed = len([case for case in cases if case.get("ok") is False])
        detail["summary"].update(
            {
                "total_cases": len(cases),
                "passed_cases": passed,
                "failed_cases": failed,
                "run_id": _sim_run_id(step),
                "test_suite": _sim_suite_label(workspace, cases),
                "suite_id": _sim_suite_id(workspace, cases),
                "cpu_test_mode": _sim_cpu_test_mode(workspace, cases),
                "available_cpu_tests": _available_cpu_test_cases(workspace),
                "default_cpu_tests": _default_cpu_test_cases(workspace),
            }
        )
    elif step_name == "review":
        review = _build_frontend_review_payload(step)
        detail["review"] = review
        if review:
            detail["summary"].update({
                "rtl_review": review.get("summary", {}),
                "review_report": review.get("path", ""),
            })
    elif step_name == "elab":
        elab = _build_frontend_elab_payload(step)
        detail["elab"] = elab
        if elab:
            detail["summary"].update({
                "elab": elab.get("summary", {}),
                "elab_report": elab.get("path", ""),
            })
    elif step_name == "lint":
        lint = _build_frontend_lint_payload(step)
        detail["lint"] = lint
        if lint:
            detail["summary"].update({
                "lint": lint.get("summary", {}),
                "lint_report": lint.get("path", ""),
            })
    elif step_name == "prepare":
        prepare = _build_frontend_prepare_payload(workspace, step)
        detail["prepare"] = prepare
        if prepare:
            detail["summary"].update({
                "readiness": prepare.get("readiness", {}),
                "inputs": prepare.get("inputs", {}),
                "contracts": prepare.get("contracts", []),
                "runtime_plan": prepare.get("runtime", {}),
            })

    return detail


def _build_frontend_step_summary(step: Any, state: str, runtime: str) -> dict[str, Any]:
    report = _json_read(_step_section(step, "report").get("step", ""))
    summary: dict[str, Any] = {
        "status": state,
        "runtime": runtime,
    }
    if isinstance(report, dict):
        summary["report"] = report
    return summary


def _build_frontend_step_logs(step_log_path: str, report_log_path: str) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = []
    seen: set[str] = set()
    report_dir = _optional_path(Path(report_log_path).parent if report_log_path else "")
    for item in (
        _existing_path_item(report_dir / "rtl_review_summary.md" if report_dir else "", "Review summary"),
        _existing_path_item(step_log_path, "Step log"),
        _existing_path_item(report_log_path, "Tool log"),
        _existing_path_item(_first_existing(report_dir, ("yosys_precheck.log", "structural_probe.log")) if report_dir else "", "Yosys precheck log"),
        _existing_path_item(report_dir / "build_programs.log.txt" if report_dir else "", "Build programs log"),
    ):
        path = str((item or {}).get("path", ""))
        if item and path not in seen:
            seen.add(path)
            logs.append(item)
    return logs


def _build_frontend_step_reports(step: Any) -> list[dict[str, str]]:
    reports: list[dict[str, str]] = []
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    for item in (
        _existing_path_item(report_dir / "rtl_review_summary.md" if report_dir else "", "Review summary"),
        _existing_path_item(_step_section(step, "report").get("step", ""), "Step report"),
        _existing_path_item(report_dir / "elab_summary.json" if report_dir else "", "Elab summary"),
        _existing_path_item(report_dir / "lint_summary.json" if report_dir else "", "Lint summary"),
        _existing_path_item(report_dir / "rtl_review.json" if report_dir else "", "RTL review"),
        _existing_path_item(_first_existing(report_dir, ("yosys_precheck.json", "structural_probe.json")) if report_dir else "", "Yosys precheck"),
        _existing_path_item(report_dir / "cases.json" if report_dir else "", "Simulation cases"),
        _existing_path_item(report_dir / "build_programs.log.txt" if report_dir else "", "Build programs log"),
    ):
        if item:
            reports.append(item)
    return reports


def _build_frontend_step_artifacts(workspace: dict[str, Any], step: Any) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    output_dir = _optional_path(_step_section(step, "output").get("dir", ""))
    design = str(workspace.get("design", ""))

    def append_item(item: dict[str, str] | None) -> None:
        path = str((item or {}).get("path", "")).strip()
        if not item or not path or path in seen_paths:
            return
        seen_paths.add(path)
        artifacts.append(item)

    for label, path in (
        ("Simulation binary", output_dir / f"{design}_sim" if output_dir and design else ""),
    ):
        append_item(_existing_path_item(path, label))

    if str(step.name).strip().lower() == "prepare":
        for item in _build_prepare_cpu_source_artifacts(workspace):
            append_item(item)

    if str(step.name).strip().lower() == "review":
        for item in _build_review_source_artifacts(step):
            append_item(item)

    if str(step.name).strip().lower() == "sim":
        for case in _build_frontend_sim_cases(step):
            case_name = str(case.get("name", "")).strip()
            for key, suffix in (("wave", "wave"), ("image", "image"), ("log", "log"), ("run_log", "run log")):
                path = str(case.get(key, "")).strip()
                label = f"{case_name} {suffix}".strip()
                append_item(_existing_path_item(path, label))

    return artifacts


def _build_frontend_review_payload(step: Any) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return {}
    review_path = report_dir / "rtl_review.json"
    data = _json_read(review_path)
    if not isinstance(data, dict):
        return {}
    return {
        "path": str(review_path),
        "scope": data.get("scope", ""),
        "summary": data.get("summary", {}),
        "metrics": data.get("metrics", {}),
        "issues": data.get("issues", []),
        "source_files": data.get("source_files", []),
        "structural_probe": data.get("structural_probe", {}),
        "yosys_precheck": data.get("yosys_precheck", data.get("structural_probe", {})),
        "profiles": data.get("profiles", []),
        "next_analyzers": data.get("next_analyzers", []),
    }


def _build_frontend_elab_payload(step: Any) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return {}
    summary_path = report_dir / "elab_summary.json"
    data = _json_read(summary_path)
    if not isinstance(data, dict):
        return {}
    data["path"] = str(summary_path)
    data["readiness"] = _build_elab_readiness(data)
    data["hierarchy"] = _build_elab_hierarchy(data)
    data["next_action"] = _build_elab_next_action(data["readiness"], data["hierarchy"])
    return data


def _build_elab_readiness(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    diagnostics = data.get("diagnostics", [])
    diagnostics = diagnostics if isinstance(diagnostics, list) else []
    unresolved = data.get("unresolved_modules", [])
    unresolved = unresolved if isinstance(unresolved, list) else []
    top_module = str(summary.get("top_module") or data.get("top_module") or "").strip()
    top_found = bool(summary.get("top_found", False))
    status = str(summary.get("status") or data.get("status") or "not run").strip().lower()
    errors = int(summary.get("errors") or 0)
    warnings = int(summary.get("warnings") or 0)

    if status == "fail" or errors:
        state = "Failed"
        message = "Slang reported parse or elaboration errors. Fix diagnostics before running later steps."
    elif not top_found:
        state = "Incomplete"
        message = f"Top module {top_module or '<unset>'} was not found in the current RTL universe."
    elif unresolved:
        state = "Warning"
        message = "Some instantiated modules are not defined in the current file universe."
    else:
        state = "Ready"
        message = "Top exists and the module universe is structurally complete."

    return {
        "status": state,
        "message": message,
        "top_module": top_module or "--",
        "top_found": top_found,
        "errors": errors,
        "warnings": warnings,
        "diagnostics": len(diagnostics),
        "unresolved_modules": len(unresolved),
        "rtl_files": int(summary.get("rtl_files") or data.get("inputs", {}).get("rtl_file_count") or 0),
        "modules": int(summary.get("modules") or 0),
        "referenced_modules": int(summary.get("referenced_modules") or 0),
    }


def _build_elab_hierarchy(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    top_module = str(summary.get("top_module") or data.get("top_module") or "").strip()
    modules = data.get("modules", [])
    modules = modules if isinstance(modules, list) else []
    module_records = [item for item in modules if isinstance(item, dict)]
    module_by_name = {str(item.get("module", "")): item for item in module_records}
    top_record = module_by_name.get(top_module, {})
    top_children = [
        str(item)
        for item in (top_record.get("instantiates", []) if isinstance(top_record, dict) else [])
        if str(item).strip()
    ]
    hotspots = sorted(
        module_records,
        key=lambda item: int(item.get("instances") or 0),
        reverse=True,
    )[:8]
    unresolved = data.get("unresolved_modules", [])
    unresolved = [str(item) for item in unresolved] if isinstance(unresolved, list) else []
    referenced = data.get("referenced_modules", [])
    referenced = [str(item) for item in referenced] if isinstance(referenced, list) else []
    return {
        "top_module": top_module or "--",
        "top_children": top_children,
        "module_count": len(module_records),
        "referenced_count": len(referenced),
        "unresolved": unresolved,
        "largest_modules": [
            {
                "module": str(item.get("module", "")),
                "path": str(item.get("path", "")),
                "line": int(item.get("line") or 1),
                "instances": int(item.get("instances") or 0),
                "ports": int(item.get("ports") or 0),
                "parameters": int(item.get("parameters") or 0),
            }
            for item in hotspots
        ],
    }


def _build_elab_next_action(readiness: dict[str, Any], hierarchy: dict[str, Any]) -> dict[str, str]:
    status = str(readiness.get("status", "")).lower()
    if status == "failed":
        return {
            "title": "Fix Slang Diagnostics",
            "detail": "Open Diagnostics and jump to the reported source locations.",
            "target": "diagnostics",
        }
    if status == "incomplete":
        return {
            "title": "Fix Top Module",
            "detail": "Check the configured top module and the prepared filelist.",
            "target": "top",
        }
    if hierarchy.get("unresolved"):
        return {
            "title": "Resolve Missing Modules",
            "detail": "Add missing RTL files or fix module names before continuing.",
            "target": "unresolved",
        }
    return {
        "title": "Continue",
        "detail": "The design universe is complete. Continue to RTL Review or Lint.",
        "target": "next",
    }


def _build_frontend_lint_payload(step: Any) -> dict[str, Any]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return {}
    summary_path = report_dir / "lint_summary.json"
    data = _json_read(summary_path)
    if not isinstance(data, dict):
        return {}
    data["path"] = str(summary_path)
    return data


def _build_frontend_prepare_payload(workspace: dict[str, Any], step: Any) -> dict[str, Any]:
    report = _json_read(_step_section(step, "report").get("step", ""))
    manifest = _json_read(workspace.get("prepared_manifest", ""))
    report = report if isinstance(report, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    inputs = report.get("inputs", {})
    inputs = inputs if isinstance(inputs, dict) else {}
    rtl_files = _normalize_str_list(manifest.get("rtl_files", []))
    incdirs = _normalize_str_list(manifest.get("incdirs", []))
    defines = _normalize_str_list(manifest.get("defines", []))
    cpu_sources = _build_prepare_cpu_source_artifacts(workspace)

    contracts = _build_prepare_contracts(workspace, report)
    failed_contracts = [
        item for item in contracts
        if str(item.get("status", "")).lower() in {"missing", "failed", "error"}
    ]
    warning_contracts = [
        item for item in contracts
        if str(item.get("status", "")).lower() in {"warning", "stub", "disabled"}
    ]
    if failed_contracts:
        readiness_status = "Failed"
        readiness_message = "Prepare found missing or incompatible runtime inputs."
    elif warning_contracts:
        readiness_status = "Warning"
        readiness_message = "Prepare completed with degraded or optional runtime capabilities."
    elif rtl_files:
        readiness_status = "Ready"
        readiness_message = "Inputs are normalized and ready for ELAB, Lint, Review, and Sim."
    else:
        readiness_status = "Pending"
        readiness_message = "Run Prepare to collect and normalize RTL inputs."

    return {
        "readiness": {
            "status": readiness_status,
            "message": readiness_message,
            "rtl_files": len(rtl_files),
            "incdirs": len(incdirs),
            "defines": len(defines),
        },
        "configuration": [
            {"label": "CPU", "value": _display_workspace_value(workspace, "frontend_core_id", "cpu_wrapper_id", "core_id")},
            {"label": "SoC Harness", "value": _display_workspace_value(workspace, "soc_harness_id", "soc_wrapper_id", "soc_variant")},
            {"label": "Toolchain", "value": _display_workspace_value(workspace, "toolchain_id")},
            {"label": "Test Suite", "value": _display_workspace_value(workspace, "test_suite_id")},
            {"label": "Top Module", "value": _display_workspace_value(workspace, "top_module")},
            {"label": "Reset/Link Base", "value": _prepare_reset_link_base(workspace)},
        ],
        "inputs": {
            "cpu_rtl_files": len(cpu_sources),
            "total_rtl_files": len(rtl_files),
            "incdirs": len(incdirs),
            "defines": len(defines),
            "sources": _prepare_input_sources(inputs),
            "manifest": str(workspace.get("prepared_manifest", "")),
            "merged_filelist": str(workspace.get("prepared_filelist", "")),
        },
        "contracts": contracts,
        "runtime": [
            {"label": "Workdir", "value": str(workspace.get("directory", "")), "mono": True},
            {"label": "Sim Top", "value": str(workspace.get("top_module", "") or "ecos_sim_top")},
            {"label": "CPU Tests", "value": _prepare_cpu_tests_label(workspace)},
            {"label": "Wave Output", "value": "sim_verilator/report/cases/*.vcd", "mono": True},
            {"label": "Step Logs", "value": "*/report/log.txt", "mono": True},
        ],
        "reports": {
            "path": str(_step_section(step, "report").get("step", "")),
            "manifest": str(workspace.get("prepared_manifest", "")),
        },
    }


def _build_prepare_contracts(workspace: dict[str, Any], report: dict[str, Any]) -> list[dict[str, str]]:
    inputs = report.get("inputs", {})
    inputs = inputs if isinstance(inputs, dict) else {}
    cpu_input = inputs.get("cpu_filelist", {})
    cpu_adapter_input = inputs.get("cpu_adapter_filelist", {})
    soc_input = inputs.get("soc_filelist", {})
    input_filelist = inputs.get("input_filelist", {})
    origin_verilog = inputs.get("origin_verilog", {})
    cpu_input = cpu_input if isinstance(cpu_input, dict) else {}
    cpu_adapter_input = cpu_adapter_input if isinstance(cpu_adapter_input, dict) else {}
    soc_input = soc_input if isinstance(soc_input, dict) else {}
    input_filelist = input_filelist if isinstance(input_filelist, dict) else {}
    origin_verilog = origin_verilog if isinstance(origin_verilog, dict) else {}

    contracts: list[dict[str, str]] = []
    if input_filelist or origin_verilog:
        contracts.append({
            "label": "Custom RTL Input",
            "status": "OK" if (input_filelist.get("rtl_files") or origin_verilog.get("rtl_files")) else "Missing",
            "detail": str(input_filelist.get("path") or origin_verilog.get("path") or "No custom input found"),
        })
    else:
        contracts.extend([
            {
                "label": "CPU Filelist",
                "status": "OK" if cpu_input.get("rtl_files") else "Missing",
                "detail": _prepare_contract_detail(cpu_input, workspace.get("cpu_filelist", "")),
            },
            {
                "label": "CPU Adapter",
                "status": "OK" if cpu_adapter_input.get("rtl_files") else "Disabled",
                "detail": _prepare_contract_detail(cpu_adapter_input, workspace.get("cpu_adapter_filelist", ""), empty="Adapter not required"),
            },
            {
                "label": "SoC Harness",
                "status": "OK" if soc_input.get("rtl_files") else "Missing",
                "detail": _prepare_contract_detail(soc_input, workspace.get("soc_filelist", "")),
            },
        ])

    contracts.extend([
        {
            "label": "ecos_sim_top",
            "status": "OK" if str(workspace.get("top_module", "") or "") == "ecos_sim_top" else "Warning",
            "detail": str(workspace.get("top_module", "") or "Top module not configured"),
        },
        {
            "label": "Difftest",
            "status": "OK" if _normalize_bool(workspace.get("cpu_supports_difftest", True)) and _normalize_bool(workspace.get("soc_supports_difftest", True)) else "Stub",
            "detail": "Enabled" if _normalize_bool(workspace.get("cpu_supports_difftest", True)) and _normalize_bool(workspace.get("soc_supports_difftest", True)) else "Using stub or disabled for this CPU/SoC.",
        },
        {
            "label": "Test Suite",
            "status": "OK" if str(workspace.get("test_suite_id", "")).strip() else "Warning",
            "detail": str(workspace.get("test_suite_id", "") or "Default smoke suite"),
        },
    ])
    return contracts


def _prepare_contract_detail(data: dict[str, Any], fallback_path: Any, *, empty: str = "No RTL files found") -> str:
    path = str(data.get("path") or fallback_path or "").strip()
    files = data.get("rtl_files")
    try:
        count = int(files)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return f"{count} RTL file(s) from {path}" if path else f"{count} RTL file(s)"
    if path:
        return f"{empty}: {path}"
    skipped = str(data.get("skipped", "")).strip()
    return skipped or empty


def _prepare_input_sources(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key, label in (
        ("cpu_filelist", "CPU RTL"),
        ("cpu_adapter_filelist", "CPU Adapter"),
        ("soc_filelist", "SoC Harness"),
        ("input_filelist", "Custom Filelist"),
        ("origin_verilog", "Single RTL"),
    ):
        value = inputs.get(key, {})
        if not isinstance(value, dict):
            continue
        path = str(value.get("path", "")).strip()
        skipped = str(value.get("skipped", "")).strip()
        if not path and not skipped:
            continue
        result.append({
            "label": label,
            "path": path,
            "rtl_files": value.get("rtl_files", 0),
            "filtered_rtl_files": value.get("filtered_rtl_files", 0),
            "skipped": skipped,
        })
    return result


def _display_workspace_value(workspace: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(workspace.get(key, "") or "").strip()
        if value:
            return value
    return "--"


def _prepare_reset_link_base(workspace: dict[str, Any]) -> str:
    for key in ("sim_program_link_base", "reset_pc", "diff_reset_vector"):
        value = str(workspace.get(key, "") or "").strip()
        if value:
            return value
    args = _normalize_str_list(workspace.get("sim_run_args", []))
    for index, item in enumerate(args):
        if item in {"--diff-reset-vector", "--reset-vector"} and index + 1 < len(args):
            return args[index + 1]
    return "--"


def _prepare_cpu_tests_label(workspace: dict[str, Any]) -> str:
    if _normalize_bool(workspace.get("sim_build_all_programs", False)):
        return "All CPU tests"
    names = _normalize_str_list(workspace.get("sim_program_names", []))
    if names:
        return ", ".join(names)
    return ", ".join(_default_cpu_test_cases(workspace)) or "Default smoke"


def _build_review_source_artifacts(step: Any) -> list[dict[str, str]]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return []
    data = _json_read(report_dir / "rtl_review.json")
    if not isinstance(data, dict):
        return []
    raw_sources = data.get("source_files", [])
    if not isinstance(raw_sources, list):
        return []

    artifacts: list[dict[str, str]] = []
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        path = str(source.get("path", "")).strip()
        label = str(source.get("label", "")).strip() or Path(path).name
        item = _existing_path_item(path, label)
        if item:
            artifacts.append(item)
    return artifacts


def _build_prepare_cpu_source_artifacts(workspace: dict[str, Any]) -> list[dict[str, str]]:
    cpu_filelist = _resolve_optional_path(workspace.get("cpu_filelist", ""))
    if not cpu_filelist:
        return []

    cpu_sources = _collect_cpu_filelist_sources(cpu_filelist)
    if not cpu_sources:
        return []

    cpu_root = Path(cpu_filelist).expanduser().resolve().parent
    artifacts: list[dict[str, str]] = []
    for source in cpu_sources:
        rel = _cpu_source_relative_path(source, cpu_root)
        artifacts.append(
            {
                "label": f"CPU RTL · {rel}",
                "path": str(source),
            }
        )
    return artifacts


def _collect_cpu_filelist_sources(cpu_filelist: str) -> list[Path]:
    filelist_path = Path(cpu_filelist).expanduser().resolve()
    if not filelist_path.is_file():
        return []

    try:
        from fecompiler.tools.prepare.runner import PrepareStep

        parsed = PrepareStep._parse_sv_filelist(str(filelist_path))
        raw_files = parsed.get("rtl_files", []) if isinstance(parsed, dict) else []
    except Exception:
        return []

    collected: list[Path] = []
    seen: set[str] = set()
    for raw in raw_files:
        try:
            source_path = Path(str(raw)).expanduser().resolve()
        except Exception:
            continue
        if source_path.suffix.lower() not in {".v", ".sv", ".vh", ".svh"}:
            continue
        if not source_path.is_file():
            continue
        key = str(source_path)
        if key in seen:
            continue
        seen.add(key)
        collected.append(source_path)

    return collected


def _cpu_source_relative_path(source: Path, cpu_root: Path) -> str:
    try:
        return source.relative_to(cpu_root).as_posix()
    except ValueError:
        return source.name


def _build_frontend_sim_cases(step: Any) -> list[dict[str, Any]]:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return []
    cases_json = report_dir / "cases.json"
    data = _json_read(cases_json)
    raw_cases = data.get("cases", []) if isinstance(data, dict) else []
    if not isinstance(raw_cases, list):
        return []

    cases: list[dict[str, Any]] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            continue
        cases.append(
            {
                "name": str(raw_case.get("name", "")),
                "suite": str(raw_case.get("suite", "")),
                "ok": bool(raw_case.get("ok", False)),
                "returncode": raw_case.get("returncode"),
                "image": str(raw_case.get("image", "")),
                "log": str(raw_case.get("log") or raw_case.get("latest_log") or ""),
                "report_log": str(raw_case.get("report_log", "")),
                "run_log": str(raw_case.get("run_log", "")),
                "wave": str(raw_case.get("wave", "")),
                "run_id": str(raw_case.get("run_id", "")),
                "validation": raw_case.get("validation", {}) if isinstance(raw_case.get("validation"), dict) else {},
            }
        )
    return cases


def _sim_run_id(step: Any) -> str:
    report_dir = _optional_path(_step_section(step, "report").get("dir", ""))
    if not report_dir:
        return ""
    data = _json_read(report_dir / "cases.json")
    return str(data.get("run_id", "")) if isinstance(data, dict) else ""


def _sim_suite_label(workspace: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> str:
    case_names = [str(case.get("name", "")) for case in (cases or [])]
    if case_names:
        if case_names == ["coremark.soc"]:
            return "CoreMark"
        return "RT-Thread" if case_names == ["rtthread.soc"] else "CPU Tests"
    names = _normalize_str_list(workspace.get("sim_program_names", []))
    if names == ["rtthread"]:
        return "RT-Thread"
    if names == ["coremark"]:
        return "CoreMark"
    if workspace.get("sim_build_all_programs") or names:
        return "CPU Tests"
    return "Default"


def _sim_suite_id(workspace: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> str:
    case_names = [str(case.get("name", "")) for case in (cases or [])]
    if case_names == ["rtthread.soc"]:
        return "rtthread"
    if case_names == ["coremark.soc"]:
        return "coremark"
    names = _normalize_str_list(workspace.get("sim_program_names", []))
    if names == ["rtthread"]:
        return "rtthread"
    if names == ["coremark"]:
        return "coremark"
    if workspace.get("sim_build_all_programs") or names:
        return "cpu_tests"
    return "default"


def _sim_cpu_test_mode(workspace: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> str:
    case_names = [str(case.get("name", "")) for case in (cases or [])]
    if case_names:
        if case_names in (["rtthread.soc"], ["coremark.soc"]):
            return ""
        return "all" if len(case_names) >= len(_available_cpu_test_cases(workspace)) else "selected"
    if workspace.get("sim_build_all_programs"):
        return "all"
    if _normalize_str_list(workspace.get("sim_program_names", [])):
        return "selected"
    return ""


def _available_cpu_test_cases(workspace: dict[str, Any]) -> list[str]:
    programs_dir = _resolve_optional_path(workspace.get("sim_programs_dir", ""))
    if not programs_dir:
        return []
    path = Path(programs_dir)
    if not path.is_dir():
        return []
    return [
        source.stem
        for source in sorted(path.glob("*.c"))
        if source.stem not in DEFAULT_FRONTEND_COREMARK_CASES
    ]


def _existing_path_item(path: Any, label: str) -> dict[str, str] | None:
    path_text = str(path or "").strip()
    if not path_text:
        return None
    try:
        resolved = Path(path_text).expanduser().resolve()
    except Exception:
        return None
    if not resolved.exists():
        return None
    return {"label": label, "path": str(resolved)}


def _first_existing(directory: Path | None, names: tuple[str, ...]) -> Path | str:
    if directory is None:
        return ""
    for name in names:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return directory / names[0] if names else ""


def _optional_path(path: Any) -> Path | None:
    path_text = str(path or "").strip()
    if not path_text:
        return None
    return Path(path_text)


def _json_read(path: Any) -> Any:
    path_text = str(path or "").strip()
    if not path_text:
        return None
    try:
        resolved = Path(path_text).expanduser().resolve()
        if not resolved.is_file():
            return None
        with resolved.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_text_tail(path: Any, max_bytes: int) -> str:
    path_text = str(path or "").strip()
    if not path_text:
        return ""
    try:
        resolved = Path(path_text).expanduser().resolve()
        if not resolved.is_file():
            return ""
        with resolved.open("rb") as f:
            size = resolved.stat().st_size
            f.seek(max(size - max_bytes, 0))
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _step_section(step: Any, section: str) -> dict[str, Any]:
    value = step.get(section, {}) if hasattr(step, "get") else {}
    return value if isinstance(value, dict) else {}


def _resolve_path(value: Any, base_dir: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _resolve_optional_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve())


def _normalize_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = value.splitlines()
    else:
        items = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _command_to_cmd(command: str) -> str:
    return {
        "create": "create_workspace",
        "load": "load_workspace",
        "run-flow": "rtl2gds",
        "run-step": "run_step",
        "get-info": "get_info",
        "get-home": "home_page",
    }.get(command, "workspace")


def _emit_event(
    cmd: str,
    phase: str,
    data: dict[str, Any] | None = None,
    message: list[str] | None = None,
    *,
    json_output: bool,
) -> None:
    if not json_output:
        return
    payload = {
        "type": "event",
        "phase": phase,
        "cmd": cmd,
        "data": data or {},
        "message": message or [],
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


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


def _exit_code(response: str) -> int:
    return 0 if response in {"success", "warning"} else 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
