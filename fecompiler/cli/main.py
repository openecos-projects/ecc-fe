"""CLI entry point — mirrors chipcompiler/cli/main.py in ecos-studio/ecc."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.workspace import (
    CreateWorkspaceData,
    build_parameter_overrides,
    create_workspace,
    load_workspace,
)
from fecompiler.engine.flow import EngineFlow
from fecompiler.utility.json import json_read, json_write


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fecompiler",
        description="Create fecompiler workspace and run flow",
    )
    parser.add_argument("--workspace", default="",  help=(
        f"Workspace directory path (default: {DEFAULT_PROJECTS_ROOT}/<design>)"
    ))
    parser.add_argument("--design",    required=True, help="Design name")
    parser.add_argument("--top",       required=True, help="Top module name")
    parser.add_argument("--clock",     default="clk", help="Clock port name (default: clk)")
    parser.add_argument("--freq",      type=float, default=100.0,
                        help="Clock frequency in MHz (default: 100)")
    parser.add_argument("--rtl",       default="", help="RTL verilog file path (optional)")
    parser.add_argument("--filelist",  default="", help="Filelist path (optional)")
    parser.add_argument("--cpu-filelist", default="", help="CPU filelist path (optional)")
    parser.add_argument("--soc-filelist", default="", help="SoC filelist path (optional)")
    parser.add_argument("--testbench", default="", help="Main C++ testbench path for sim (optional)")
    parser.add_argument(
        "--sim-cpp",
        action="append",
        default=[],
        help="Additional C++ source for verilator sim compile (repeatable)",
    )
    parser.add_argument(
        "--sim-cflag",
        action="append",
        default=[],
        help="Extra C/C++ compiler flag passed by verilator -CFLAGS (repeatable)",
    )
    parser.add_argument(
        "--sim-ldflag",
        action="append",
        default=[],
        help="Extra linker flag passed by verilator -LDFLAGS (repeatable)",
    )
    parser.add_argument(
        "--sim-arg",
        action="append",
        default=[],
        help="Runtime argument passed to simulation binary (repeatable)",
    )
    parser.add_argument(
        "--sim-image",
        action="append",
        default=[],
        help="Simulation image path (.soc.bin), one run per image when provided (repeatable)",
    )
    parser.add_argument(
        "--sim-all-tests",
        action="store_true",
        help="Run all *.soc.bin under --sim-tests-dir",
    )
    parser.add_argument(
        "--sim-tests-dir",
        default="fecompiler/thirdparty/SoC/tests/out",
        help="Directory used by --sim-all-tests (default: fecompiler/thirdparty/SoC/tests/out)",
    )
    parser.add_argument(
        "--sim-build-all-programs",
        action="store_true",
        help="Build all C programs under --sim-programs-dir to *.soc.bin before sim",
    )
    parser.add_argument(
        "--sim-programs-dir",
        default="fecompiler/thirdparty/SoC/tests/programs",
        help="Directory of C programs used by --sim-build-all-programs (default: fecompiler/thirdparty/SoC/tests/programs)",
    )
    parser.add_argument(
        "--sim-program",
        action="append",
        default=[],
        help="Specific C program name/path to build before sim (repeatable)",
    )
    parser.add_argument(
        "--sim-tests-out-dir",
        default="",
        help="Output directory for built *.soc.bin (default: sim output cases)",
    )
    parser.add_argument(
        "--sim-only",
        action="store_true",
        help="Run only sim step on existing workspace",
    )
    parser.add_argument(
        "--sim-reuse-binary",
        action="store_true",
        help="Reuse existing sim binary if present (skip compile sub-step)",
    )
    parser.add_argument("--rerun",     action="store_true", help="Re-run all steps")
    return parser


def _resolve_sim_images(args: argparse.Namespace) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(path_text: str) -> None:
        text = str(path_text).strip()
        if not text:
            return
        p = Path(text).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        canonical = str(p.resolve())
        if canonical not in seen:
            seen.add(canonical)
            ordered.append(canonical)

    for raw in args.sim_image:
        _add(raw)

    if args.sim_all_tests:
        tests_dir = Path(args.sim_tests_dir).expanduser()
        if not tests_dir.is_absolute():
            tests_dir = Path.cwd() / tests_dir
        for image in sorted(tests_dir.glob("*.soc.bin")):
            _add(str(image))
    return ordered


def _persist_parameter_overrides(ws: dict[str, object], updates: dict[str, object]) -> None:
    if not updates:
        return
    params_path = str(ws.get("parameters_path", "")).strip()
    if not params_path:
        return
    params = json_read(params_path)
    params.update(updates)
    json_write(params_path, params)


def _workspace_dir(args: argparse.Namespace) -> str:
    workspace = args.workspace.strip() or str(DEFAULT_PROJECTS_ROOT / args.design)
    return str(Path(workspace).expanduser().resolve())


def _create_workspace(args: argparse.Namespace, workspace_dir: str,
                      sim_images: list[str]) -> dict[str, object] | None:
    spec = CreateWorkspaceData(
        directory=workspace_dir,
        parameters={
            "Design":               args.design,
            "Top module":           args.top,
            "Clock":                args.clock,
            "Frequency max [MHz]":  args.freq,
        },
        origin_verilog=args.rtl,
        filelist=args.filelist,
        cpu_filelist=args.cpu_filelist,
        soc_filelist=args.soc_filelist,
        testbench=args.testbench,
        sim_cpp_sources=args.sim_cpp,
        sim_cflags=args.sim_cflag,
        sim_ldflags=args.sim_ldflag,
        sim_run_args=args.sim_arg,
        sim_images=sim_images,
        sim_all_tests=args.sim_all_tests,
        sim_tests_dir=args.sim_tests_dir,
        sim_build_all_programs=args.sim_build_all_programs,
        sim_program_names=args.sim_program,
        sim_programs_dir=args.sim_programs_dir,
        sim_tests_out_dir=args.sim_tests_out_dir,
    )
    return create_workspace(spec)


def _runtime_overrides(args: argparse.Namespace,
                       sim_images: list[str]) -> dict[str, object]:
    updates = build_parameter_overrides(
        testbench=args.testbench.strip(),
        sim_cpp_sources=args.sim_cpp if args.sim_cpp else None,
        sim_cflags=args.sim_cflag if args.sim_cflag else None,
        sim_ldflags=args.sim_ldflag if args.sim_ldflag else None,
        sim_run_args=args.sim_arg if args.sim_arg else None,
        sim_images=sim_images if sim_images else None,
        sim_all_tests=bool(args.sim_all_tests),
        sim_tests_dir=args.sim_tests_dir if args.sim_all_tests else "",
        sim_build_all_programs=bool(args.sim_build_all_programs),
        sim_program_names=args.sim_program if args.sim_program else None,
        sim_programs_dir=args.sim_programs_dir if (args.sim_build_all_programs or args.sim_program) else "",
        sim_tests_out_dir=args.sim_tests_out_dir if (args.sim_build_all_programs or args.sim_program) else "",
    )
    if (args.sim_build_all_programs or args.sim_program) and not args.sim_tests_out_dir:
        updates["sim_tests_out_dir"] = ""
    return updates


def _print_error(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 1


def _print_step_status(step: str, state: str) -> None:
    status = "✓" if state == "Success" else "✗"
    print(f"  {status}  {step:<20} {state}")


def _run_sim_only(engine: EngineFlow) -> int:
    state = engine.run_step("sim", rerun=True)
    _print_step_status("sim", state.value)
    if state.value != "Success":
        return _print_error("sim step failed")
    return 0


def _run_full_flow(engine: EngineFlow, rerun: bool) -> int:
    ok, reports = engine.run_all(rerun=rerun)
    for report in reports:
        _print_step_status(str(report["step"]), str(report["state"]))
    if not ok:
        return _print_error("flow execution failed")
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if raw_argv[:1] == ["rpc"]:
        from fecompiler.cli.rpc import run as run_rpc

        return run_rpc(raw_argv[1:])
    if raw_argv[:1] == ["workspace"]:
        from fecompiler.cli.workspace import run as run_workspace

        return run_workspace(raw_argv[1:])

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    sim_images = _resolve_sim_images(args)
    if args.sim_all_tests and not sim_images and not args.sim_build_all_programs:
        return _print_error(f"no .soc.bin found in --sim-tests-dir={args.sim_tests_dir}")

    workspace_dir = _workspace_dir(args)

    # load existing or create new
    ws = load_workspace(workspace_dir)
    if ws is None and args.sim_only:
        return _print_error("--sim-only requires an existing workspace")

    if ws is None:
        ws = _create_workspace(args, workspace_dir, sim_images)

    if ws is None:
        return _print_error("failed to create workspace")

    updates = _runtime_overrides(args, sim_images)
    ws.update(updates)
    ws["sim_reuse_binary"] = bool(args.sim_reuse_binary or args.sim_only)
    _persist_parameter_overrides(ws, updates)

    engine = EngineFlow(workspace=ws)
    if not engine.has_init():
        engine.init_default_steps()
        engine.load()
    engine.create_step_workspaces()

    if args.sim_only:
        return _run_sim_only(engine)
    return _run_full_flow(engine, rerun=args.rerun)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
