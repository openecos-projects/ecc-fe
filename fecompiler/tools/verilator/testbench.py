"""Simulate an RTL design with the user's own testbench.

The testbench is SystemVerilog: Verilator builds it together with the design
into one program (``--binary``) and the program is run once. The run passes
when it exits with status 0, prints nothing that matches ``testbench.fail_pattern``
and, when ``testbench.pass_pattern`` is set, prints something that matches it.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from fecompiler.data.workspace import WorkspaceStep
from fecompiler.tools.common.process import run_tool, tool_timeout
from fecompiler.tools.common.filelist import RTL_HEADER_SUFFIXES
from fecompiler.tools.common.rtl_inputs import (
    defines,
    rtl_files,
    verilator_define_args,
    verilator_incdir_args,
    verilator_library_args,
)
from fecompiler.tools.common.sv_module import (
    macro_names,
    module_definitions,
    module_parameter_names,
)
from fecompiler.tools.common.tool_config import (
    config_list,
    config_paths,
    config_text,
    design_parameter_overrides,
)
from fecompiler.tools.common.rtl_ownership import rtl_ownership_map, rtl_source_ownership
from fecompiler.tools.fe.base import BaseStep
from fecompiler.tools.fe.subflow import update_substep_ok
from fecompiler.tools.verilator import coverage
from fecompiler.tools.verilator.command import verilator_cmd
from fecompiler.tools.verilator.subflow import SimSubFlowEnum, init_sim_subflow
from fecompiler.utility.json import json_read, json_write

REPORT_KIND = "testbench"
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
# How much of the output a failure quotes.
_EVIDENCE_LIMIT = 300


class TestbenchSettingsError(ValueError):
    """The testbench settings cannot be used as they are."""


def testbench_settings(workspace: dict[str, Any]) -> dict[str, Any]:
    """Return the validated ``testbench.*`` settings of *workspace*."""
    top = config_text(workspace, "testbench_top")
    if not _MODULE_NAME.fullmatch(top):
        raise TestbenchSettingsError(
            f"testbench.top must be a module name, got {top!r}"
        )
    files = config_paths(workspace, "testbench_files")
    if not files:
        raise TestbenchSettingsError(
            "testbench.files is empty; name the files that define the testbench"
        )
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise TestbenchSettingsError(
            "testbench.files not found: " + ", ".join(missing)
        )
    patterns: dict[str, re.Pattern[str] | None] = {}
    for name in ("pass_pattern", "fail_pattern"):
        text = config_text(workspace, f"testbench_{name}")
        try:
            patterns[name] = re.compile(text, re.MULTILINE) if text else None
        except re.error as error:
            raise TestbenchSettingsError(
                f"testbench.{name} is not a valid regular expression: {error}"
            ) from error
    try:
        overrides = design_parameter_overrides(workspace)
    except ValueError as error:
        raise TestbenchSettingsError(str(error)) from error
    sources = [str(path) for path in files if path.suffix.lower() not in RTL_HEADER_SUFFIXES]
    if not sources:
        raise TestbenchSettingsError(
            "testbench.files names only headers; name the file that defines the testbench"
        )
    applied, not_applied = split_parameter_overrides(
        overrides, testbench_parameters(sources, top, defines(workspace))
    )
    return {
        "top": top,
        # Every listed file is tracked; a header is included, not compiled.
        "files": [str(path) for path in files],
        "sources": sources,
        "parameters": applied,
        "parameters_not_applied": not_applied,
        "coverage": str(config_text(workspace, "testbench_coverage")).strip().lower()
        in {"1", "true", "yes", "on"},
        "plusargs": config_list(workspace, "testbench_plusargs"),
        "extra_args": config_list(workspace, "testbench_extra_args"),
        **patterns,
    }


def testbench_parameters(
    sources: list[str], top: str, defines: list[str] | None = None
) -> list[str]:
    """Return the parameters the testbench module *top* declares."""
    # Verilator defines this one itself.
    macros = macro_names(defines) | {"VERILATOR"}
    for path in module_definitions(sources, top, defined_macros=macros):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        return module_parameter_names(text, top, defined_macros=macros)
    return []


def split_parameter_overrides(
    overrides: list[str], declared: list[str]
) -> tuple[list[str], list[str]]:
    """Split ``design.parameters`` into what the testbench takes and the rest.

    The simulated top module is the testbench, and a tool can override the
    parameters of the top module only. An override reaches the design when
    the testbench declares a parameter of that name and passes it on.
    """
    applied = [item for item in overrides if item.split("=", 1)[0].strip() in declared]
    return applied, [item for item in overrides if item not in applied]


def judge_run(
    returncode: int,
    output: str,
    *,
    timed_out: bool,
    pass_pattern: re.Pattern[str] | None,
    fail_pattern: re.Pattern[str] | None,
) -> dict[str, str] | None:
    """Return why the run failed, or ``None`` when it passed."""
    if timed_out:
        return {
            "kind": "timeout",
            "message": "the simulation did not finish; does the testbench call $finish?",
        }
    if returncode != 0:
        return {
            "kind": "exit_status",
            "message": f"the simulation exited with status {returncode}",
        }
    if fail_pattern is not None:
        match = fail_pattern.search(output)
        if match is not None:
            return {
                "kind": "fail_pattern",
                "message": "the output matches testbench.fail_pattern: "
                + _line_of(output, match.start()),
            }
    if pass_pattern is not None and pass_pattern.search(output) is None:
        return {
            "kind": "pass_pattern_missing",
            "message": "the output does not match testbench.pass_pattern",
        }
    return None


def _line_of(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    return text[start : end if end >= 0 else len(text)].strip()[:_EVIDENCE_LIMIT]


class TestbenchSimStep(BaseStep):
    """Build the design with its testbench and run it once.

    Sub-steps: compile → simulate → report
    """

    def run(self, step: WorkspaceStep, workspace: dict[str, Any]) -> None:
        init_sim_subflow(step)
        report_dir = Path(step.report["dir"])
        report_dir.mkdir(parents=True, exist_ok=True)
        log_path = report_dir / "log.txt"
        case: dict[str, Any] = {
            "name": config_text(workspace, "testbench_top") or "testbench",
            "ok": False,
            "returncode": None,
            "log": str(log_path),
            "metrics": None,
            "failure": None,
        }
        try:
            settings = testbench_settings(workspace)
        except TestbenchSettingsError as error:
            log_path.write_text(f"%Error: {error}\n", encoding="utf-8")
            case["failure"] = {"kind": "settings", "message": str(error)}
            update_substep_ok(
                step, SimSubFlowEnum.compile.value, False, info={"error": str(error)}
            )
        else:
            program = self._compile(step, workspace, settings, case)
            if program is not None:
                self._simulate(step, program, settings, case)
        report_coverage: dict[str, Any] | None = None
        if case["failure"] is None or case["failure"]["kind"] not in {"settings", "compile"}:
            report_coverage = self._coverage(step, workspace, settings)
        if report_coverage is None:
            (report_dir / coverage.REPORT_NAME).unlink(missing_ok=True)
        if case["failure"] is not None and case["failure"]["kind"] in {"settings", "compile"}:
            update_substep_ok(
                step, SimSubFlowEnum.simulate.value, False, info={"skipped": "not compiled"}
            )
        json_write(
            str(report_dir / "cases.json"),
            {
                "schema_version": 1,
                "kind": REPORT_KIND,
                "suite": REPORT_KIND,
                "cases": [case],
                **({"coverage": report_coverage} if report_coverage is not None else {}),
            },
        )
        json_write(
            step.report["step"],
            {
                "sim": "pass" if case["ok"] else "fail",
                "testbench": case["name"],
                "failure": case["failure"],
            },
        )
        update_substep_ok(step, SimSubFlowEnum.report.value, True)

    def check_result(self, step: WorkspaceStep) -> bool:
        report = json_read(str(Path(step.report["dir"]) / "cases.json"))
        cases = report.get("cases") if isinstance(report, dict) else None
        return bool(cases) and all(
            isinstance(case, dict) and case.get("ok") is True for case in cases
        )

    def _compile(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        settings: dict[str, Any],
        case: dict[str, Any],
    ) -> Path | None:
        build_dir = Path(step.directory) / "obj_dir"
        if build_dir.exists():
            shutil.rmtree(build_dir)
        output_dir = Path(step.output["dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        program = output_dir / f"{settings['top']}_sim"
        program.unlink(missing_ok=True)
        # A testbench includes its own headers relative to where it is.
        testbench_dirs = list(dict.fromkeys(str(Path(path).parent) for path in settings["files"]))
        case["design_parameters"] = {
            "applied": settings["parameters"],
            "not_applied": settings["parameters_not_applied"],
        }
        command = [
            verilator_cmd(),
            "--binary",
            "--timing",
            "--trace",
            "-Wno-fatal",
            *(coverage.BUILD_ARGS if settings["coverage"] else ()),
            "-j",
            "0",
            *verilator_incdir_args(workspace),
            *(f"+incdir+{directory}" for directory in testbench_dirs),
            *verilator_library_args(workspace),
            *verilator_define_args(workspace),
            *(f"-G{item}" for item in settings["parameters"]),
            *settings["extra_args"],
            "--top-module",
            settings["top"],
            "--Mdir",
            str(build_dir),
            "-o",
            str(program),
            *rtl_files(workspace),
            *settings["sources"],
        ]
        compile_log = Path(step.log["dir"]) / "log.txt"
        compile_log.parent.mkdir(parents=True, exist_ok=True)
        timeout = tool_timeout("sim_compile")
        try:
            result = run_tool(
                command,
                log_path=compile_log,
                timeout=timeout,
                session_dir=Path(step.log["dir"]),
            )
            ok = result.returncode == 0 and program.is_file()
            output = result.output
            if result.timed_out:
                output += f"\n%Error: the build did not finish within {timeout:g}s\n"
        except OSError as error:
            ok = False
            output = f"%Error: failed to execute verilator: {error}\n"
        if ok:
            update_substep_ok(step, SimSubFlowEnum.compile.value, True)
            return program
        Path(case["log"]).write_text(output, encoding="utf-8")
        case["failure"] = {
            "kind": "compile",
            "message": _first_error(output) or "the testbench did not build",
        }
        update_substep_ok(
            step,
            SimSubFlowEnum.compile.value,
            False,
            info={"error": case["failure"]["message"]},
        )
        return None

    def _simulate(
        self,
        step: WorkspaceStep,
        program: Path,
        settings: dict[str, Any],
        case: dict[str, Any],
    ) -> None:
        timeout = tool_timeout("sim_case")
        coverage_args: list[str] = []
        if settings["coverage"]:
            data = _coverage_data(program)
            data.unlink(missing_ok=True)
            # The program Verilator builds writes its coverage where this says.
            coverage_args = [f"+verilator+coverage+file+{data}"]
        try:
            result = run_tool(
                [str(program), *settings["plusargs"], *coverage_args],
                log_path=Path(case["log"]),
                timeout=timeout,
                session_dir=Path(step.log["dir"]),
                cwd=program.parent,
            )
            returncode, output, timed_out = result.returncode, result.output, result.timed_out
        except OSError as error:
            returncode, output, timed_out = 127, f"%Error: failed to run {program}: {error}\n", False
        failure = judge_run(
            returncode,
            output,
            timed_out=timed_out,
            pass_pattern=settings["pass_pattern"],
            fail_pattern=settings["fail_pattern"],
        )
        # Say how the run was judged where the run output and `log` show it.
        if failure is not None:
            verdict = f"simulation failed: {failure['message']}"
        elif settings["pass_pattern"] is None and settings["fail_pattern"] is None:
            verdict = (
                "simulation passed on its exit status alone; set testbench.pass_pattern "
                "or testbench.fail_pattern to judge what the testbench prints"
            )
        else:
            verdict = "simulation passed"
        notes = [verdict]
        if settings["parameters"]:
            notes.append(
                "design.parameters applied through the testbench: "
                + " ".join(settings["parameters"])
            )
        if settings["parameters_not_applied"]:
            notes.append(
                "design.parameters not applied to the simulation: "
                + " ".join(settings["parameters_not_applied"])
                + f"; module {settings['top']} declares no parameter of that name to pass on"
            )
        Path(case["log"]).write_text(
            output.rstrip("\n") + "".join(f"\necc-fe: {note}" for note in notes) + "\n",
            encoding="utf-8",
        )
        case["judged_by"] = [
            "exit_status",
            *(name for name in ("pass_pattern", "fail_pattern") if settings[name] is not None),
        ]
        case.update(ok=failure is None, returncode=returncode, failure=failure)
        update_substep_ok(
            step,
            SimSubFlowEnum.simulate.value,
            failure is None,
            info={"returncode": returncode, **({"error": failure["message"]} if failure else {})},
        )


    def _coverage(
        self,
        step: WorkspaceStep,
        workspace: dict[str, Any],
        settings: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not settings["coverage"]:
            return None
        output_dir = Path(step.output["dir"])
        data = _coverage_data(output_dir / f"{settings['top']}_sim")
        ownership = rtl_ownership_map(workspace)
        report_dir = Path(step.report["dir"])
        return coverage.brief(
            coverage.write_report(
                [data],
                report_dir=report_dir,
                merged_path=output_dir / coverage.MERGED_NAME,
                ownership_of=lambda source: rtl_source_ownership(workspace, source, ownership),
                # The testbench is not the design whose coverage is asked for.
                exclude=settings["sources"],
                cases=1,
            ),
            report_dir,
        )


def _coverage_data(program: Path) -> Path:
    return program.with_name(program.name + ".coverage.dat")


def _first_error(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("%Error"):
            return line.strip()[:_EVIDENCE_LIMIT]
    return ""


__all__ = [
    "REPORT_KIND",
    "TestbenchSettingsError",
    "TestbenchSimStep",
    "judge_run",
    "split_parameter_overrides",
    "testbench_parameters",
    "testbench_settings",
]
