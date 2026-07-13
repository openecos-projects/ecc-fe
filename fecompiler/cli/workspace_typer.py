"""Typer command bindings for the ecc-fe workspace CLI.

This module owns only the Typer-facing command shape.  Workspace business
logic, JSON rendering, and compatibility behavior stay in ``workspace.py``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

typer: Any = None


@dataclass(frozen=True, slots=True)
class WorkspaceTyperHandlers:
    call_command: Callable[[str, Callable[[], Any]], Any]
    render_result: Callable[..., None]
    exit_code: Callable[[str], int]
    create: Callable[[argparse.Namespace], Any]
    catalog_list: Callable[[], Any]
    catalog_check: Callable[[], Any]
    validate_config: Callable[[argparse.Namespace], Any]
    load: Callable[[argparse.Namespace], Any]
    run_flow: Callable[[argparse.Namespace], Any]
    run_step: Callable[[argparse.Namespace], Any]
    get_info: Callable[[argparse.Namespace], Any]
    get_home: Callable[[argparse.Namespace], Any]


def build_typer_app(typer_module: Any, handlers: WorkspaceTyperHandlers) -> Any:
    """Build the Typer workspace command app without importing workspace logic."""
    global typer
    typer = typer_module

    app = typer_module.Typer(
        add_completion=False,
        no_args_is_help=True,
        rich_markup_mode=None,
        help="Manage fecompiler workspaces with ECOS Studio CLI-compatible JSON responses.",
    )

    def finish(command: str, json_output: bool, callback: Callable[[], Any]) -> None:
        result = handlers.call_command(command, callback)
        handlers.render_result(result, json_output=json_output)
        raise typer_module.Exit(code=handlers.exit_code(result.response))

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
        core_id: Annotated[str | None, typer.Option("--core-id")] = None,
        soc_variant: Annotated[str | None, typer.Option("--soc-variant")] = None,
        soc_harness_id: Annotated[str | None, typer.Option("--soc-harness-id")] = None,
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
            core_id=core_id or "",
            soc_variant=soc_variant or "",
            soc_harness_id=soc_harness_id or "",
        )
        finish("create", json_output, lambda: handlers.create(args))

    @app.command("catalog-list", help="List frontend core/SoC/toolchain/test catalogs")
    def catalog_list_cmd(
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        finish("catalog-list", json_output, handlers.catalog_list)

    @app.command("catalog-check", help="Check frontend catalog adapter contracts")
    def catalog_check_cmd(
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        finish("catalog-check", json_output, handlers.catalog_check)

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
        finish("validate-config", json_output, lambda: handlers.validate_config(args))

    @app.command("load", help="Load an existing frontend workspace")
    def load_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory)
        finish("load", json_output, lambda: handlers.load(args))

    @app.command("run-flow", help="Run the full frontend flow")
    def run_flow_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        rerun: Annotated[bool, typer.Option("--rerun")] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory, rerun=rerun)
        finish("run-flow", json_output, lambda: handlers.run_flow(args))

    @app.command("run-step", help="Run one frontend flow step")
    def run_step_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        step: Annotated[str, typer.Option("--step")] = "",
        rerun: Annotated[bool, typer.Option("--rerun")] = False,
        sim_test_suite: Annotated[str | None, typer.Option("--sim-test-suite")] = None,
        sim_cpu_test_mode: Annotated[str, typer.Option("--sim-cpu-test-mode")] = "selected",
        sim_cpu_test_case: Annotated[list[str] | None, typer.Option("--sim-cpu-test-case")] = None,
        sim_compile_preset: Annotated[str | None, typer.Option("--sim-compile-preset")] = None,
        sim_compile_opt_level: Annotated[str | None, typer.Option("--sim-compile-opt-level")] = None,
        sim_compile_march: Annotated[str | None, typer.Option("--sim-compile-march")] = None,
        sim_compile_mabi: Annotated[str | None, typer.Option("--sim-compile-mabi")] = None,
        sim_compile_extra_cflag: Annotated[list[str] | None, typer.Option("--sim-compile-extra-cflag")] = None,
        sim_coremark_iterations: Annotated[int | None, typer.Option("--sim-coremark-iterations")] = None,
        sim_coremark_total_data_size: Annotated[int | None, typer.Option("--sim-coremark-total-data-size")] = None,
        sim_coremark_max_cycles: Annotated[int | None, typer.Option("--sim-coremark-max-cycles")] = None,
        sim_coremark_has_float: Annotated[str | None, typer.Option("--sim-coremark-has-float")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(
            directory=directory,
            step=step,
            rerun=rerun,
            sim_test_suite=sim_test_suite or "",
            sim_cpu_test_mode=sim_cpu_test_mode,
            sim_cpu_test_case=list(sim_cpu_test_case or []),
            sim_compile_preset=sim_compile_preset or "",
            sim_compile_opt_level=sim_compile_opt_level or "",
            sim_compile_march=sim_compile_march or "",
            sim_compile_mabi=sim_compile_mabi or "",
            sim_compile_extra_cflag=list(sim_compile_extra_cflag or []),
            sim_coremark_iterations=sim_coremark_iterations,
            sim_coremark_total_data_size=sim_coremark_total_data_size,
            sim_coremark_max_cycles=sim_coremark_max_cycles,
            sim_coremark_has_float=sim_coremark_has_float or "",
        )
        finish("run-step", json_output, lambda: handlers.run_step(args))

    @app.command("get-info", help="Get step information")
    def get_info_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        step: Annotated[str, typer.Option("--step")] = "",
        info_id: Annotated[str, typer.Option("--id")] = "",
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory, step=step, id=info_id)
        finish("get-info", json_output, lambda: handlers.get_info(args))

    @app.command("get-home", help="Get workspace home.json")
    def get_home_cmd(
        directory: Annotated[str, typer.Option("--directory")] = "",
        json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    ) -> None:
        args = argparse.Namespace(directory=directory)
        finish("get-home", json_output, lambda: handlers.get_home(args))

    return app
