"""ECOS Studio workspace CLI for fecompiler.

This command shape mirrors the Electron CLI bridge used by ecos-studio main:

    python3 -m fecompiler.cli.main workspace create --input-json request.json --json
    python3 -m fecompiler.cli.main workspace load --directory <workspace> --json
    python3 -m fecompiler.cli.main workspace run-flow --directory <workspace> --json
    python3 -m fecompiler.cli.main workspace run-step --directory <workspace> --step sim --json
    python3 -m fecompiler.cli.main workspace get-info --directory <workspace> --step sim --id subflow --json
    python3 -m fecompiler.cli.main workspace get-home --directory <workspace> --json
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
from typing import Annotated, Any

from fecompiler.catalog import catalog_payload, check_catalog_contracts, validate_frontend_config
from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow
from fecompiler.soc import soc_runtime_options
from fecompiler.utility.json import json_read, json_write

try:
    import click
    import typer
except ImportError:
    click = None
    typer = None


DEFAULT_FRONTEND_SMOKE_TEST_CASES = ["add"]
CLI_LOG_TAIL_BYTES = 24 * 1024
DIFFTEST_SOURCE_NAME = "difftest.cpp"
DIFFTEST_STUB_SOURCE_NAME = "difftest_stub.cpp"

_PATH_FIELDS = {
    "directory",
    "origin_def",
    "origin_verilog",
    "filelist",
    "cpu_filelist",
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
        prog="fecompiler workspace",
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
            prog_name="fecompiler workspace",
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
    """Build the Typer workspace command app without making Typer a hard import."""
    if typer_module is None:
        _, typer_module = _load_typer_modules()

    app = typer_module.Typer(
        add_completion=False,
        no_args_is_help=True,
        rich_markup_mode=None,
        help="Manage fecompiler workspaces with ECOS Studio CLI-compatible JSON responses.",
    )

    def finish(command: str, json_output: bool, callback: Callable[[], CliResult]) -> None:
        result = _call_command(command, callback)
        _render_result(result, json_output=json_output)
        raise typer_module.Exit(code=_exit_code(result.response))

    @app.command("create", help="Create a frontend workspace")
    def create_cmd(
        input_json: Annotated[
            str | None,
            typer.Option("--input-json", help="Workspace create request JSON path, or '-' for stdin"),
        ] = None,
        directory: Annotated[str | None, typer.Option("--directory", help="Workspace directory")] = None,
        design: Annotated[str | None, typer.Option("--design", help="Design name")] = None,
        top: Annotated[str | None, typer.Option("--top", help="Top module name")] = None,
        clock: Annotated[str | None, typer.Option("--clock", help="Clock port name")] = None,
        freq: Annotated[float | None, typer.Option("--freq", help="Clock frequency in MHz")] = None,
        origin_def: Annotated[str | None, typer.Option("--origin-def")] = None,
        origin_verilog: Annotated[str | None, typer.Option("--origin-verilog")] = None,
        filelist: Annotated[str | None, typer.Option("--filelist")] = None,
        cpu_filelist: Annotated[str | None, typer.Option("--cpu-filelist")] = None,
        soc_filelist: Annotated[str | None, typer.Option("--soc-filelist")] = None,
        testbench: Annotated[str | None, typer.Option("--testbench")] = None,
        sim_cpp: Annotated[list[str] | None, typer.Option("--sim-cpp")] = None,
        sim_cflag: Annotated[list[str] | None, typer.Option("--sim-cflag")] = None,
        sim_ldflag: Annotated[list[str] | None, typer.Option("--sim-ldflag")] = None,
        sim_arg: Annotated[list[str] | None, typer.Option("--sim-arg")] = None,
        sim_image: Annotated[list[str] | None, typer.Option("--sim-image")] = None,
        sim_program: Annotated[list[str] | None, typer.Option("--sim-program")] = None,
        sim_program_source: Annotated[list[str] | None, typer.Option("--sim-program-source")] = None,
        sim_all_tests: Annotated[bool, typer.Option("--sim-all-tests")] = False,
        sim_build_all_programs: Annotated[bool, typer.Option("--sim-build-all-programs")] = False,
        sim_tests_dir: Annotated[str | None, typer.Option("--sim-tests-dir")] = None,
        sim_programs_dir: Annotated[str | None, typer.Option("--sim-programs-dir")] = None,
        sim_tests_out_dir: Annotated[str | None, typer.Option("--sim-tests-out-dir")] = None,
        sim_soc_root: Annotated[str | None, typer.Option("--sim-soc-root")] = None,
        sim_build_test_script: Annotated[str | None, typer.Option("--sim-build-test-script")] = None,
        rtl: Annotated[list[str] | None, typer.Option("--rtl", help="RTL source path; repeatable")] = None,
        soc_variant: Annotated[str | None, typer.Option("--soc-variant")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(
            input_json=input_json or "",
            directory=directory or "",
            design=design or "",
            top=top or "",
            clock=clock or "",
            freq=freq,
            origin_def=origin_def or "",
            origin_verilog=origin_verilog or "",
            filelist=filelist or "",
            cpu_filelist=cpu_filelist or "",
            soc_filelist=soc_filelist or "",
            testbench=testbench or "",
            sim_cpp=list(sim_cpp or []),
            sim_cflag=list(sim_cflag or []),
            sim_ldflag=list(sim_ldflag or []),
            sim_arg=list(sim_arg or []),
            sim_image=list(sim_image or []),
            sim_program=list(sim_program or []),
            sim_program_source=list(sim_program_source or []),
            sim_all_tests=sim_all_tests,
            sim_build_all_programs=sim_build_all_programs,
            sim_tests_dir=sim_tests_dir or "",
            sim_programs_dir=sim_programs_dir or "",
            sim_tests_out_dir=sim_tests_out_dir or "",
            sim_soc_root=sim_soc_root or "",
            sim_build_test_script=sim_build_test_script or "",
            rtl=list(rtl or []),
            soc_variant=soc_variant or "",
        )
        finish("create", json_output, lambda: _create(args))

    @app.command("catalog-list", help="List frontend core/SoC/toolchain/test catalogs")
    def catalog_list_cmd(
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        finish("catalog-list", json_output, _catalog_list)

    @app.command("catalog-check", help="Check frontend catalog adapter contracts")
    def catalog_check_cmd(
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        finish("catalog-check", json_output, _catalog_check)

    @app.command("validate-config", help="Validate a frontend catalog configuration")
    def validate_config_cmd(
        input_json: Annotated[
            str | None,
            typer.Option("--input-json", help="Frontend config JSON path, or '-' for stdin"),
        ] = None,
        core_id: Annotated[str | None, typer.Option("--core-id")] = None,
        soc_harness_id: Annotated[str | None, typer.Option("--soc-harness-id")] = None,
        toolchain_id: Annotated[str | None, typer.Option("--toolchain-id")] = None,
        test_suite_id: Annotated[str | None, typer.Option("--test-suite-id")] = None,
        cpu_filelist: Annotated[str | None, typer.Option("--cpu-filelist")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(
            input_json=input_json or "",
            core_id=core_id or "",
            soc_harness_id=soc_harness_id or "",
            toolchain_id=toolchain_id or "",
            test_suite_id=test_suite_id or "",
            cpu_filelist=cpu_filelist or "",
        )
        finish("validate-config", json_output, lambda: _validate_config(args))

    @app.command("load", help="Load an existing frontend workspace")
    def load_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory)
        finish("load", json_output, lambda: _load(args))

    @app.command("run-flow", help="Run the full frontend flow")
    def run_flow_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        rerun: Annotated[bool, typer.Option("--rerun")] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory, rerun=rerun)
        finish("run-flow", json_output, lambda: _run_flow(args))

    @app.command("run-step", help="Run one frontend flow step")
    def run_step_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        step: Annotated[str, typer.Option("--step")] = "",
        rerun: Annotated[bool, typer.Option("--rerun")] = False,
        sim_test_suite: Annotated[str | None, typer.Option("--sim-test-suite")] = None,
        sim_cpu_test_mode: Annotated[str, typer.Option("--sim-cpu-test-mode")] = "selected",
        sim_cpu_test_case: Annotated[list[str] | None, typer.Option("--sim-cpu-test-case")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(
            directory=directory,
            step=step,
            rerun=rerun,
            sim_test_suite=sim_test_suite or "",
            sim_cpu_test_mode=sim_cpu_test_mode,
            sim_cpu_test_case=list(sim_cpu_test_case or []),
        )
        finish("run-step", json_output, lambda: _run_step(args))

    @app.command("get-info", help="Get step information")
    def get_info_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        step: Annotated[str, typer.Option("--step")] = "",
        info_id: Annotated[str, typer.Option("--id")] = "",
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory, step=step, id=info_id)
        finish("get-info", json_output, lambda: _get_info(args))

    @app.command("get-home", help="Get workspace home.json")
    def get_home_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory)
        finish("get-home", json_output, lambda: _get_home(args))

    return app


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
    parameters["cpu_supports_difftest"] = bool(validation.normalized.get("cpu_supports_difftest", True))
    parameters["core_supported_test_suites"] = validation.normalized.get("core_supported_test_suites", [])
    if validation.normalized.get("core_sim_program_link_base"):
        parameters["sim_program_link_base"] = validation.normalized["core_sim_program_link_base"]
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
    for workspace_step in engine.workspace_steps:
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
    if step == "sim":
        suite_name = str(args.sim_test_suite or "").strip()
        if suite_name and suite_name.lower() != "default":
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
        ("soc_filelist", "soc_filelist"),
        ("testbench", "testbench"),
        ("sim_tests_dir", "sim_tests_dir"),
        ("sim_programs_dir", "sim_programs_dir"),
        ("sim_tests_out_dir", "sim_tests_out_dir"),
        ("sim_soc_root", "sim_soc_root"),
        ("sim_build_test_script", "sim_build_test_script"),
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
    request["cpu_supports_difftest"] = bool(normalized.get("cpu_supports_difftest", True))
    request["soc_supports_difftest"] = bool(normalized.get("soc_supports_difftest", True))
    request["core_supported_test_suites"] = normalized.get("core_supported_test_suites", [])
    request["soc_supported_test_suites"] = normalized.get("soc_supported_test_suites", [])
    if normalized.get("soc_variant"):
        request["soc_variant"] = normalized["soc_variant"]
    if normalized.get("core_sim_program_link_base"):
        request["sim_program_link_base"] = normalized["core_sim_program_link_base"]


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
    for field in (
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
    ):
        if str(data.get(field, "")).strip():
            continue
        value = defaults.get(field, "")
        if value:
            data[field] = value
            changed = True

    for field in ("sim_cpp_sources", "sim_cflags", "sim_ldflags"):
        if _normalize_str_list(data.get(field, [])):
            continue
        values = _normalize_str_list(defaults.get(field, []))
        if values:
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
    for field in (
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
    ):
        if str(workspace.get(field, "")).strip():
            continue
        value = defaults.get(field, "")
        if value:
            updates[field] = value

    for field in ("sim_cflags", "sim_ldflags"):
        if _normalize_str_list(workspace.get(field, [])):
            continue
        values = _normalize_str_list(defaults.get(field, []))
        if values:
            updates[field] = values

    existing_sources = _normalize_str_list(workspace.get("sim_cpp_sources", []))
    default_sources = _normalize_str_list(defaults.get("sim_cpp_sources", []))
    source_base = existing_sources or default_sources
    adapted_sources = _adapt_sim_cpp_sources_for_cpu(workspace, source_base)
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
    if name == "SoC2":
        return "ysyx-am-soc-alt"
    if name == "SoC3":
        return "ysyx-am-soc-extended"
    return "ysyx-am-soc"


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
    else:
        raise WorkspaceCliError("run_step", "failed", f"unknown frontend sim test suite: {suite_name}")

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

    available = [source.stem for source in sorted(path.glob("*.c"))]
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


def _validate_workspace_test_suite_supported(workspace: dict[str, Any], suite_id: str) -> None:
    supported = _workspace_supported_test_suites(workspace)
    if supported and suite_id not in supported:
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
    core_supported = _normalize_str_list(workspace.get("core_supported_test_suites", []))
    soc_supported = _normalize_str_list(workspace.get("soc_supported_test_suites", []))
    if core_supported and soc_supported:
        return [suite for suite in core_supported if suite in soc_supported]
    if core_supported:
        return [suite for suite in core_supported if suite in _fallback_soc_supported_test_suites(workspace)]
    if soc_supported:
        return [suite for suite in _fallback_core_supported_test_suites(workspace) if suite in soc_supported]

    core_fallback = _fallback_core_supported_test_suites(workspace)
    soc_fallback = _fallback_soc_supported_test_suites(workspace)
    if core_fallback and soc_fallback:
        return [suite for suite in core_fallback if suite in soc_fallback]
    if core_fallback:
        return core_fallback
    return soc_fallback


def _fallback_core_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    if core_id in {"picorv32", "scr1", "ibex", "cv32e40p", "serv", "femtorv32", "darkriscv"}:
        return ["cpu-tests", "smoke"]
    if core_id in {"custom-filelist", "ysyx_00000000", ""}:
        return ["smoke", "cpu-tests", "rtthread"]
    return []


def _fallback_soc_supported_test_suites(workspace: dict[str, Any]) -> list[str]:
    soc_id = str(workspace.get("soc_wrapper_id") or workspace.get("soc_harness_id") or "").strip()
    if soc_id == "ysyx-am-soc":
        return ["smoke", "cpu-tests", "rtthread"]
    if soc_id in {
        "ysyx-am-soc-alt",
        "ysyx-am-soc-extended",
        "minimal-riscv-soc",
        "corev-mini-soc",
        "femtorv-mini-soc",
    }:
        return ["smoke", "cpu-tests"]
    return []


def _cpu_supports_difftest(workspace: dict[str, Any]) -> bool:
    raw = workspace.get("cpu_supports_difftest")
    if raw is not None:
        return _normalize_bool(raw)
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "").strip()
    if core_id in {"picorv32", "scr1", "ibex", "cv32e40p", "serv", "femtorv32", "darkriscv"}:
        return False
    return True


def _soc_supports_difftest(workspace: dict[str, Any]) -> bool:
    raw = workspace.get("soc_supports_difftest")
    if raw is not None:
        return _normalize_bool(raw)
    soc_id = str(workspace.get("soc_wrapper_id") or workspace.get("soc_harness_id") or "").strip()
    if soc_id in {"minimal-riscv-soc", "corev-mini-soc", "femtorv-mini-soc"}:
        return False
    return True


def _supports_difftest(workspace: dict[str, Any]) -> bool:
    return _cpu_supports_difftest(workspace) and _soc_supports_difftest(workspace)


def _workspace_core_label(workspace: dict[str, Any]) -> str:
    core_id = str(workspace.get("cpu_wrapper_id") or workspace.get("frontend_core_id") or "selected CPU").strip()
    if core_id == "picorv32":
        return "PicoRV32"
    return core_id or "selected CPU"


def _workspace_soc_label(workspace: dict[str, Any]) -> str:
    soc_id = str(workspace.get("soc_wrapper_id") or workspace.get("soc_harness_id") or "selected SoC").strip()
    if soc_id == "minimal-riscv-soc":
        return "Minimal RISC-V SoC"
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
    return data


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
        return "RT-Thread" if case_names == ["rtthread.soc"] else "CPU Tests"
    names = _normalize_str_list(workspace.get("sim_program_names", []))
    if names == ["rtthread"]:
        return "RT-Thread"
    if workspace.get("sim_build_all_programs") or names:
        return "CPU Tests"
    return "Default"


def _sim_cpu_test_mode(workspace: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> str:
    case_names = [str(case.get("name", "")) for case in (cases or [])]
    if case_names:
        if case_names == ["rtthread.soc"]:
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
    return [source.stem for source in sorted(path.glob("*.c"))]


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
