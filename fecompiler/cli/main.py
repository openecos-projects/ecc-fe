"""CLI entry point — mirrors chipcompiler/cli/main.py in ecos-studio/ecc."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from fecompiler.config import DEFAULT_PROJECTS_ROOT
from fecompiler.data.workspace import CreateWorkspaceData, create_workspace, load_workspace
from fecompiler.engine.flow import EngineFlow


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
    parser.add_argument("--rerun",     action="store_true", help="Re-run all steps")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    workspace = args.workspace.strip() or str(DEFAULT_PROJECTS_ROOT / args.design)
    workspace_dir = str(Path(workspace).expanduser().resolve())

    # load existing or create new
    ws = load_workspace(workspace_dir)
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
        )
        ws = create_workspace(spec)

    if ws is None:
        print("Error: failed to create workspace", file=sys.stderr)
        return 1

    engine = EngineFlow(workspace=ws)
    if not engine.has_init():
        engine.init_default_steps()
        engine.load()
    engine.create_step_workspaces()

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
