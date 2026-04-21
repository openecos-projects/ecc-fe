"""CLI entry point — mirrors chipcompiler/cli/main.py in ecos-studio/ecc."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
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


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    sim_images = _resolve_sim_images(args)
    if args.sim_all_tests and not sim_images:
        print(
            f"Error: no .soc.bin found in --sim-tests-dir={args.sim_tests_dir}",
            file=sys.stderr,
        )
        return 1

    workspace = args.workspace.strip() or str(DEFAULT_PROJECTS_ROOT / args.design)
    workspace_dir = str(Path(workspace).expanduser().resolve())

    # load existing or create new
    ws = load_workspace(workspace_dir)
    if ws is None and args.sim_only:
        print("Error: --sim-only requires an existing workspace", file=sys.stderr)
        return 1

    if ws is None:
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
        )
        ws = create_workspace(spec)

    if ws is None:
        print("Error: failed to create workspace", file=sys.stderr)
        return 1

    updates: dict[str, object] = {}
    if args.testbench.strip():
        tb = str(Path(args.testbench).expanduser().resolve())
        ws["testbench"] = tb
        updates["testbench"] = tb
    if args.sim_cpp:
        sim_cpp = [str(Path(p).expanduser().resolve()) for p in args.sim_cpp if str(p).strip()]
        ws["sim_cpp_sources"] = sim_cpp
        updates["sim_cpp_sources"] = sim_cpp
    if args.sim_cflag:
        sim_cflags = [str(f).strip() for f in args.sim_cflag if str(f).strip()]
        ws["sim_cflags"] = sim_cflags
        updates["sim_cflags"] = sim_cflags
    if args.sim_ldflag:
        sim_ldflags = [str(f).strip() for f in args.sim_ldflag if str(f).strip()]
        ws["sim_ldflags"] = sim_ldflags
        updates["sim_ldflags"] = sim_ldflags
    if args.sim_arg:
        sim_args = [str(a) for a in args.sim_arg if str(a)]
        ws["sim_run_args"] = sim_args
        updates["sim_run_args"] = sim_args
    if sim_images:
        ws["sim_images"] = sim_images
        updates["sim_images"] = sim_images
    ws["sim_reuse_binary"] = bool(args.sim_reuse_binary or args.sim_only)
    _persist_parameter_overrides(ws, updates)

    engine = EngineFlow(workspace=ws)
    if not engine.has_init():
        engine.init_default_steps()
        engine.load()
    engine.create_step_workspaces()

    if args.sim_only:
        state = engine.run_step("sim", rerun=True)
        status = "✓" if state.value == "Success" else "✗"
        print(f"  {status}  {'sim':<20} {state.value}")
        if state.value != "Success":
            print("Error: sim step failed", file=sys.stderr)
            return 1
        return 0

    ok, reports = engine.run_all(rerun=args.rerun)
    for r in reports:
        status = "✓" if r["state"] == "Success" else "✗"
        print(f"  {status}  {r['step']:<20} {r['state']}")

    if not ok:
        print("Error: flow execution failed", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
