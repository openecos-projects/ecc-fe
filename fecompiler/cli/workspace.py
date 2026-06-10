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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fecompiler.data.step import StateEnum
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow
from fecompiler.utility.json import json_read, json_write


DEFAULT_FRONTEND_SMOKE_TEST_CASES = ["add", "load-store"]

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
    run_step.add_argument("--sim-cpu-test-mode", default="all")
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
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = _dispatch(args)
    except WorkspaceCliError as exc:
        result = exc.result
    except Exception as exc:
        cmd = _command_to_cmd(getattr(args, "command", "workspace"))
        result = CliResult(cmd=cmd, response="error", data={}, message=[str(exc)])

    _render_result(result, json_output=bool(getattr(args, "json", False)))
    return _exit_code(result.response)


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def _dispatch(args: argparse.Namespace) -> CliResult:
    command = str(args.command)
    if command == "create":
        return _create(args)
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


def _create(args: argparse.Namespace) -> CliResult:
    request, base_dir = _create_request_from_args(args)
    normalized = _normalize_create_request(request, base_dir)
    directory = str(normalized.get("directory", "")).strip()
    if not directory:
        raise WorkspaceCliError("create_workspace", "failed", "missing required field: directory")

    parameters = _normalize_parameters(normalized.get("parameters", {}))
    parameters.setdefault("Design Tool", "frontend")
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
        sim_program_names=_normalize_str_list(normalized.get("sim_program_names", [])),
        sim_program_sources=_normalize_str_list(normalized.get("sim_program_sources", [])),
        sim_programs_dir=str(normalized.get("sim_programs_dir", "")),
        sim_tests_out_dir=str(normalized.get("sim_tests_out_dir", "")),
        sim_soc_root=str(normalized.get("sim_soc_root", "")),
        sim_build_test_script=str(normalized.get("sim_build_test_script", "")),
        rtl_list=_normalize_str_list(normalized.get("rtl_list", [])),
    )
    workspace = create_workspace(spec)
    if workspace is None:
        raise WorkspaceCliError("create_workspace", "failed", f"create frontend workspace failed: {directory}")

    engine = _build_engine(workspace)
    engine.create_step_workspaces()
    return CliResult(
        cmd="create_workspace",
        response="success",
        data={"directory": workspace["directory"], "workspace_id": workspace["directory"]},
        message=[f"create frontend workspace success: {workspace['directory']}"],
    )


def _load(args: argparse.Namespace) -> CliResult:
    workspace, _ = _load_runtime(args.directory, cmd="load_workspace")
    return CliResult(
        cmd="load_workspace",
        response="success",
        data={"directory": workspace["directory"], "workspace_id": workspace["directory"]},
        message=[f"load frontend workspace success: {workspace['directory']}"],
    )


def _run_flow(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(args.directory, cmd="rtl2gds")
    if args.rerun:
        engine.clear_states()

    json_output = bool(getattr(args, "json", False))
    reports: list[dict[str, Any]] = []
    failed_step = ""
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
            break

    data: dict[str, Any] = {"rerun": bool(args.rerun), "reports": reports}
    if failed_step:
        data["failed_step"] = failed_step
        return CliResult(
            cmd="rtl2gds",
            response="failed",
            data=data,
            message=[f"run frontend flow failed in step: {failed_step}"],
        )
    return CliResult(
        cmd="rtl2gds",
        response="success",
        data=data,
        message=[f"run frontend flow success: {workspace['directory']}"],
    )


def _run_step(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(args.directory, cmd="run_step")
    step = str(args.step).strip()
    if not step:
        raise WorkspaceCliError("run_step", "failed", "missing required field: step")

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
    workspace_step = engine.get_workspace_step(step)
    data: dict[str, Any] = {"step": step, "state": state.value, "directory": workspace["directory"]}
    if workspace_step is not None:
        data.update(_step_report_payload(workspace, workspace_step, state))
    phase = "completed" if state == StateEnum.Success else "failed"
    response = "success" if state == StateEnum.Success else "failed"
    message = f"run frontend step {step} {response}: {workspace['directory']}"
    _emit_event("run_step", phase, data, [message], json_output=json_output)
    return CliResult(cmd="run_step", response=response, data=data, message=[message])


def _get_info(args: argparse.Namespace) -> CliResult:
    workspace, engine = _load_runtime(args.directory, cmd="get_info")
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


def _apply_sim_test_suite(
    workspace: dict[str, Any],
    suite: Any,
    cpu_test_mode: Any = "all",
    cpu_test_cases: Any = None,
) -> None:
    suite_name = str(suite or "").strip()
    if not suite_name or suite_name == "default":
        return

    if suite_name == "cpu_tests":
        mode = str(cpu_test_mode or "all").strip().lower()
        cases = _normalize_str_list(cpu_test_cases)
        if mode not in {"all", "selected"}:
            raise WorkspaceCliError("run_step", "failed", f"unknown CPU Tests mode: {mode}")
        if mode == "selected" and not cases:
            raise WorkspaceCliError("run_step", "failed", "select at least one CPU test case")
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
        updates = {
            "sim_all_tests": False,
            "sim_images": [],
            "sim_build_all_programs": False,
            "sim_program_names": ["rtthread"],
            "sim_run_args": ["--max-cycles", "10000000", "--wave", "/dev/null"],
        }
    else:
        raise WorkspaceCliError("run_step", "failed", f"unknown frontend sim test suite: {suite_name}")

    _update_workspace_parameters(workspace, updates)


def _apply_default_sim_smoke_suite(workspace: dict[str, Any]) -> None:
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
        return {
            "step": step.name,
            "tool": step.tool,
            "state": str((flow_step or {}).get("state", "Unstart")),
            "runtime": str((flow_step or {}).get("runtime", "")),
            "log": _step_section(step, "log").get("file", ""),
            "report": _step_section(step, "report").get("step", ""),
            "subflow": _step_section(step, "subflow").get("path", ""),
            "home_page": workspace.get("home_path", ""),
        }
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
    return {
        "step": workspace_step.name,
        "tool": workspace_step.tool,
        "state": state.value,
        "log_file": workspace_step.log.get("file", ""),
        "subflow_path": workspace_step.subflow.get("path", ""),
        "home_page": workspace.get("home_path", ""),
    }


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
